/*
 * CoAP endpoint for the smart shelf node - client and server on one socket.
 *
 * The gateway address is not configured anywhere: requests start out as
 * realm-local multicast (ff03::1, reaches every node in the Thread mesh) and
 * as soon as the gateway answers - or pushes anything to us - its unicast
 * address is latched and used from then on. If the gateway stops answering,
 * the node falls back to multicast again.
 *
 * Every ACTIVE shelf node hears that multicast too, so two rules keep a peer
 * from being mistaken for the gateway: a node never answers a request it does
 * not serve (no 4.04), and a discovery exchange only completes on a 2.xx
 * response - anything else is ignored while the real answer is awaited.
 *
 * All reception happens on one RX thread:
 *   - responses are matched to the (single) waiting requester by token,
 *   - incoming requests (`PUT <base>/led`, `PUT <base>/mode`) are handled in
 *     place and answered with a piggybacked ACK.
 * In IDLE mode the Thread parent queues those pushes and delivers them on the
 * next SED data poll, so a push reaches a sleeping node within one poll period.
 */

#include "shelf_coap.h"

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/drivers/hwinfo.h>
#include <zephyr/net/socket.h>
#include <zephyr/net/coap.h>
#include <zephyr/spinlock.h>

#include <errno.h>
#include <stdio.h>
#include <string.h>

LOG_MODULE_REGISTER(shelf_coap, CONFIG_SHELF_LOG_LEVEL);

#define COAP_PORT          5683
/* Thread realm-local all-nodes: forwarded across the whole mesh. */
#define GW_MCAST_ADDR      "ff03::1"
#define MCAST_HOP_LIMIT    8

#define TX_BUF_SIZE        192
#define RX_BUF_SIZE        192
#define TOKEN_LEN          8

/* Consecutive unicast failures before falling back to multicast discovery. */
#define GW_LOST_THRESHOLD  3

static int sock = -1;
static K_SEM_DEFINE(sock_ready, 0, 1);
/* Serialises requesters: one outstanding exchange at a time. */
static K_MUTEX_DEFINE(req_lock);

static struct sockaddr_in6 gw_addr;
static bool gw_known;
static uint8_t gw_miss;

static void (*led_push_cb)(bool on);

static struct k_spinlock state_lock;
static int64_t last_contact;

/* The one in-flight request the RX thread may complete. */
static struct {
	bool busy;
	/* Sent to the discovery multicast: only a 2.xx may complete it. */
	bool discovery;
	uint8_t token[TOKEN_LEN];
	uint8_t buf[RX_BUF_SIZE];
	int len;
	struct sockaddr_in6 from;
} pending;
static K_SEM_DEFINE(pending_done, 0, 1);

static char dev_id[2 * 8 + 1];

const char *shelf_device_id(void)
{
	return dev_id;
}

void shelf_coap_set_led_push_cb(void (*cb)(bool on))
{
	led_push_cb = cb;
}

int64_t shelf_coap_last_contact(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);
	int64_t v = last_contact;

	k_spin_unlock(&state_lock, key);
	return v;
}

static void note_contact(void)
{
	k_spinlock_key_t key = k_spin_lock(&state_lock);

	last_contact = k_uptime_get();
	k_spin_unlock(&state_lock, key);
}

static void latch_gateway(const struct sockaddr_in6 *from)
{
	if (!gw_known) {
		char addr[NET_IPV6_ADDR_LEN];

		memcpy(&gw_addr, from, sizeof(gw_addr));
		gw_addr.sin6_port = htons(COAP_PORT);
		gw_known = true;
		LOG_INF("gateway found at %s",
			zsock_inet_ntop(AF_INET6, &gw_addr.sin6_addr, addr,
					sizeof(addr)));
	}
	gw_miss = 0;
}

static void init_device_id(void)
{
	uint8_t id[8] = { 0 };
	ssize_t len = hwinfo_get_device_id(id, sizeof(id));

	if (len <= 0) {
		len = 0;
	}

	for (ssize_t i = 0; i < len; i++) {
		(void)snprintf(&dev_id[i * 2], 3, "%02x", id[i]);
	}
	dev_id[len * 2] = '\0';

	if (dev_id[0] == '\0') {
		strcpy(dev_id, "unknown");
	}
}

int shelf_coap_init(void)
{
	struct sockaddr_in6 local = {
		.sin6_family = AF_INET6,
		.sin6_port = htons(COAP_PORT),
		.sin6_addr = in6addr_any,
	};
	int hops = MCAST_HOP_LIMIT;
	int ret;

	init_device_id();

	sock = zsock_socket(AF_INET6, SOCK_DGRAM, IPPROTO_UDP);
	if (sock < 0) {
		LOG_ERR("socket() failed (%d)", errno);
		return -errno;
	}

	ret = zsock_bind(sock, (struct sockaddr *)&local, sizeof(local));
	if (ret < 0) {
		LOG_ERR("bind() failed (%d)", errno);
		zsock_close(sock);
		sock = -1;
		return -errno;
	}

	/* Realm-local multicast has to survive more than one mesh hop. */
	(void)zsock_setsockopt(sock, IPPROTO_IPV6, IPV6_MULTICAST_HOPS,
			       &hops, sizeof(hops));

	LOG_INF("CoAP ready on :%d, node id %s", COAP_PORT, dev_id);
	k_sem_give(&sock_ready);
	return 0;
}

static void gw_target(struct sockaddr_in6 *dst)
{
	memset(dst, 0, sizeof(*dst));
	dst->sin6_family = AF_INET6;
	dst->sin6_port = htons(COAP_PORT);

	if (gw_known) {
		memcpy(&dst->sin6_addr, &gw_addr.sin6_addr, sizeof(dst->sin6_addr));
	} else {
		(void)zsock_inet_pton(AF_INET6, GW_MCAST_ADDR, &dst->sin6_addr);
	}
}

/* @p token must already be filled in by the caller (see do_exchange: it is
 * registered with the RX thread before the datagram leaves, so a fast answer
 * cannot arrive before anyone is listening for it). @p dst comes from
 * gw_target(). */
static int send_request(uint8_t type, uint8_t method, const char *resource,
			const char *query, const uint8_t *payload, size_t payload_len,
			const uint8_t token[TOKEN_LEN],
			const struct sockaddr_in6 *dst)
{
	uint8_t buf[TX_BUF_SIZE];
	struct coap_packet req;
	int ret;

	ret = coap_packet_init(&req, buf, sizeof(buf), COAP_VERSION_1, type,
			       TOKEN_LEN, token, method, coap_next_id());
	if (ret < 0) {
		return ret;
	}

	/* Option numbers must be appended in ascending order: PATH(11) < QUERY(15). */
	ret = coap_packet_append_option(&req, COAP_OPTION_URI_PATH,
					CONFIG_SHELF_COAP_BASE_PATH,
					strlen(CONFIG_SHELF_COAP_BASE_PATH));
	if (ret < 0) {
		return ret;
	}

	ret = coap_packet_append_option(&req, COAP_OPTION_URI_PATH, resource,
					strlen(resource));
	if (ret < 0) {
		return ret;
	}

	if (query != NULL) {
		ret = coap_packet_append_option(&req, COAP_OPTION_URI_QUERY, query,
						strlen(query));
		if (ret < 0) {
			return ret;
		}
	}

	if (payload != NULL && payload_len > 0) {
		ret = coap_packet_append_payload_marker(&req);
		if (ret < 0) {
			return ret;
		}
		ret = coap_packet_append_payload(&req, payload, payload_len);
		if (ret < 0) {
			return ret;
		}
	}

	ret = zsock_sendto(sock, req.data, req.offset, 0,
			   (const struct sockaddr *)dst, sizeof(*dst));
	if (ret < 0) {
		return -errno;
	}

	return 0;
}

/* ------------------------------------------------------------ client side */

/*
 * Send one request and wait for the RX thread to hand us the answer.
 * On success the raw response is in @p rsp/@p rsp_len (caller parses).
 */
static int do_exchange(uint8_t type, uint8_t method, const char *resource,
		       const char *query, uint8_t *rsp, int *rsp_len,
		       struct sockaddr_in6 *from)
{
	uint8_t token[TOKEN_LEN];
	struct sockaddr_in6 dst;
	k_spinlock_key_t key;
	int ret;

	k_mutex_lock(&req_lock, K_FOREVER);

	memcpy(token, coap_next_token(), TOKEN_LEN);
	gw_target(&dst);

	k_sem_reset(&pending_done);
	key = k_spin_lock(&state_lock);
	pending.busy = true;
	pending.discovery = net_ipv6_is_addr_mcast(&dst.sin6_addr);
	memcpy(pending.token, token, TOKEN_LEN);
	k_spin_unlock(&state_lock, key);

	ret = send_request(type, method, resource, query, NULL, 0, token, &dst);
	if (ret == 0) {
		if (k_sem_take(&pending_done,
			       K_MSEC(CONFIG_SHELF_COAP_RESP_TIMEOUT_MS)) == 0) {
			memcpy(rsp, pending.buf, pending.len);
			*rsp_len = pending.len;
			if (from != NULL) {
				memcpy(from, &pending.from, sizeof(*from));
			}
			ret = 0;
		} else {
			ret = -EAGAIN;
		}
	}

	key = k_spin_lock(&state_lock);
	pending.busy = false;
	k_spin_unlock(&state_lock, key);

	if (ret == 0) {
		latch_gateway(from != NULL ? from : &pending.from);
		note_contact();
	} else if (gw_known && ++gw_miss >= GW_LOST_THRESHOLD) {
		LOG_WRN("gateway unresponsive, back to multicast discovery");
		gw_known = false;
		gw_miss = 0;
	}

	k_mutex_unlock(&req_lock);
	return ret;
}

/* Extract the payload of a 2.05 Content response, NUL terminated. */
static int content_payload(const uint8_t *rsp, int rsp_len, char *text,
			   size_t text_size)
{
	struct coap_packet pkt;
	const uint8_t *payload;
	uint16_t payload_len;
	int ret;

	ret = coap_packet_parse(&pkt, (uint8_t *)rsp, rsp_len, NULL, 0);
	if (ret < 0) {
		return ret;
	}

	if (coap_header_get_code(&pkt) != COAP_RESPONSE_CODE_CONTENT) {
		LOG_WRN("gateway answered %d.%02d",
			coap_header_get_code(&pkt) >> 5,
			coap_header_get_code(&pkt) & 0x1f);
		return -EAGAIN;
	}

	payload = coap_packet_get_payload(&pkt, &payload_len);
	if (payload == NULL || payload_len == 0) {
		return -EAGAIN;
	}

	payload_len = MIN(payload_len, text_size - 1);
	memcpy(text, payload, payload_len);
	text[payload_len] = '\0';
	return 0;
}

int shelf_coap_report_weight(int32_t raw, int32_t milligram, uint32_t seq,
			     int32_t batt_mv)
{
	char payload[TX_BUF_SIZE - 64];
	uint8_t token[TOKEN_LEN];
	struct sockaddr_in6 dst;
	int len;
	int ret;

	if (sock < 0) {
		return -ENOTCONN;
	}

	len = snprintk(payload, sizeof(payload),
		       "{\"id\":\"%s\",\"seq\":%u,\"raw\":%d,\"mg\":%d,\"mv\":%d,"
		       "\"md\":\"%c\"}",
		       dev_id, seq, raw, milligram, batt_mv, shelf_mode_char());
	if (len < 0 || len >= (int)sizeof(payload)) {
		return -ENOMEM;
	}

	k_mutex_lock(&req_lock, K_FOREVER);
	memcpy(token, coap_next_token(), TOKEN_LEN);
	gw_target(&dst);
	ret = send_request(COAP_TYPE_NON_CON, COAP_METHOD_POST, "weight", NULL,
			   (const uint8_t *)payload, len, token, &dst);
	k_mutex_unlock(&req_lock);

	if (ret) {
		LOG_WRN("weight report failed (%d)", ret);
	} else {
		LOG_INF("report seq=%u raw=%d mg=%d mv=%d", seq, raw, milligram,
			batt_mv);
	}

	return ret;
}

/*
 * Accept "0"/"1", "on"/"off", {"led":1}, {"led":true} and friends.
 */
static bool parse_led_payload(const uint8_t *p, uint16_t len, bool *led_on)
{
	char text[33];
	size_t n = MIN(len, sizeof(text) - 1);
	const char *cursor;

	if (p == NULL || len == 0) {
		return false;
	}

	memcpy(text, p, n);
	text[n] = '\0';

	/* If the payload names the field, only look after it. */
	cursor = strstr(text, "led");
	if (cursor != NULL) {
		cursor += 3;
	} else {
		cursor = text;
	}

	for (; *cursor != '\0'; cursor++) {
		switch (*cursor) {
		case '0':
			*led_on = false;
			return true;
		case '1':
			*led_on = true;
			return true;
		case 't':
		case 'T':
			*led_on = true;   /* true */
			return true;
		case 'f':
		case 'F':
			*led_on = false;  /* false */
			return true;
		case 'o':
		case 'O':
			/* "on" vs "off" */
			*led_on = (cursor[1] != 'f' && cursor[1] != 'F');
			return true;
		default:
			break;
		}
	}

	return false;
}

int shelf_coap_poll_led(bool *led_on)
{
	char query[16 + sizeof(dev_id)];
	uint8_t rsp[RX_BUF_SIZE];
	char text[33];
	int rsp_len = 0;
	int ret;

	if (sock < 0) {
		return -ENOTCONN;
	}

	(void)snprintk(query, sizeof(query), "id=%s", dev_id);

	ret = do_exchange(COAP_TYPE_CON, COAP_METHOD_GET, "led", query,
			  rsp, &rsp_len, NULL);
	if (ret) {
		return ret;
	}

	ret = content_payload(rsp, rsp_len, text, sizeof(text));
	if (ret) {
		return ret;
	}

	if (!parse_led_payload((const uint8_t *)text, strlen(text), led_on)) {
		LOG_WRN("unparsable LED payload");
		return -EAGAIN;
	}

	return 0;
}

int shelf_coap_get_mode(enum shelf_mode *mode)
{
	char query[16 + sizeof(dev_id)];
	uint8_t rsp[RX_BUF_SIZE];
	char text[33];
	int rsp_len = 0;
	int ret;

	if (sock < 0) {
		return -ENOTCONN;
	}

	(void)snprintk(query, sizeof(query), "id=%s", dev_id);

	ret = do_exchange(COAP_TYPE_CON, COAP_METHOD_GET, "mode", query,
			  rsp, &rsp_len, NULL);
	if (ret) {
		return ret;
	}

	ret = content_payload(rsp, rsp_len, text, sizeof(text));
	if (ret) {
		return ret;
	}

	if (strstr(text, "active") != NULL) {
		*mode = SHELF_MODE_ACTIVE;
	} else if (strstr(text, "idle") != NULL) {
		*mode = SHELF_MODE_IDLE;
	} else {
		LOG_WRN("unparsable mode payload '%s'", text);
		return -EAGAIN;
	}

	return 0;
}

/* ------------------------------------------------------------ server side */

static void send_ack(const struct coap_packet *req, uint8_t code,
		     const struct sockaddr_in6 *to)
{
	uint8_t buf[64];
	uint8_t token[COAP_TOKEN_MAX_LEN];
	uint8_t tkl = coap_header_get_token(req, token);
	struct coap_packet rsp;
	int ret;

	ret = coap_packet_init(&rsp, buf, sizeof(buf), COAP_VERSION_1,
			       COAP_TYPE_ACK, tkl, token, code,
			       coap_header_get_id(req));
	if (ret < 0) {
		return;
	}

	(void)zsock_sendto(sock, rsp.data, rsp.offset, 0,
			   (const struct sockaddr *)to, sizeof(*to));
}

/*
 * Handle a request pushed by the gateway. Only two resources exist:
 *   PUT <base>/led  "1"/"0"          -> drive the LED (ACTIVE mode push path)
 *   PUT <base>/mode "active"/"idle"  -> switch the power mode
 *
 * Anything else is dropped without an answer: it is another node's multicast
 * gateway discovery (GET <base>/mode, GET <base>/led, POST <base>/weight), and
 * a 4.04 from us would be taken for the gateway's reply.
 */
static void handle_request(struct coap_packet *req,
			   const struct sockaddr_in6 *from)
{
	struct coap_option path[4];
	const uint8_t *payload;
	uint16_t payload_len;
	uint8_t code = coap_header_get_code(req);
	uint8_t type = coap_header_get_type(req);
	bool confirmable = (type == COAP_TYPE_CON);
	int n;

	n = coap_find_options(req, COAP_OPTION_URI_PATH, path, ARRAY_SIZE(path));
	if (n != 2 ||
	    path[0].len != strlen(CONFIG_SHELF_COAP_BASE_PATH) ||
	    memcmp(path[0].value, CONFIG_SHELF_COAP_BASE_PATH, path[0].len) != 0) {
		return;
	}

	payload = coap_packet_get_payload(req, &payload_len);

	if (code == COAP_METHOD_PUT &&
	    path[1].len == 3 && memcmp(path[1].value, "led", 3) == 0) {
		bool on;

		if (!parse_led_payload(payload, payload_len, &on)) {
			if (confirmable) {
				send_ack(req, COAP_RESPONSE_CODE_BAD_REQUEST, from);
			}
			return;
		}

		LOG_INF("LED push: %s", on ? "on" : "off");
		if (led_push_cb != NULL) {
			led_push_cb(on);
		}
	} else if (code == COAP_METHOD_PUT &&
		   path[1].len == 4 && memcmp(path[1].value, "mode", 4) == 0) {
		char text[16] = { 0 };

		memcpy(text, payload, MIN(payload_len, sizeof(text) - 1));

		if (strstr(text, "active") != NULL) {
			shelf_mode_request(SHELF_MODE_ACTIVE,
					   SHELF_MODE_ORIGIN_GATEWAY);
		} else if (strstr(text, "idle") != NULL) {
			shelf_mode_request(SHELF_MODE_IDLE,
					   SHELF_MODE_ORIGIN_GATEWAY);
		} else {
			if (confirmable) {
				send_ack(req, COAP_RESPONSE_CODE_BAD_REQUEST, from);
			}
			return;
		}
	} else {
		return;
	}

	/* A push from the gateway is as good as an answered poll. */
	latch_gateway(from);
	note_contact();

	if (confirmable) {
		send_ack(req, COAP_RESPONSE_CODE_CHANGED, from);
	}
}

/* A CoAP code < 1.00 with a nonzero detail is a request method. */
static bool is_request(uint8_t code)
{
	return code >= COAP_METHOD_GET && code <= COAP_METHOD_DELETE;
}

static void rx_thread_fn(void *p1, void *p2, void *p3)
{
	static uint8_t buf[RX_BUF_SIZE];

	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	k_sem_take(&sock_ready, K_FOREVER);

	while (true) {
		struct sockaddr_in6 from;
		socklen_t from_len = sizeof(from);
		struct coap_packet pkt;
		uint8_t token[COAP_TOKEN_MAX_LEN];
		k_spinlock_key_t key;
		bool matched = false;
		int received;

		received = zsock_recvfrom(sock, buf, sizeof(buf), 0,
					  (struct sockaddr *)&from, &from_len);
		if (received < 0) {
			LOG_ERR("recvfrom failed (%d)", errno);
			k_sleep(K_MSEC(100));
			continue;
		}

		if (coap_packet_parse(&pkt, buf, received, NULL, 0) < 0) {
			continue;
		}

		if (is_request(coap_header_get_code(&pkt))) {
			handle_request(&pkt, &from);
			continue;
		}

		/* Response (or empty ACK/RST): hand it to the waiting
		 * requester when the token matches. */
		if (coap_header_get_token(&pkt, token) != TOKEN_LEN) {
			continue;
		}

		/* During discovery every listening node may answer; only a
		 * success can be the gateway. Skipping the rest (e.g. a 4.04
		 * from a peer on older firmware) keeps the exchange open for
		 * the gateway's own reply. */
		key = k_spin_lock(&state_lock);
		if (pending.busy &&
		    memcmp(token, pending.token, TOKEN_LEN) == 0 &&
		    (!pending.discovery ||
		     (coap_header_get_code(&pkt) >> 5) == 2)) {
			memcpy(pending.buf, buf, received);
			pending.len = received;
			memcpy(&pending.from, &from, sizeof(from));
			pending.busy = false;
			matched = true;
		}
		k_spin_unlock(&state_lock, key);

		if (matched) {
			k_sem_give(&pending_done);
		}
	}
}

K_THREAD_DEFINE(shelf_coap_rx_tid, 2048, rx_thread_fn, NULL, NULL, NULL,
		K_PRIO_PREEMPT(5), 0, 0);

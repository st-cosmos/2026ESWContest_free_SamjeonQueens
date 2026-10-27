/*
 * Smart shelf node.
 *
 *   E73-2G4M08S1C (nRF52840) + CS1237 load cell ADC, Thread / CoAP uplink.
 *
 * Two power modes (docs/power-modes.md), commanded by the gateway:
 *
 *   IDLE   : Thread SED (radio polls every CONFIG_SHELF_IDLE_POLL_PERIOD_MS),
 *            gated 40 Hz measurements every CONFIG_SHELF_IDLE_MEASURE_INTERVAL_MS,
 *            LED forced off. This is the boot default.
 *   ACTIVE : Thread MED (radio always on), load cell rail kept powered and
 *            sampled at 640 Hz every CONFIG_SHELF_ACTIVE_MEASURE_INTERVAL_MS,
 *            LED driven by gateway pushes with a slow poll as backup.
 *
 *   measure thread : samples the load cell and reports to the gateway whenever
 *                    the reading moved by more than the configured threshold,
 *                    or the mode's heartbeat interval expired.
 *   led thread     : boot mode sync, ACTIVE LED sync poll + gateway failsafe,
 *                    IDLE LED-off enforcement.
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/logging/log.h>
#include <zephyr/net/net_event.h>
#include <zephyr/net/net_mgmt.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>

#if defined(CONFIG_USB_DEVICE_STACK)
#include <zephyr/usb/usb_device.h>
#endif

#include "cs1237.h"
#include "shelf_cal.h"
#include "shelf_coap.h"
#include "shelf_mode.h"

LOG_MODULE_REGISTER(shelf, CONFIG_SHELF_LOG_LEVEL);

#define SHELF_USER_NODE DT_PATH(zephyr_user)

BUILD_ASSERT(CONFIG_SHELF_CAL_COUNTS_PER_KG != 0,
	     "SHELF_CAL_COUNTS_PER_KG must not be zero");

static const struct gpio_dt_spec led =
	GPIO_DT_SPEC_GET(DT_ALIAS(shelf_led), gpios);

/* .config is the IDLE profile; ACTIVE swaps the speed bits (see below). */
static const struct cs1237 loadcell = {
	.sck  = GPIO_DT_SPEC_GET(SHELF_USER_NODE, cs1237_sck_gpios),
	.dout = GPIO_DT_SPEC_GET(SHELF_USER_NODE, cs1237_dout_gpios),
	.pwr  = GPIO_DT_SPEC_GET(SHELF_USER_NODE, loadcell_power_gpios),
	.config = CS1237_SPEED_40 | CS1237_PGA_128 | CS1237_CH_A,
};

/* ACTIVE keeps the rail powered, so the slow-settling 40 Hz filter is not
 * needed - 640 Hz makes a full average cost ~15 ms instead of ~200 ms. */
#define CS_CFG_ACTIVE (CS1237_SPEED_640 | CS1237_PGA_128 | CS1237_CH_A)

static K_SEM_DEFINE(net_ready, 0, 1);
static struct net_mgmt_event_callback l4_cb;

/* Upper bound on how long a worker waits for the first L4_CONNECTED event.
 * After that it starts anyway - CoAP simply fails until the mesh is up, which
 * is preferable to a node that stays silent forever if the event is missed. */
#define NET_READY_GRACE K_SECONDS(120)

static void wait_for_network(const char *who)
{
	if (k_sem_take(&net_ready, NET_READY_GRACE) != 0) {
		LOG_WRN("%s: no Thread connectivity yet, starting anyway", who);
	} else {
		/* L4_CONNECTED gives the semaphore once, but the measure and LED
		 * threads both wait on it - hand the token back so the second
		 * waiter is released too instead of eating the full grace time. */
		k_sem_give(&net_ready);
	}
}

/* ------------------------------------------------------------------ network */

static void l4_event_handler(struct net_mgmt_event_callback *cb, uint64_t event,
			     struct net_if *iface)
{
	ARG_UNUSED(cb);
	ARG_UNUSED(iface);

	switch (event) {
	case NET_EVENT_L4_CONNECTED:
		LOG_INF("Thread network connected");
		k_sem_give(&net_ready);
		break;
	case NET_EVENT_L4_DISCONNECTED:
		LOG_WRN("Thread network disconnected");
		break;
	default:
		break;
	}
}

/* ------------------------------------------------------------------ weight */

static uint32_t abs_delta(int32_t a, int32_t b)
{
	return (uint32_t)((a > b) ? (a - b) : (b - a));
}

/* SHELF_SETTLE_THRESHOLD_G converted to raw counts with the active
 * calibration. counts_per_kg is negative on reverse-wired load cells. */
static uint32_t settle_threshold_counts(void)
{
	int64_t cpkg = shelf_cal_counts_per_kg();

	if (cpkg < 0) {
		cpkg = -cpkg;
	}
	return (uint32_t)((cpkg * CONFIG_SHELF_SETTLE_THRESHOLD_G) / 1000);
}

/* The measurement thread and the `shelf` shell commands both drive the rail
 * and the bit-banged bus, so everything is serialised by this mutex. It also
 * guards rail_held: in ACTIVE mode the measurement thread keeps the rail
 * powered between samples, and a shell `shelf raw` must ride that rail
 * instead of power-cycling it away underneath the thread. */
static K_MUTEX_DEFINE(loadcell_lock);
static bool rail_held;

int shelf_read_raw(int32_t *raw)
{
	int ret;

	k_mutex_lock(&loadcell_lock, K_FOREVER);

	if (rail_held) {
		/* Rail already on and converting at 640 Hz. */
		ret = cs1237_read_avg(&loadcell, 1,
				      CONFIG_SHELF_ACTIVE_AVG_SAMPLES, raw);
	} else {
		ret = cs1237_power_up(&loadcell);
		if (ret == 0) {
			ret = cs1237_read_avg(&loadcell,
					      CONFIG_SHELF_ADC_DISCARD_SAMPLES,
					      CONFIG_SHELF_ADC_AVG_SAMPLES, raw);
		}
		cs1237_power_down(&loadcell);
	}

	k_mutex_unlock(&loadcell_lock);
	return ret;
}

/* Rail policy on a mode transition. Failure to power up is not fatal - the
 * next measurement attempt retries. */
static void rail_enter_mode(enum shelf_mode mode)
{
	k_mutex_lock(&loadcell_lock, K_FOREVER);

	if (mode == SHELF_MODE_ACTIVE && !rail_held) {
		if (cs1237_power_up_cfg(&loadcell, CS_CFG_ACTIVE) == 0) {
			rail_held = true;
		} else {
			cs1237_power_down(&loadcell);
		}
	} else if (mode == SHELF_MODE_IDLE && rail_held) {
		cs1237_power_down(&loadcell);
		rail_held = false;
	}

	k_mutex_unlock(&loadcell_lock);
}

static void measure_thread_fn(void *p1, void *p2, void *p3)
{
	enum shelf_mode cur = SHELF_MODE_IDLE;
	int32_t last_reported = 0;
	int32_t last_meas = 0;
	int64_t last_report_ms = 0;
	bool have_last = false;
	bool have_meas = false;
	uint32_t seq = 0;
	int64_t next;

	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	wait_for_network("measure");
	next = k_uptime_get();

	while (true) {
		enum shelf_mode m = shelf_mode_get();
		uint32_t interval = (m == SHELF_MODE_ACTIVE)
			? CONFIG_SHELF_ACTIVE_MEASURE_INTERVAL_MS
			: CONFIG_SHELF_IDLE_MEASURE_INTERVAL_MS;
		uint32_t heartbeat_s = (m == SHELF_MODE_ACTIVE)
			? CONFIG_SHELF_HEARTBEAT_ACTIVE_S
			: CONFIG_SHELF_HEARTBEAT_IDLE_S;
		int32_t raw = 0;
		int ret;

		if (m != cur) {
			rail_enter_mode(m);
			cur = m;
		} else if (m == SHELF_MODE_ACTIVE && !rail_held) {
			rail_enter_mode(m);	/* retry a failed power-up */
		}

		next += interval;

		ret = shelf_read_raw(&raw);

		if (ret) {
			LOG_ERR("load cell measurement failed (%d)", ret);
		} else {
			int64_t now = k_uptime_get();
			bool changed = !have_last ||
				abs_delta(raw, last_reported) >=
					(uint32_t)CONFIG_SHELF_ADC_DELTA_THRESHOLD;
			/* Settling filter: while something is being placed the
			 * reading moves between cycles (0 -> 300 g -> 500 g).
			 * Only report once two consecutive measurements agree,
			 * so the server sees a single settled change instead of
			 * every mid-placement transient. */
			bool settled = have_meas &&
				abs_delta(raw, last_meas) <= settle_threshold_counts();
			bool heartbeat = (heartbeat_s > 0) &&
				(now - last_report_ms >= (int64_t)heartbeat_s * 1000);

			last_meas = raw;
			have_meas = true;

			LOG_DBG("raw=%d mg=%d%s%s", raw, shelf_raw_to_mg(raw),
				changed ? " (changed)" : "",
				settled ? "" : " (settling)");

			if ((changed && settled) || heartbeat) {
				int32_t batt_mv = -1;

				(void)shelf_battery_read_mv(&batt_mv);

				if (shelf_coap_report_weight(raw, shelf_raw_to_mg(raw),
							     ++seq, batt_mv) == 0) {
					last_reported = raw;
					last_report_ms = now;
					have_last = true;
				}
			}
		}

		/* Sleep until the next slot - or instantly re-evaluate when
		 * shelf_mode_request() gives the semaphore on a mode change. */
		if (k_sem_take(&shelf_mode_measure_wake,
			       K_TIMEOUT_ABS_MS(next)) == 0) {
			next = k_uptime_get();
		}
	}
}

/* --------------------------------------------------------------------- LED */

static atomic_t led_commanded;

static void led_apply(bool on)
{
	atomic_set(&led_commanded, on ? 1 : 0);
	gpio_pin_set_dt(&led, on);
}

/* Runs in the CoAP RX thread when the gateway pushes PUT <base>/led. */
static void led_push_handler(bool on)
{
	if (shelf_mode_get() != SHELF_MODE_ACTIVE && on) {
		/* IDLE means nobody is looking at the shelf - don't let a
		 * racing push leave the LED burning for nobody. */
		LOG_WRN("LED-on push ignored in idle mode");
		return;
	}
	led_apply(on);
}

/*
 * One-time boot synchronisation: ask the gateway which mode we should be in.
 * Ends as soon as an answer arrives, any mode push beats us to it, or the
 * shell forces a mode (bench work without a gateway - the LED thread must
 * not stay parked here or forced ACTIVE never runs the wake animation).
 * Backs off so an old gateway (no mode resource) only costs one request a
 * minute.
 */
static void boot_mode_sync(void)
{
	uint32_t delay_s = 2;

	while (!shelf_mode_is_synced() && !shelf_mode_is_forced()) {
		enum shelf_mode m;

		if (shelf_coap_get_mode(&m) == 0) {
			shelf_mode_request(m, SHELF_MODE_ORIGIN_GATEWAY);
			return;
		}

		for (uint32_t i = 0;
		     i < delay_s && !shelf_mode_is_synced() &&
		     !shelf_mode_is_forced(); i++) {
			k_sleep(K_SECONDS(1));
		}
		delay_s = MIN(delay_s * 2, 60);
	}
}

static void led_thread_fn(void *p1, void *p2, void *p3)
{
	enum shelf_mode prev = SHELF_MODE_IDLE;

	ARG_UNUSED(p1);
	ARG_UNUSED(p2);
	ARG_UNUSED(p3);

	wait_for_network("led");
	boot_mode_sync();

	while (true) {
		enum shelf_mode m = shelf_mode_get();

		if (m == SHELF_MODE_ACTIVE && prev != SHELF_MODE_ACTIVE) {
			/* Wake animation: one 0->255->0 breathe so the user sees
			 * the shelf come alive on login. Runs here (not in the
			 * CoAP RX thread that received the push) so the mode-push
			 * ACK is not delayed by the ~4 s sweep. A LED push that
			 * lands mid-sweep is re-asserted right after. */
			LOG_INF("wake animation");
			shelf_led_breathe(1);
			led_apply(atomic_get(&led_commanded));
		}
		prev = m;

		if (m != SHELF_MODE_ACTIVE) {
			if (atomic_get(&led_commanded)) {
				LOG_INF("idle mode, LED off");
			}
			led_apply(false);
			/* Nothing to do until the mode changes. */
			k_sem_take(&shelf_mode_led_wake, K_FOREVER);
			continue;
		}

		/* ACTIVE: pushes do the fast path; this poll repairs a lost
		 * push and doubles as the gateway liveness probe. */
		bool want = atomic_get(&led_commanded);

		if (shelf_coap_poll_led(&want) == 0) {
			if (want != (bool)atomic_get(&led_commanded)) {
				LOG_INF("LED %s (sync poll)", want ? "on" : "off");
				led_apply(want);
			}
		} else if (!shelf_mode_is_forced()) {
			int64_t contact = shelf_coap_last_contact();

			if (contact > 0 &&
			    k_uptime_get() - contact >
				    (int64_t)CONFIG_SHELF_ACTIVE_FAILSAFE_S * 1000) {
				LOG_WRN("no gateway for %d s, dropping to idle",
					CONFIG_SHELF_ACTIVE_FAILSAFE_S);
				shelf_mode_request(SHELF_MODE_IDLE,
						   SHELF_MODE_ORIGIN_FAILSAFE);
				continue;
			}
		}

		(void)k_sem_take(&shelf_mode_led_wake,
				 K_MSEC(CONFIG_SHELF_MODE_SYNC_INTERVAL_MS));
	}
}

/* ----------------------------------------------------------------- battery */

/* SAADC channel on the internal VDDH/5 tap (see the board overlay). VDDH is
 * +BAT_FIN, so this reads the battery when running from it - and VBUS minus
 * the D1 drop when USB is plugged in and Q5 has the battery disconnected. */
static const struct adc_dt_spec vbat_adc =
	ADC_DT_SPEC_GET_BY_IDX(SHELF_USER_NODE, 0);

int shelf_battery_read_mv(int32_t *mv)
{
	int16_t buf;
	struct adc_sequence seq = {
		.buffer = &buf,
		.buffer_size = sizeof(buf),
	};
	int32_t val;
	int ret;

	if (!adc_is_ready_dt(&vbat_adc)) {
		return -ENODEV;
	}

	ret = adc_channel_setup_dt(&vbat_adc);
	if (ret) {
		return ret;
	}

	ret = adc_sequence_init_dt(&vbat_adc, &seq);
	if (ret) {
		return ret;
	}

	ret = adc_read_dt(&vbat_adc, &seq);
	if (ret) {
		return ret;
	}

	val = buf;
	ret = adc_raw_to_millivolts_dt(&vbat_adc, &val);
	if (ret) {
		return ret;
	}

	*mv = val * 5;	/* the tap divides VDDH by 5 */
	return 0;
}

/* Software-PWM breathing test for the LED hardware (Q1 gate on P0.06). The
 * shell thread bit-bangs the duty cycle, so no PWM peripheral or devicetree
 * channel is needed. The LED thread or a push may write the pin once
 * mid-test; the next PWM period overwrites it, so a glitch is at most 2 ms. */
int shelf_led_breathe(unsigned int cycles)
{
	const uint32_t period_us = 2000;      /* 500 Hz, flicker-free */
	const uint32_t periods_per_step = 4;  /* ~8 ms per brightness step */

	for (unsigned int c = 0; c < cycles; c++) {
		for (int step = 0; step < 510; step++) {
			uint32_t b = (step < 255) ? step : 510 - step;
			/* b^2/255 approximates the eye's response, so the ramp
			 * looks linear instead of hanging near full brightness. */
			uint32_t on_us = (b * b * period_us) / (255U * 255U);

			for (uint32_t p = 0; p < periods_per_step; p++) {
				if (on_us > 0) {
					gpio_pin_set_dt(&led, 1);
					k_busy_wait(on_us);
				}
				if (on_us < period_us) {
					gpio_pin_set_dt(&led, 0);
					k_busy_wait(period_us - on_us);
				}
			}
		}
	}

	gpio_pin_set_dt(&led, 0);
	return 0;
}

/* -------------------------------------------------------------------- main */

K_THREAD_DEFINE(measure_tid, 3072, measure_thread_fn, NULL, NULL, NULL,
		K_PRIO_PREEMPT(7), 0, 0);
K_THREAD_DEFINE(led_tid, 3072, led_thread_fn, NULL, NULL, NULL,
		K_PRIO_PREEMPT(6), 0, 0);

int main(void)
{
	int ret;

#if defined(CONFIG_USB_DEVICE_STACK)
	/* Console and shell live on the USB-C port. */
	(void)usb_enable(NULL);
#endif

	LOG_INF("smart shelf node starting");

	if (!gpio_is_ready_dt(&led)) {
		LOG_ERR("LED GPIO not ready");
		return -ENODEV;
	}

	ret = gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
	if (ret) {
		LOG_ERR("LED configure failed (%d)", ret);
		return ret;
	}

	ret = cs1237_init(&loadcell);
	if (ret) {
		LOG_ERR("CS1237 GPIO init failed (%d)", ret);
		return ret;
	}

	/* Non-fatal: without stored values the Kconfig defaults stay in effect,
	 * which is still a working (if uncalibrated) node. */
	(void)shelf_cal_init();

	shelf_coap_set_led_push_cb(led_push_handler);

	ret = shelf_coap_init();
	if (ret) {
		LOG_ERR("CoAP init failed (%d)", ret);
		return ret;
	}

	shelf_mode_init();

	net_mgmt_init_event_callback(&l4_cb, l4_event_handler,
				     NET_EVENT_L4_CONNECTED | NET_EVENT_L4_DISCONNECTED);
	net_mgmt_add_event_callback(&l4_cb);

	LOG_INF("waiting for the Thread network");
	return 0;
}

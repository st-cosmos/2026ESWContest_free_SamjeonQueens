/*
 * CoAP endpoint used to talk to the shelf gateway over Thread.
 *
 * The node is both a client (weight reports, LED/mode queries) and a server:
 * the gateway pushes `PUT <base>/led` and `PUT <base>/mode` to us. A single
 * socket bound to :5683 carries everything; a dedicated RX thread demuxes
 * responses (matched to the waiting request by token) from incoming requests.
 */

#ifndef SHELF_COAP_H_
#define SHELF_COAP_H_

#include <stdbool.h>
#include <stdint.h>

#include "shelf_mode.h"

/**
 * Open the UDP socket (:5683) and start the RX thread. Safe to call before
 * the Thread network is up.
 */
int shelf_coap_init(void);

/**
 * NON-confirmable POST of one measurement to <base>/weight.
 *
 * Payload: {"id":"<eui64>","seq":N,"raw":N,"mg":N,"mv":N,"md":"a"|"i"}
 *
 * @param batt_mv VDDH millivolts (the battery when running from it), or a
 *                negative value when the reading failed - sent as-is so the
 *                gateway can tell "unknown" from a real voltage.
 */
int shelf_coap_report_weight(int32_t raw, int32_t milligram, uint32_t seq,
			     int32_t batt_mv);

/**
 * Confirmable GET of <base>/led?id=<eui64>, waits up to
 * CONFIG_SHELF_COAP_RESP_TIMEOUT_MS for the answer.
 *
 * ACTIVE mode uses this as a slow synchronisation fallback for lost pushes;
 * it is also what keeps an old-firmware node working against this gateway.
 *
 * @retval 0        @p led_on holds the commanded state
 * @retval -EAGAIN  no usable answer within the timeout
 */
int shelf_coap_poll_led(bool *led_on);

/**
 * Confirmable GET of <base>/mode?id=<eui64> - the boot time mode sync.
 *
 * @retval 0        @p mode holds the gateway's current answer
 * @retval -EAGAIN  no usable answer within the timeout
 */
int shelf_coap_get_mode(enum shelf_mode *mode);

/**
 * Register the handler run when the gateway pushes `PUT <base>/led`.
 * Called from the CoAP RX thread.
 */
void shelf_coap_set_led_push_cb(void (*cb)(bool on));

/**
 * Uptime [ms] of the last successful gateway exchange (answered request or
 * valid incoming push), 0 when there has been none yet. The LED thread's
 * ACTIVE failsafe runs on this.
 */
int64_t shelf_coap_last_contact(void);

/**
 * Human readable node id (EUI64 style hex string) used in the payloads.
 */
const char *shelf_device_id(void);

#endif /* SHELF_COAP_H_ */

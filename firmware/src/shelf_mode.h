/*
 * Power mode state machine (docs/power-modes.md).
 *
 *   IDLE   - Thread SED (rx off, data poll), gated 40 Hz measurements, LED off.
 *   ACTIVE - Thread MED (rx always on), rail powered, 640 Hz measurements,
 *            LED driven by gateway pushes.
 *
 * The mode is normally commanded by the gateway (PUT shelf/mode, or the boot
 * time GET shelf/mode answer). The shell can force a mode for bench work, and
 * the LED thread demotes to IDLE when the gateway disappears for too long.
 */

#ifndef SHELF_MODE_H_
#define SHELF_MODE_H_

#include <stdbool.h>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>

enum shelf_mode {
	SHELF_MODE_IDLE = 0,
	SHELF_MODE_ACTIVE = 1,
};

enum shelf_mode_origin {
	SHELF_MODE_ORIGIN_BOOT,     /* initial application of the default   */
	SHELF_MODE_ORIGIN_GATEWAY,  /* PUT shelf/mode push or GET answer    */
	SHELF_MODE_ORIGIN_FAILSAFE, /* gateway silent too long -> IDLE      */
	SHELF_MODE_ORIGIN_SHELL,    /* `shelf mode idle|active` (sticky)    */
};

/* Given by shelf_mode_request() on every change so the measurement and LED
 * threads re-evaluate their period immediately instead of finishing a full
 * sleep in the old mode. */
extern struct k_sem shelf_mode_measure_wake;
extern struct k_sem shelf_mode_led_wake;

/* Apply the IDLE default to the Thread stack. Call once at boot. */
void shelf_mode_init(void);

enum shelf_mode shelf_mode_get(void);

/* "a" / "i" - the weight report `md` field. */
char shelf_mode_char(void);

/*
 * Request a mode. Gateway/failsafe requests are ignored while a shell force is
 * in effect. Returns true when the mode actually changed.
 */
bool shelf_mode_request(enum shelf_mode mode, enum shelf_mode_origin origin);

/* Drop a shell force; the mode stays until the gateway says otherwise. */
void shelf_mode_unforce(void);

bool shelf_mode_is_forced(void);

/*
 * True once the mode has been synchronised with the gateway at least once
 * (boot GET answered, or any push received). The LED thread uses this to end
 * its boot sync loop.
 */
bool shelf_mode_is_synced(void);

/* `shelf mode [idle|active|auto]` - registered from shelf_cal.c's table. */
int shelf_mode_shell_cmd(const struct shell *sh, size_t argc, char **argv);

#endif /* SHELF_MODE_H_ */

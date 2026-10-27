/*
 * Power mode state machine - see shelf_mode.h and docs/power-modes.md.
 *
 * The Thread side of a switch is one otThreadSetLinkMode() call: clearing
 * mRxOnWhenIdle turns the child into a SED (the parent starts queueing its
 * downlink frames), setting it makes the radio listen continuously. The child
 * stays attached either way - no re-join, just an MLE Child Update exchange.
 */

#include "shelf_mode.h"

#include <zephyr/logging/log.h>
#include <zephyr/spinlock.h>

#include <openthread.h>
#include <openthread/link.h>
#include <openthread/thread.h>

#include <string.h>

LOG_MODULE_REGISTER(shelf_mode, CONFIG_SHELF_LOG_LEVEL);

K_SEM_DEFINE(shelf_mode_measure_wake, 0, 1);
K_SEM_DEFINE(shelf_mode_led_wake, 0, 1);

static struct k_spinlock lock;
static enum shelf_mode cur_mode = SHELF_MODE_IDLE;
static bool forced;
static bool synced;

static const char *mode_name(enum shelf_mode m)
{
	return (m == SHELF_MODE_ACTIVE) ? "active" : "idle";
}

static const char *origin_name(enum shelf_mode_origin o)
{
	switch (o) {
	case SHELF_MODE_ORIGIN_BOOT:     return "boot";
	case SHELF_MODE_ORIGIN_GATEWAY:  return "gateway";
	case SHELF_MODE_ORIGIN_FAILSAFE: return "failsafe";
	case SHELF_MODE_ORIGIN_SHELL:    return "shell";
	}
	return "?";
}

static void apply_link_mode(enum shelf_mode m)
{
	struct otInstance *ot = openthread_get_default_instance();
	otLinkModeConfig link;

	if (ot == NULL) {
		LOG_ERR("no OpenThread instance, link mode not applied");
		return;
	}

	openthread_mutex_lock();

	link = otThreadGetLinkMode(ot);
	link.mRxOnWhenIdle = (m == SHELF_MODE_ACTIVE);
	(void)otThreadSetLinkMode(ot, link);

	if (m == SHELF_MODE_IDLE) {
		(void)otLinkSetPollPeriod(ot, CONFIG_SHELF_IDLE_POLL_PERIOD_MS);
	}

	openthread_mutex_unlock();
}

void shelf_mode_init(void)
{
	/* prj.conf already boots the stack as a SED with the idle poll period;
	 * applying it again here just makes the state explicit. */
	apply_link_mode(SHELF_MODE_IDLE);
	LOG_INF("power mode: idle (boot default)");
}

enum shelf_mode shelf_mode_get(void)
{
	return cur_mode;
}

char shelf_mode_char(void)
{
	return (cur_mode == SHELF_MODE_ACTIVE) ? 'a' : 'i';
}

bool shelf_mode_is_forced(void)
{
	return forced;
}

bool shelf_mode_is_synced(void)
{
	return synced;
}

bool shelf_mode_request(enum shelf_mode mode, enum shelf_mode_origin origin)
{
	bool changed = false;
	k_spinlock_key_t key = k_spin_lock(&lock);

	if (origin == SHELF_MODE_ORIGIN_GATEWAY) {
		synced = true;
	}

	if (forced && origin != SHELF_MODE_ORIGIN_SHELL) {
		k_spin_unlock(&lock, key);
		LOG_WRN("mode %s from %s ignored (shell force in effect)",
			mode_name(mode), origin_name(origin));
		return false;
	}

	if (origin == SHELF_MODE_ORIGIN_SHELL) {
		forced = true;
	}

	if (cur_mode != mode) {
		cur_mode = mode;
		changed = true;
	}

	k_spin_unlock(&lock, key);

	if (changed) {
		LOG_INF("power mode: %s (%s)", mode_name(mode), origin_name(origin));
		apply_link_mode(mode);
		k_sem_give(&shelf_mode_measure_wake);
		k_sem_give(&shelf_mode_led_wake);
	}

	return changed;
}

void shelf_mode_unforce(void)
{
	k_spinlock_key_t key = k_spin_lock(&lock);

	forced = false;
	k_spin_unlock(&lock, key);
	LOG_INF("mode force cleared, following the gateway again");
}

/* --------------------------------------------------------------------- shell */

int shelf_mode_shell_cmd(const struct shell *sh, size_t argc, char **argv)
{
	if (argc < 2) {
		shell_print(sh, "mode   %s%s", mode_name(cur_mode),
			    forced ? " (forced)" : "");
		shell_print(sh, "synced %s", synced ? "yes" : "no");
		return 0;
	}

	if (strcmp(argv[1], "idle") == 0) {
		shelf_mode_request(SHELF_MODE_IDLE, SHELF_MODE_ORIGIN_SHELL);
	} else if (strcmp(argv[1], "active") == 0) {
		shelf_mode_request(SHELF_MODE_ACTIVE, SHELF_MODE_ORIGIN_SHELL);
	} else if (strcmp(argv[1], "auto") == 0) {
		shelf_mode_unforce();
	} else {
		shell_error(sh, "expected: shelf mode [idle|active|auto]");
		return -EINVAL;
	}

	shell_print(sh, "mode %s%s", mode_name(cur_mode), forced ? " (forced)" : "");
	return 0;
}

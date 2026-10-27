/*
 * Persistent load cell calibration + the `shelf` shell commands used to set it.
 *
 * Storage is the Zephyr settings subsystem under "shelf/cal". CONFIG_SETTINGS,
 * CONFIG_SETTINGS_NVS and CONFIG_NVS are already pulled in by OpenThread (it
 * keeps its dataset there), so nothing extra has to be enabled for this.
 */

#include "shelf_cal.h"
#include "shelf_mode.h"

#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>
#include <zephyr/shell/shell.h>

#include <errno.h>
#include <stdlib.h>

LOG_MODULE_REGISTER(shelf_cal, CONFIG_SHELF_LOG_LEVEL);

#define CAL_SUBTREE   "shelf/cal"
#define CAL_KEY_OFF   CAL_SUBTREE "/offset"
#define CAL_KEY_CPKG  CAL_SUBTREE "/cpkg"

static int32_t cal_offset = CONFIG_SHELF_CAL_OFFSET;
static int32_t cal_cpkg   = CONFIG_SHELF_CAL_COUNTS_PER_KG;

/* ------------------------------------------------------------------ storage */

static int cal_settings_set(const char *name, size_t len,
			    settings_read_cb read_cb, void *cb_arg)
{
	const char *next;
	int32_t *target;

	if (settings_name_steq(name, "offset", &next) && !next) {
		target = &cal_offset;
	} else if (settings_name_steq(name, "cpkg", &next) && !next) {
		target = &cal_cpkg;
	} else {
		return -ENOENT;
	}

	if (len != sizeof(*target)) {
		return -EINVAL;
	}

	if (read_cb(cb_arg, target, sizeof(*target)) < 0) {
		return -EIO;
	}

	return 0;
}

SETTINGS_STATIC_HANDLER_DEFINE(shelf_cal, CAL_SUBTREE, NULL, cal_settings_set,
			       NULL, NULL);

int shelf_cal_init(void)
{
	int ret;

	ret = settings_subsys_init();
	if (ret) {
		LOG_ERR("settings init failed (%d), using built-in defaults", ret);
		return ret;
	}

	ret = settings_load_subtree(CAL_SUBTREE);
	if (ret) {
		LOG_ERR("calibration load failed (%d), using built-in defaults", ret);
		return ret;
	}

	if (cal_cpkg == 0) {
		LOG_ERR("stored counts_per_kg is zero, reverting to default");
		cal_cpkg = CONFIG_SHELF_CAL_COUNTS_PER_KG;
	}

	LOG_INF("calibration: offset=%d counts_per_kg=%d", cal_offset, cal_cpkg);
	return 0;
}

int32_t shelf_cal_offset(void)
{
	return cal_offset;
}

int32_t shelf_cal_counts_per_kg(void)
{
	return cal_cpkg;
}

int shelf_cal_set_offset(int32_t offset)
{
	int ret = settings_save_one(CAL_KEY_OFF, &offset, sizeof(offset));

	if (ret) {
		return ret;
	}

	cal_offset = offset;
	return 0;
}

int shelf_cal_set_counts_per_kg(int32_t counts_per_kg)
{
	int ret;

	if (counts_per_kg == 0) {
		return -EINVAL;
	}

	ret = settings_save_one(CAL_KEY_CPKG, &counts_per_kg,
				sizeof(counts_per_kg));
	if (ret) {
		return ret;
	}

	cal_cpkg = counts_per_kg;
	return 0;
}

int shelf_cal_reset(void)
{
	int first = settings_delete(CAL_KEY_OFF);
	int second = settings_delete(CAL_KEY_CPKG);

	cal_offset = CONFIG_SHELF_CAL_OFFSET;
	cal_cpkg = CONFIG_SHELF_CAL_COUNTS_PER_KG;

	return first ? first : second;
}

int32_t shelf_raw_to_mg(int32_t raw)
{
	int64_t counts = (int64_t)raw - (int64_t)cal_offset;

	return (int32_t)((counts * 1000000LL) / (int64_t)cal_cpkg);
}

/* -------------------------------------------------------------------- shell */

static int cmd_show(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	shell_print(sh, "offset        %d counts", cal_offset);
	shell_print(sh, "counts_per_kg %d", cal_cpkg);
	shell_print(sh, "defaults      offset=%d counts_per_kg=%d",
		    CONFIG_SHELF_CAL_OFFSET, CONFIG_SHELF_CAL_COUNTS_PER_KG);
	return 0;
}

static int cmd_raw(const struct shell *sh, size_t argc, char **argv)
{
	int32_t raw;
	int ret;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	ret = shelf_read_raw(&raw);
	if (ret) {
		shell_error(sh, "read failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "raw %d  ->  %d mg", raw, shelf_raw_to_mg(raw));
	return 0;
}

static int cmd_tare(const struct shell *sh, size_t argc, char **argv)
{
	int32_t raw;
	int ret;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	ret = shelf_read_raw(&raw);
	if (ret) {
		shell_error(sh, "read failed (%d)", ret);
		return ret;
	}

	ret = shelf_cal_set_offset(raw);
	if (ret) {
		shell_error(sh, "save failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "tared: offset = %d counts (saved)", raw);
	return 0;
}

static int cmd_cal(const struct shell *sh, size_t argc, char **argv)
{
	long grams;
	int32_t raw;
	int64_t span;
	int32_t cpkg;
	char *end;
	int ret;

	grams = strtol(argv[1], &end, 10);
	if (*end != '\0' || grams <= 0) {
		shell_error(sh, "expected a positive weight in grams");
		return -EINVAL;
	}

	ret = shelf_read_raw(&raw);
	if (ret) {
		shell_error(sh, "read failed (%d)", ret);
		return ret;
	}

	span = (int64_t)raw - (int64_t)cal_offset;
	if (span == 0) {
		shell_error(sh, "reading is identical to the tare point - "
			        "is the weight actually on the shelf?");
		return -EINVAL;
	}

	cpkg = (int32_t)((span * 1000LL) / (int64_t)grams);

	ret = shelf_cal_set_counts_per_kg(cpkg);
	if (ret) {
		shell_error(sh, "save failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "raw %d, span %lld counts over %ld g", raw, span, grams);
	shell_print(sh, "counts_per_kg = %d (saved)", cpkg);
	return 0;
}

static int cmd_reset(const struct shell *sh, size_t argc, char **argv)
{
	int ret;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	ret = shelf_cal_reset();
	if (ret) {
		shell_error(sh, "delete failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "calibration reset to defaults: offset=%d counts_per_kg=%d",
		    cal_offset, cal_cpkg);
	return 0;
}

static int cmd_ledtest(const struct shell *sh, size_t argc, char **argv)
{
	long cycles = 3;

	if (argc > 1) {
		char *end;

		cycles = strtol(argv[1], &end, 10);
		if (*end != '\0' || cycles < 1 || cycles > 20) {
			shell_error(sh, "expected a cycle count between 1 and 20");
			return -EINVAL;
		}
	}

	shell_print(sh, "breathing %ld cycle(s), ~4 s each...", cycles);
	shelf_led_breathe((unsigned int)cycles);
	shell_print(sh, "done");
	return 0;
}

static int cmd_battery(const struct shell *sh, size_t argc, char **argv)
{
	int32_t mv;
	int ret;

	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	ret = shelf_battery_read_mv(&mv);
	if (ret) {
		shell_error(sh, "read failed (%d)", ret);
		return ret;
	}

	shell_print(sh, "VDDH %d mV", mv);

	if (mv > 4400) {
		shell_print(sh, "USB power (battery disconnected by Q5) - "
				"unplug USB to measure the battery");
	} else {
		/* Crude 1S Li-ion estimate, 3.3 V empty .. 4.2 V full. */
		int32_t pct = (mv - 3300) * 100 / (4200 - 3300);

		pct = CLAMP(pct, 0, 100);
		shell_print(sh, "battery ~%d%% (linear 3.3-4.2 V estimate)", pct);
	}
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(shelf_cmds,
	SHELL_CMD(show,  NULL, "Show the active calibration", cmd_show),
	SHELL_CMD(raw,   NULL, "Take one reading and print raw counts + mg", cmd_raw),
	SHELL_CMD(tare,  NULL, "Store the current reading as the zero point", cmd_tare),
	SHELL_CMD_ARG(cal, NULL,
		      "Calibrate with a known weight: shelf cal <grams>\n"
		      "Tare first with an empty shelf, then put the weight on.",
		      cmd_cal, 2, 0),
	SHELL_CMD(reset, NULL, "Forget the stored calibration", cmd_reset),
	SHELL_CMD_ARG(ledtest, NULL,
		      "Breathe the LED 0->255->0: shelf ledtest [cycles]",
		      cmd_ledtest, 1, 1),
	SHELL_CMD(battery, NULL,
		  "Read VDDH (battery) voltage via the SAADC VDDH/5 tap",
		  cmd_battery),
	SHELL_CMD_ARG(mode, NULL,
		      "Show or force the power mode: shelf mode [idle|active|auto]",
		      shelf_mode_shell_cmd, 1, 1),
	SHELL_SUBCMD_SET_END
);

SHELL_CMD_REGISTER(shelf, &shelf_cmds, "Smart shelf load cell calibration", NULL);

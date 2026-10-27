/*
 * Persistent load cell calibration.
 *
 * The tare offset and the counts-per-kilogram scale live in the Zephyr settings
 * subsystem (NVS on storage_partition), so they survive a reboot and a firmware
 * update and can be set in the field over the shell - no rebuild needed. The
 * Kconfig values are only the factory defaults, used until something is stored.
 */

#ifndef SHELF_CAL_H_
#define SHELF_CAL_H_

#include <stdint.h>

/**
 * Load the stored calibration, falling back to the Kconfig defaults when
 * nothing has been saved yet. Call once at boot, before the measurement thread
 * starts using the values.
 */
int shelf_cal_init(void);

int32_t shelf_cal_offset(void);
int32_t shelf_cal_counts_per_kg(void);

/** Set and persist the tare offset. */
int shelf_cal_set_offset(int32_t offset);

/** Set and persist the scale. Rejects zero - it would divide by zero. */
int shelf_cal_set_counts_per_kg(int32_t counts_per_kg);

/** Drop the stored values and go back to the Kconfig defaults. */
int shelf_cal_reset(void);

/** Convert a raw reading to milligrams using the active calibration. */
int32_t shelf_raw_to_mg(int32_t raw);

/**
 * Take one averaged reading: power the rail up, sample, power it back down.
 *
 * Implemented in main.c because that is where the CS1237 instance lives. It
 * serialises against the measurement thread, so the shell can call it safely.
 */
int shelf_read_raw(int32_t *raw);

/**
 * Fade the LED 0 -> 255 -> 0 the given number of times (hardware test).
 *
 * Implemented in main.c because that is where the LED GPIO lives. Blocks the
 * calling thread for about four seconds per cycle.
 */
int shelf_led_breathe(unsigned int cycles);

/**
 * Read the VDDH rail in millivolts through the SAADC's internal VDDH/5 tap.
 *
 * Implemented in main.c. VDDH is the battery input (+BAT_FIN), so this is the
 * battery voltage when running from it. With USB plugged in Q5 disconnects the
 * battery and the reading is VBUS minus a diode drop (~4.6 V) instead.
 */
int shelf_battery_read_mv(int32_t *mv);

#endif /* SHELF_CAL_H_ */

/*
 * CS1237 24-bit load cell ADC - bit-banged two wire (DRDY/DOUT + SCLK) driver
 * with a switchable supply rail.
 */

#ifndef SHELF_CS1237_H_
#define SHELF_CS1237_H_

#include <stdint.h>
#include <zephyr/drivers/gpio.h>

/* Configuration register bit fields. */
#define CS1237_REFO_OFF    BIT(6)  /* 1 = disable the internal reference output */

#define CS1237_SPEED_10    (0U << 4)
#define CS1237_SPEED_40    (1U << 4)
#define CS1237_SPEED_640   (2U << 4)
#define CS1237_SPEED_1280  (3U << 4)
#define CS1237_SPEED_MASK  (3U << 4)

#define CS1237_PGA_1       (0U << 2)
#define CS1237_PGA_2       (1U << 2)
#define CS1237_PGA_64      (2U << 2)
#define CS1237_PGA_128     (3U << 2)
#define CS1237_PGA_MASK    (3U << 2)

#define CS1237_CH_A        (0U << 0)  /* external differential input */
#define CS1237_CH_TEMP     (2U << 0)  /* internal temperature sensor */
#define CS1237_CH_SHORT    (3U << 0)  /* internal short, for offset measurement */
#define CS1237_CH_MASK     (3U << 0)

struct cs1237 {
	struct gpio_dt_spec sck;
	struct gpio_dt_spec dout;
	struct gpio_dt_spec pwr;
	uint8_t config;   /* value written to the configuration register */
};

/**
 * Claim the GPIOs and leave the rail off. Call once at boot.
 */
int cs1237_init(const struct cs1237 *dev);

/**
 * Switch the rail on, wait for the warm-up time and apply @ref cs1237.config.
 * The chip is ready to be read when this returns 0.
 */
int cs1237_power_up(const struct cs1237 *dev);

/**
 * Like cs1237_power_up() but with an explicit configuration - used to run the
 * same chip at different sample rates per power mode (docs/power-modes.md:
 * 40 Hz gated in IDLE, 640 Hz continuous in ACTIVE).
 */
int cs1237_power_up_cfg(const struct cs1237 *dev, uint8_t config);

/**
 * Switch the rail off and park DOUT/SCLK high impedance so that no current is
 * injected into the unpowered CS1237.
 */
void cs1237_power_down(const struct cs1237 *dev);

/**
 * Read one conversion result, sign extended to 32 bits.
 */
int cs1237_read(const struct cs1237 *dev, int32_t *raw, uint32_t timeout_ms);

/**
 * Throw away @p discard conversions, read @p samples (max 64) and return
 * their interquartile mean - the middle two quartiles averaged, so a rare
 * corrupted frame does not drag the result. Plain mean below 4 samples.
 */
int cs1237_read_avg(const struct cs1237 *dev, uint8_t discard, uint8_t samples,
		    int32_t *raw);

int cs1237_read_config(const struct cs1237 *dev, uint8_t *cfg);
int cs1237_write_config(const struct cs1237 *dev, uint8_t cfg);

#endif /* SHELF_CS1237_H_ */

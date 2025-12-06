#!/usr/bin/env python3
"""
cp2112_driver.py

Minimal CP2112 I2C helper for BC-211 monitor.
This file is written from scratch based on CP2112 datasheet / app note.
"""

import time
import hid


class Cp2112Error(Exception):
    """Generic CP2112 communication error."""
    pass


class Cp2112I2CBus:
    """
    Very small wrapper around a single CP2112 device.

    - Opens the USB HID device (vendor / product fixed to CP2112).
    - Initializes GPIO / SMBus engine.
    - Provides 8-bit I2C register R/W helpers:
        * write_reg8(i2c_addr, reg, value)
        * read_reg8(i2c_addr, reg) -> value
    """

    def __init__(
        self,
        vendor_id: int = 0x10C4,
        product_id: int = 0xEA90,
        serial: str | None = None,
        *,
        enable_rx_tx_led: bool = True,
    ) -> None:
        self._dev = hid.device()
        # open CP2112 HID interface
        if serial is None:
            self._dev.open(vendor_id, product_id)
        else:
            self._dev.open(vendor_id, product_id, serial)

        # optional: print info (debug)
        try:
            print("CP2112 Manufacturer:", self._dev.get_manufacturer_string())
            print("CP2112 Product     :", self._dev.get_product_string())
            print("CP2112 Serial      :", self._dev.get_serial_number_string())
        except Exception:
            # some stacks may fail on these; ignore
            pass

        # configure GPIO and SMBus engine
        self._configure_gpio(led_mode=enable_rx_tx_led)
        self._configure_smbus()

    # -------------------- low-level helpers --------------------

    def _send_feature(self, payload: list[int]) -> None:
        """
        Send a HID feature report.
        CP2112 uses report ID in the first byte, rest is payload.
        """
        # hidapi in Python expects the *entire* report including report ID.
        self._dev.send_feature_report(payload)

    def _reset_device(self) -> None:
        """Reset CP2112 to cancel all transfers / clear error state."""
        # 0x01 = Reset Device (see CP2112 app note)
        self._send_feature([0x01, 0x01])
        # give OS / USB stack a moment
        time.sleep(0.05)

    def close(self) -> None:
        """Close underlying HID device."""
        try:
            self._dev.close()
        except Exception:
            pass

    # -------------------- configuration --------------------

    def _configure_gpio(self, *, led_mode: bool) -> None:
        """
        Configure GPIO pins.

        For BC-211 + MCP23017 I2C only, we don't really care about most
        GPIOs, but we at least need a sane default and may enable
        RX/TX LED if desired.
        """
        # Direction / push-pull / special function are described in CP2112 doc.
        if led_mode:
            # example: use default LED function for some pins
            gpio_direction = 0x83  # bits set -> output
            gpio_pushpull  = 0xFF
            gpio_special   = 0xFF  # LED / clock on low bits
        else:
            # all GPIO as open-drain general purpose
            gpio_direction = 0x00
            gpio_pushpull  = 0x00
            gpio_special   = 0x00

        gpio_clockdiv = 1  # 48MHz/(2*1) = 24MHz on clock pin (if used)

        # 0x02: Set GPIO configuration
        self._send_feature([0x02, gpio_direction, gpio_pushpull, gpio_special, gpio_clockdiv])

    def _configure_smbus(self) -> None:
        """
        Configure SMBus engine: clock, timeouts, retries etc.

        The exact layout of this feature report is from the CP2112 datasheet /
        application note. Values below select roughly 100 kHz I2C and
        reasonable timeouts for short transfers.
        """
        # 0x06: Set SMBus Configuration
        # The following byte sequence is based on CP2112 documentation
        # (clock, timeouts, retry count, etc).
        config = [
            0x06,
            0x00,  # reserved
            0x01,  # enable SMBus
            0x86, 0xA0,  # clock div / timeout (approx 100 kHz)
            0x02, 0x00,  # slave address (unused in host mode)
            0x00,        # reserved
            0xFF, 0x00,  # read timeout
            0xFF, 0x01,  # write timeout
            0x00, 0x0F,  # retries, etc.
        ]
        self._send_feature(config)

    # -------------------- I2C helpers --------------------

    def _wait_transfer_complete(self, *, attempts: int = 10, delay: float = 0.01) -> None:
        """
        Poll transfer status until the SMBus engine reports that data
        is ready or timeout occurs.
        """
        for _ in range(attempts):
            # 0x15: Get Transfer Status
            self._dev.write([0x15, 0x01])
            resp = self._dev.read(7)
            if not resp:
                time.sleep(delay)
                continue
            # 0x16: Transfer Status Response
            if resp[0] == 0x16 and resp[2] == 5:
                return
            time.sleep(delay)
        raise Cp2112Error("CP2112 SMBus transfer timeout")

    def write_reg8(self, i2c_addr: int, reg: int, value: int) -> None:
        """
        Write 8-bit value to [i2c_addr]/reg.
        """
        if not (0 <= value <= 0xFF):
            raise ValueError("value must be 0..255")

        # 0x14: Data Write Request
        # format: [0x14, addr<<1, count, data...]
        pkt = [0x14, (i2c_addr << 1) & 0xFE, 0x02, reg & 0xFF, value & 0xFF]
        written = self._dev.write(pkt)
        if written <= 0:
            raise Cp2112Error("CP2112 write_reg8 failed")

        # poll completion
        self._wait_transfer_complete()

    def read_reg8(self, i2c_addr: int, reg: int) -> int:
        """
        Read 8-bit value from [i2c_addr]/reg.
        """
        # 0x11: Data Write-Read Request
        # [0x11, addr<<1, 0x00, read_len, write_len, reg]
        self._dev.write([
            0x11,
            (i2c_addr << 1) & 0xFE,
            0x00,      # no flags
            0x01,      # read 1 byte
            0x01,      # write 1 byte (the register address)
            reg & 0xFF
        ])

        # wait until data is ready
        self._wait_transfer_complete()

        # 0x12: Force Read Response
        self._dev.write([0x12, 0x00, 0x01])  # offset=0, length=1
        resp = self._dev.read(4)
        if not resp or len(resp) < 4:
            raise Cp2112Error("CP2112 read_reg8: empty response")
        # resp[0]=0x13, resp[3]=data
        return resp[3]

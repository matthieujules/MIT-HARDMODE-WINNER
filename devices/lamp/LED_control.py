"""
RGB LED controller via GPIO PWM.

Accepts an array of (R, G, B, time_ms) tuples and plays them sequentially.
Each tuple sets the LED to that color for the given duration.
After the last tuple, the LED turns off.

Pin config loaded from config.yaml in this directory.
Uses lgpio directly — no sudo required on bookworm.
PWM duty cycle = value / 255 * 100
"""

import os
import time

import lgpio
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def _load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


class LEDController:
    def __init__(self, config_path=None):
        cfg = _load_config() if config_path is None else yaml.safe_load(open(config_path))
        led_cfg = cfg["led"]

        self.red_pin = led_cfg["pins"]["red"]
        self.green_pin = led_cfg["pins"]["green"]
        self.blue_pin = led_cfg["pins"]["blue"]
        self.freq = led_cfg.get("pwm_frequency", 1000)

        self._chip = lgpio.gpiochip_open(0)

        for pin in (self.red_pin, self.green_pin, self.blue_pin):
            lgpio.gpio_claim_output(self._chip, pin)

    def set_color(self, r, g, b):
        """Set RGB values (0-255)."""
        lgpio.tx_pwm(self._chip, self.red_pin, self.freq, r / 255.0 * 100)
        lgpio.tx_pwm(self._chip, self.green_pin, self.freq, g / 255.0 * 100)
        lgpio.tx_pwm(self._chip, self.blue_pin, self.freq, b / 255.0 * 100)

    def off(self):
        self.set_color(0, 0, 0)

    def play(self, sequence):
        """
        Play a sequence of (R, G, B, time_ms) tuples.
        After the last entry, the LED turns off.
        """
        try:
            for r, g, b, duration_ms in sequence:
                self.set_color(r, g, b)
                time.sleep(duration_ms / 1000.0)
        finally:
            self.off()

    def cleanup(self):
        for pin in (self.red_pin, self.green_pin, self.blue_pin):
            lgpio.tx_pwm(self._chip, pin, 0, 0)
            lgpio.gpio_write(self._chip, pin, 0)
        lgpio.gpiochip_close(self._chip)

"""
RGB LED controller via GPIO PWM.

Accepts an array of (R, G, B, time_ms) tuples and plays them sequentially.
Each tuple sets the LED to that color for the given duration.
After the last tuple, the LED turns off.

Pin config loaded from config.yaml in this directory.
PWM duty cycle = value / 255 * 100
"""

import os
import time

import RPi.GPIO as GPIO
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
        freq = led_cfg.get("pwm_frequency", 1000)

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        for pin in (self.red_pin, self.green_pin, self.blue_pin):
            GPIO.setup(pin, GPIO.OUT)

        self.pwm_r = GPIO.PWM(self.red_pin, freq)
        self.pwm_g = GPIO.PWM(self.green_pin, freq)
        self.pwm_b = GPIO.PWM(self.blue_pin, freq)

        self.pwm_r.start(0)
        self.pwm_g.start(0)
        self.pwm_b.start(0)

    def set_color(self, r, g, b):
        """Set RGB values (0-255)."""
        self.pwm_r.ChangeDutyCycle(r / 255.0 * 100)
        self.pwm_g.ChangeDutyCycle(g / 255.0 * 100)
        self.pwm_b.ChangeDutyCycle(b / 255.0 * 100)

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
        self.off()
        self.pwm_r.stop()
        self.pwm_g.stop()
        self.pwm_b.stop()
        GPIO.cleanup()

from __future__ import annotations

import time
from dataclasses import dataclass

from config import DialConfig

try:
    from adafruit_extended_bus import ExtendedI2C as I2C
    from adafruit_pca9685 import PCA9685
except ImportError:
    I2C = None
    PCA9685 = None


@dataclass
class DialEvent:
    direction: str
    duration_seconds: float
    value: float


class RadioDial:
    def __init__(self, config: DialConfig, enabled: bool = True) -> None:
        self.config = config
        self.enabled = enabled
        self._i2c = None
        self._pca = None
        self._channel = None
        self._attached = False
        self._history: list[DialEvent] = []

        if not self.enabled:
            return

        self._setup_pca9685()

        if self._channel is None:
            self.enabled = False
            return

        self.attach()
        self.stop()

    def nudge_clockwise(self, duration_seconds: float | None = None) -> None:
        self._spin("clockwise", self.config.clockwise_value, duration_seconds or self.config.turn_duration_seconds)

    def nudge_counterclockwise(self, duration_seconds: float | None = None) -> None:
        self._spin("counterclockwise", self.config.counterclockwise_value, duration_seconds or self.config.turn_duration_seconds)

    def stop(self) -> None:
        if self._channel is not None:
            self._set_channel_value(self.config.stop_value)
        self._history.append(DialEvent(direction="stop", duration_seconds=0.0, value=self.config.stop_value))

    def attach(self) -> None:
        if self._channel is None:
            return
        self._attached = True

    def detach(self) -> None:
        if self._channel is None:
            return
        # 0 duty disables pulses on PCA9685, effectively detaching the servo.
        self._channel.duty_cycle = 0
        self._attached = False

    def history(self) -> list[DialEvent]:
        return list(self._history)

    def close(self) -> None:
        if self._channel is not None:
            self.stop()
            self.detach()
            self._channel = None
        if self._pca is not None:
            self._pca.deinit()
            self._pca = None
        self._i2c = None

    def _spin(self, direction: str, value: float, duration_seconds: float) -> None:
        if self._channel is not None:
            self.attach()
            self._set_channel_value(value)
            time.sleep(duration_seconds)
            self._set_channel_value(self.config.stop_value)
            time.sleep(self.config.settle_seconds)

        self._history.append(
            DialEvent(
                direction=direction,
                duration_seconds=duration_seconds,
                value=value,
            )
        )

    def _setup_pca9685(self) -> None:
        if I2C is None or PCA9685 is None:
            return
        try:
            self._i2c = I2C(self.config.pca9685_bus)
            self._pca = PCA9685(self._i2c, address=self.config.pca9685_address)
            self._pca.frequency = self.config.pca9685_frequency_hz
            self._channel = self._pca.channels[self.config.pca9685_channel]
        except Exception:
            self._channel = None
            if self._pca is not None:
                self._pca.deinit()
            self._pca = None
            self._i2c = None

    def _set_channel_value(self, value: float) -> None:
        if self._channel is None:
            return
        if not self._attached:
            self.attach()
        pulse_us = self._value_to_us(value)
        self._channel.duty_cycle = self._us_to_duty(pulse_us)

    def _value_to_us(self, value: float) -> int:
        v = max(-1.0, min(1.0, value + self.config.neutral_trim))
        if v >= 0:
            span = self.config.max_pulse_us - self.config.neutral_pulse_us
            return int(self.config.neutral_pulse_us + v * span)
        span = self.config.neutral_pulse_us - self.config.min_pulse_us
        return int(self.config.neutral_pulse_us + v * span)

    @staticmethod
    def _us_to_duty(pulse_us: int) -> int:
        duty = int((pulse_us / 20000.0) * 0xFFFF)
        return max(0, min(0xFFFF, duty))

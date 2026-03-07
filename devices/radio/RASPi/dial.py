from __future__ import annotations

import time
from dataclasses import dataclass

from config import DialConfig

try:
    from gpiozero import Servo
except ImportError:
    Servo = None


@dataclass
class DialEvent:
    direction: str
    duration_seconds: float
    value: float


class RadioDial:
    def __init__(self, config: DialConfig, enabled: bool = True) -> None:
        self.config = config
        self.enabled = enabled and Servo is not None
        self._servo = None
        self._history: list[DialEvent] = []

        if self.enabled:
            try:
                self._servo = Servo(
                    pin=self.config.gpio_pin,
                    min_pulse_width=self.config.min_pulse_width,
                    max_pulse_width=self.config.max_pulse_width,
                )
                self.stop()
            except Exception:
                self.enabled = False
                self._servo = None

    def nudge_clockwise(self, duration_seconds: float | None = None) -> None:
        self._spin("clockwise", self.config.clockwise_value, duration_seconds or self.config.turn_duration_seconds)

    def nudge_counterclockwise(self, duration_seconds: float | None = None) -> None:
        self._spin("counterclockwise", self.config.counterclockwise_value, duration_seconds or self.config.turn_duration_seconds)

    def stop(self) -> None:
        if self._servo is not None:
            self._servo.value = self.config.stop_value
        self._history.append(DialEvent(direction="stop", duration_seconds=0.0, value=self.config.stop_value))

    def history(self) -> list[DialEvent]:
        return list(self._history)

    def close(self) -> None:
        if self._servo is not None:
            self.stop()
            self._servo.close()

    def _spin(self, direction: str, value: float, duration_seconds: float) -> None:
        if self._servo is not None:
            self._servo.value = value
            time.sleep(duration_seconds)
            self._servo.value = self.config.stop_value
            time.sleep(self.config.settle_seconds)

        self._history.append(
            DialEvent(
                direction=direction,
                duration_seconds=duration_seconds,
                value=value,
            )
        )

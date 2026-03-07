from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Iterable

try:  # pragma: no cover - gpiozero usually isn't available in local dev
    from gpiozero import PWMLED
except Exception:  # pragma: no cover - import fallback for non-Pi environments
    PWMLED = None


@dataclass(frozen=True)
class LightFrame:
    r: int
    g: int
    b: int
    t_ms: int


class LEMPLightController:
    def __init__(self, lemp_config: dict, simulate: bool = True):
        self.config = lemp_config
        self.simulate = simulate
        self._hardware_ready = False
        self.pins = lemp_config["pins"]
        self.current_rgb = {
            channel: int(value)
            for channel, value in lemp_config["default_color"].items()
        }
        self._red = None
        self._green = None
        self._blue = None

    def set_rgb(self, rgb: dict[str, int], brightness: float = 1.0) -> dict:
        scaled = self._scale_rgb(rgb, brightness)
        duties = {
            "red": round(scaled["r"] / 255.0, 4),
            "green": round(scaled["g"] / 255.0, 4),
            "blue": round(scaled["b"] / 255.0, 4),
        }

        payload = {
            "rgb": scaled,
            "brightness": round(max(0.0, min(1.0, float(brightness))), 3),
            "pins": dict(self.pins),
            "duty_cycle": duties,
        }

        if self.simulate:
            print("SIMULATED_LEMP_PWM")
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            self._ensure_hardware()
            self._red.value = duties["red"]
            self._green.value = duties["green"]
            self._blue.value = duties["blue"]

        self.current_rgb = scaled
        return payload

    def play_frames(
        self,
        frames: Iterable[tuple[int, int, int, int] | LightFrame],
        brightness: float = 1.0,
        loop_count: int = 1,
    ) -> list[dict]:
        normalized_frames = [self._normalize_frame(frame) for frame in frames]
        payloads: list[dict] = []

        for _ in range(max(1, int(loop_count))):
            for frame in normalized_frames:
                payload = self.set_rgb(
                    {"r": frame.r, "g": frame.g, "b": frame.b},
                    brightness=brightness,
                )
                payload["t_ms"] = frame.t_ms
                payloads.append(payload)
                time.sleep(max(0, frame.t_ms) / 1000.0)

        return payloads

    def close(self) -> None:
        for led in (self._red, self._green, self._blue):
            if led is not None:
                led.close()
        self._hardware_ready = False

    def _ensure_hardware(self) -> None:
        if self.simulate or self._hardware_ready:
            return
        if PWMLED is None:
            raise RuntimeError(
                "gpiozero.PWMLED is not available. Install gpiozero on the Raspberry Pi."
            )
        self._red = PWMLED(
            self.pins["red"],
            frequency=int(self.config.get("pwm_frequency", 1000)),
        )
        self._green = PWMLED(
            self.pins["green"],
            frequency=int(self.config.get("pwm_frequency", 1000)),
        )
        self._blue = PWMLED(
            self.pins["blue"],
            frequency=int(self.config.get("pwm_frequency", 1000)),
        )
        self._hardware_ready = True

    @staticmethod
    def _normalize_frame(frame: tuple[int, int, int, int] | LightFrame) -> LightFrame:
        if isinstance(frame, LightFrame):
            return frame
        if len(frame) != 4:
            raise ValueError("Each light frame must be a 4-value tuple: (r, g, b, t_ms)")
        r, g, b, t_ms = frame
        return LightFrame(int(r), int(g), int(b), int(t_ms))

    @staticmethod
    def _scale_rgb(rgb: dict[str, int], brightness: float) -> dict[str, int]:
        brightness = max(0.0, min(1.0, float(brightness)))
        return {
            channel: max(0, min(255, int(round(int(rgb[channel]) * brightness))))
            for channel in ("r", "g", "b")
        }

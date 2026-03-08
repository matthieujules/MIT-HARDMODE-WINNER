"""Hardware controller for the Lamp device.

Bridges the agent/ws_client interface to LAMP_TASC hardware:
  - LED_control.LEDController for RGB LED (RPi.GPIO PWM)
  - compat.make_bus() for Feetech servo bus (lerobot)

In sim mode (default on Mac), all hardware calls are logged but no
real GPIO or serial I/O occurs.  Sim mode is auto-detected when
RPi.GPIO or lerobot are not importable, or can be forced via the
``simulate`` constructor flag.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from planner import ArmPlan

logger = logging.getLogger(__name__)

# Joint names used by LAMP_TASC / lerobot / compat.py
JOINT_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]


class LEMHardwareController:
    """Unified hardware controller consumed by agent.py, ws_client.py, and main.py.

    Public interface:
        current_joints  : dict[str, float]   — current joint positions
        current_color   : dict[str, int]     — current RGB color {r, g, b}
        config          : dict               — the loaded config.yaml
        apply_plan(plan: ArmPlan) -> dict    — execute an ArmPlan
        close() -> None                      — release hardware resources
    """

    def __init__(self, config: dict, simulate: bool = True):
        self.config = config
        self.simulate = simulate

        # LED config lives under config["led"]
        led_cfg = config.get("led", {})
        self.current_color: dict[str, int] = {
            "r": int(led_cfg.get("default_color", {}).get("r", 255)),
            "g": int(led_cfg.get("default_color", {}).get("g", 180)),
            "b": int(led_cfg.get("default_color", {}).get("b", 120)),
        }
        self._brightness_scale: float = float(led_cfg.get("brightness_scale", 1.0))

        # Arm config lives under config["arm"]
        arm_cfg = config.get("arm", {})
        joints_cfg = arm_cfg.get("joints", {})
        # Initialise joint positions at zero (lerobot normalized midpoint)
        self.current_joints: dict[str, float] = {
            name: 0.0 for name in (joints_cfg.keys() if joints_cfg else JOINT_NAMES)
        }

        # Hardware handles (lazy-init on first real-hardware call)
        self._led: Any | None = None
        self._bus: Any | None = None

        if not simulate:
            self._init_hardware(config)

    # ── Public interface ──────────────────────────────────────────

    def apply_plan(self, plan: ArmPlan) -> dict:
        """Execute an ArmPlan: move joints and set LED color.

        Returns a summary dict with the executed state.
        """
        # Resolve joint targets — only update joints that are present in the plan
        target_joints = dict(self.current_joints)
        for name, value in plan.joints.items():
            if name in target_joints:
                target_joints[name] = float(value)

        # Resolve color
        color = plan.color if plan.color else dict(self.current_color)
        brightness = plan.brightness if plan.brightness is not None else self._brightness_scale
        scaled_color = self._scale_color(color, brightness)

        # Execute on hardware or sim
        self._set_joints(target_joints, plan.duration_ms)
        self._set_led(scaled_color)

        # Play light animation frames if present
        if plan.light_frames:
            self._play_light_frames(plan.light_frames, brightness)

        # Update state
        self.current_joints = target_joints
        self.current_color = scaled_color

        return {
            "joints": dict(target_joints),
            "color": dict(scaled_color),
            "brightness": round(brightness, 3),
            "duration_ms": plan.duration_ms,
            "pose_preview_mm": {},  # kinematics removed — not needed for demo
        }

    def close(self) -> None:
        """Release hardware resources."""
        if self._led is not None:
            try:
                self._led.cleanup()
            except Exception as e:
                logger.warning("LED cleanup error: %s", e)
            self._led = None

        if self._bus is not None:
            try:
                self._bus.disconnect()
            except Exception as e:
                logger.warning("Bus disconnect error: %s", e)
            self._bus = None

    # ── Hardware init ─────────────────────────────────────────────

    def _init_hardware(self, config: dict) -> None:
        """Initialise real hardware handles. Only called when simulate=False."""
        # LED
        try:
            from LED_control import LEDController
            self._led = LEDController()
            # Set default color on boot
            self._led.set_color(
                self.current_color["r"],
                self.current_color["g"],
                self.current_color["b"],
            )
            logger.info("LED hardware initialised")
        except Exception as e:
            logger.error("Failed to init LED hardware: %s — falling back to sim for LED", e)

        # Servo bus
        try:
            from compat import make_bus
            port = config.get("arm", {}).get("serial_port", "/dev/ttyACM0")
            self._bus = make_bus(port=port)
            logger.info("Servo bus initialised on %s", port)
        except Exception as e:
            logger.error("Failed to init servo bus: %s — falling back to sim for arm", e)

    # ── Joint control ─────────────────────────────────────────────

    def _set_joints(self, joints: dict[str, float], duration_ms: int) -> None:
        """Send joint targets to the servo bus, or log in sim mode."""
        if self.simulate or self._bus is None:
            logger.info(
                "SIM joints: %s (duration=%dms)",
                json.dumps({k: round(v, 2) for k, v in joints.items()}),
                duration_ms,
            )
            return

        try:
            # Use lerobot FeetechMotorsBus sync_write with normalized values
            # The values are already in lerobot normalized range (-100 to 100)
            self._bus.sync_write("Goal_Position", joints, normalize=True)
            logger.info("HW joints: %s", {k: round(v, 2) for k, v in joints.items()})
        except TypeError:
            # Fallback for older lerobot API
            self._bus.sync_write("Goal_Position", joints)
            logger.info("HW joints (no normalize): %s", {k: round(v, 2) for k, v in joints.items()})
        except Exception as e:
            logger.error("Failed to write joint positions: %s", e)

    # ── LED control ───────────────────────────────────────────────

    def _set_led(self, color: dict[str, int]) -> None:
        """Set LED color, or log in sim mode."""
        if self.simulate or self._led is None:
            logger.info("SIM LED: R=%d G=%d B=%d", color["r"], color["g"], color["b"])
            return

        try:
            self._led.set_color(color["r"], color["g"], color["b"])
            logger.info("HW LED: R=%d G=%d B=%d", color["r"], color["g"], color["b"])
        except Exception as e:
            logger.error("Failed to set LED color: %s", e)

    def _play_light_frames(
        self, frames: list[tuple[int, int, int, int]], brightness: float
    ) -> None:
        """Play a sequence of (R, G, B, time_ms) light frames."""
        for r, g, b, t_ms in frames:
            scaled = self._scale_color({"r": r, "g": g, "b": b}, brightness)
            self._set_led(scaled)
            self.current_color = scaled
            time.sleep(max(0, t_ms) / 1000.0)

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _scale_color(color: dict[str, int], brightness: float) -> dict[str, int]:
        brightness = max(0.0, min(1.0, float(brightness)))
        return {
            "r": max(0, min(255, int(round(color["r"] * brightness)))),
            "g": max(0, min(255, int(round(color["g"] * brightness)))),
            "b": max(0, min(255, int(round(color["b"] * brightness)))),
        }

"""Hardware controller for the Lamp device.

Bridges the agent/ws_client interface to real hardware:
  - LED_control.LEDController for RGB LED (lgpio PWM)
  - lerobot Robot API (SO100FollowerConfig + make_robot_from_config) for arm

In sim mode (default on Mac), all hardware calls are logged but no
real GPIO or serial I/O occurs.  Sim mode is auto-detected when
lgpio or lerobot are not importable, or can be forced via the
``simulate`` constructor flag.
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Joint names used by lerobot Robot API (without .pos suffix)
JOINT_NAMES = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll"]

POSES_PATH = Path(__file__).parent / "poses.json"


def _load_poses(path: Path) -> dict:
    """Load poses.json. Returns empty dict if not found."""
    if not path.exists():
        logger.warning("No poses.json at %s — pose/animation commands will fail", path)
        return {}
    with open(path) as f:
        return json.load(f)


def _strip_gripper(pose: dict[str, float]) -> dict[str, float]:
    """Remove gripper key from a pose dict (gripper causes crash)."""
    return {k: v for k, v in pose.items() if "gripper" not in k}


def _ensure_pos_suffix(joints: dict[str, float]) -> dict[str, float]:
    """Ensure all joint keys have the .pos suffix required by Robot API."""
    result = {}
    for k, v in joints.items():
        if not k.endswith(".pos"):
            result[f"{k}.pos"] = float(v)
        else:
            result[k] = float(v)
    return result


def _strip_pos_suffix(joints: dict[str, float]) -> dict[str, float]:
    """Remove .pos suffix from joint keys for internal state tracking."""
    return {k.replace(".pos", ""): v for k, v in joints.items()}


class LEMHardwareController:
    """Unified hardware controller consumed by agent.py, ws_client.py, and main.py.

    Public interface:
        current_joints  : dict[str, float]   -- current joint positions (no .pos suffix)
        current_color   : dict[str, int]     -- current RGB color {r, g, b}
        brightness      : float              -- current brightness scale 0.0-1.0
        poses           : dict               -- loaded poses from poses.json
        config          : dict               -- the loaded config.yaml
        move_to_pose(name) -> str            -- move to a named pose/animation
        set_color(r, g, b) -> None           -- set LED color
        set_brightness(b) -> None            -- set brightness
        get_pose_names() -> list[str]        -- list available pose names
        close() -> None                      -- release hardware resources
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
        self.brightness: float = float(led_cfg.get("brightness_scale", 1.0))

        # Load poses from poses.json
        self.poses = _load_poses(POSES_PATH)

        # Initialize joint state from home pose if available
        if "home" in self.poses:
            home_pose = self.poses["home"]
            # home is a static dict (not animation)
            if isinstance(home_pose, dict) and "type" not in home_pose:
                self.current_joints = _strip_pos_suffix(_strip_gripper(home_pose))
            else:
                self.current_joints = {name: 0.0 for name in JOINT_NAMES}
        else:
            self.current_joints = {name: 0.0 for name in JOINT_NAMES}

        # Hardware handles (lazy-init on first real-hardware call)
        self._led: Any | None = None
        self._robot: Any | None = None

        if not simulate:
            self._init_hardware(config)

    # -- Public interface ---------------------------------------------------

    def move_to_pose(self, name: str) -> str:
        """Move to a named pose from poses.json. Handles both static and animation poses.

        Returns a description string of what was done.
        """
        if name not in self.poses:
            available = ", ".join(self.poses.keys())
            return f"Unknown pose '{name}'. Available: {available}"

        pose_data = self.poses[name]

        # Animation pose: {type: "animation", fps: N, frames: [...]}
        if isinstance(pose_data, dict) and pose_data.get("type") == "animation":
            return self._play_animation(name, pose_data)

        # Static pose: {joint.pos: value, ...}
        if isinstance(pose_data, dict):
            clean = _strip_gripper(pose_data)
            error = self._move_to_joints(clean, duration_ms=1500)
            if error:
                return error
            self.current_joints = _strip_pos_suffix(clean)
            return f"Moved to pose '{name}'"

        return f"Invalid pose data for '{name}'"

    def set_color(self, r: int, g: int, b: int) -> None:
        """Set LED color (0-255 per channel). Applies brightness scaling."""
        self.current_color = {"r": r, "g": g, "b": b}
        scaled = self._scale_color(self.current_color, self.brightness)
        self._set_led(scaled)

    def set_brightness(self, brightness: float) -> None:
        """Set brightness 0.0-1.0 and re-apply to current color."""
        self.brightness = max(0.0, min(1.0, float(brightness)))
        scaled = self._scale_color(self.current_color, self.brightness)
        self._set_led(scaled)

    def flash(self, r: int, g: int, b: int, duration_ms: int = 500) -> None:
        """Flash a color briefly, then return to previous color."""
        prev_color = dict(self.current_color)
        self.set_color(r, g, b)
        time.sleep(max(0, duration_ms) / 1000.0)
        self.set_color(prev_color["r"], prev_color["g"], prev_color["b"])

    def pulse(self, r: int, g: int, b: int, cycles: int = 3, period_ms: int = 800) -> None:
        """Sine-wave brightness pulse effect."""
        prev_color = dict(self.current_color)
        prev_brightness = self.brightness
        steps_per_cycle = 30
        step_duration = (period_ms / 1000.0) / steps_per_cycle

        for cycle in range(cycles):
            for step in range(steps_per_cycle):
                phase = (step / steps_per_cycle) * 2 * math.pi
                # Sine from 0.1 to 1.0
                b_scale = 0.1 + 0.9 * (0.5 + 0.5 * math.sin(phase - math.pi / 2))
                scaled = self._scale_color({"r": r, "g": g, "b": b}, b_scale)
                self._set_led(scaled)
                time.sleep(step_duration)

        # Restore previous state
        self.current_color = prev_color
        self.brightness = prev_brightness
        self._set_led(self._scale_color(prev_color, prev_brightness))

    def get_pose_names(self) -> list[str]:
        """Return list of available pose names."""
        return list(self.poses.keys())

    def close(self) -> None:
        """Release hardware resources."""
        if self._led is not None:
            try:
                self._led.cleanup()
            except Exception as e:
                logger.warning("LED cleanup error: %s", e)
            self._led = None

        if self._robot is not None:
            try:
                self._robot.disconnect()
            except Exception as e:
                logger.warning("Robot disconnect error: %s", e)
            self._robot = None

    # -- Hardware init ------------------------------------------------------

    def _init_hardware(self, config: dict) -> None:
        """Initialise real hardware handles. Only called when simulate=False."""
        # LED
        try:
            from LED_control import LEDController
            self._led = LEDController()
            # Set default color on boot
            scaled = self._scale_color(self.current_color, self.brightness)
            self._led.set_color(scaled["r"], scaled["g"], scaled["b"])
            logger.info("LED hardware initialised")
        except Exception as e:
            logger.error("Failed to init LED hardware: %s -- falling back to sim for LED", e)

        # Robot (lerobot Robot API)
        try:
            from lerobot.robots import so_follower, make_robot_from_config
            port = config.get("arm", {}).get("serial_port", "/dev/ttyACM0")
            robot_config = so_follower.SO100FollowerConfig(port=port)
            self._robot = make_robot_from_config(robot_config)
            self._robot.connect()
            logger.info("Robot API initialised on %s", port)

            # Read current position from robot
            try:
                obs = self._robot.get_observation()
                self.current_joints = _strip_pos_suffix(_strip_gripper(dict(obs)))
                logger.info("Read current joint positions: %s",
                            {k: round(v, 2) for k, v in self.current_joints.items()})
            except Exception as e:
                logger.warning("Could not read initial joint positions: %s", e)
        except Exception as e:
            logger.error("Failed to init Robot API: %s -- falling back to sim for arm", e)

    # -- Joint control (Robot API) ------------------------------------------

    def _move_to_joints(self, target: dict[str, float], duration_ms: int = 1000) -> str | None:
        """Move to target joint positions. Interpolates for smooth motion.

        target: dict with .pos suffix keys and float values
        Returns None on success, or an error string on failure.
        """
        if self.simulate or self._robot is None:
            logger.info(
                "SIM joints: %s (duration=%dms)",
                json.dumps({k: round(v, 2) for k, v in target.items()}),
                duration_ms,
            )
            return None

        try:
            # Get current position for interpolation
            obs = self._robot.get_observation()
            current = _strip_gripper(dict(obs))

            # Ensure target has .pos suffix
            target_pos = _ensure_pos_suffix(_strip_gripper(target))

            # Interpolate over frames for smooth motion
            fps = 30
            total_frames = max(1, int((duration_ms / 1000.0) * fps))
            frame_interval = (duration_ms / 1000.0) / total_frames

            for i in range(1, total_frames + 1):
                t = i / total_frames  # 0..1
                frame = {}
                for key in target_pos:
                    if key in current:
                        start = float(current[key])
                        end = float(target_pos[key])
                        # Smooth easing (ease-in-out)
                        t_smooth = t * t * (3 - 2 * t)
                        frame[key] = start + (end - start) * t_smooth
                    else:
                        frame[key] = float(target_pos[key])

                frame_start = time.perf_counter()
                self._robot.send_action(frame)
                elapsed = time.perf_counter() - frame_start
                sleep_time = frame_interval - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

            logger.info("HW joints moved to: %s", {k: round(v, 2) for k, v in target_pos.items()})
            return None

        except Exception as e:
            logger.error("Failed to move joints: %s", e)
            # Try direct move as fallback
            try:
                target_pos = _ensure_pos_suffix(_strip_gripper(target))
                self._robot.send_action(target_pos)
                logger.info("HW joints (direct): %s", {k: round(v, 2) for k, v in target_pos.items()})
                return None
            except Exception as e2:
                logger.error("Direct move also failed: %s", e2)
                # Attempt to reconnect the serial port
                self._try_reconnect_robot()
                return f"ARM ERROR: serial port failure ({e2}). Arm did not move."

    def _play_animation(self, name: str, anim_data: dict) -> str:
        """Play an animation from poses.json."""
        fps = anim_data.get("fps", 30)
        frames = anim_data.get("frames", [])
        if not frames:
            return f"Animation '{name}' has no frames"

        if self.simulate or self._robot is None:
            logger.info("SIM animation '%s': %d frames @ %dfps", name, len(frames), fps)
            return f"Played animation '{name}' ({len(frames)} frames)"

        interval = 1.0 / fps
        logger.info("Playing animation '%s': %d frames @ %dfps", name, len(frames), fps)

        failed = False
        for frame in frames:
            clean = _strip_gripper(frame)
            pose = _ensure_pos_suffix(clean)
            frame_start = time.perf_counter()
            try:
                self._robot.send_action(pose)
            except Exception as e:
                logger.error("Animation frame error: %s", e)
                self._try_reconnect_robot()
                failed = True
                break
            elapsed = time.perf_counter() - frame_start
            sleep_time = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        if failed:
            return f"ARM ERROR: animation '{name}' failed mid-playback. Arm may be in unexpected position."

        # Update current joints from last frame
        last_frame = _strip_gripper(frames[-1])
        self.current_joints = _strip_pos_suffix(last_frame)

        return f"Played animation '{name}' ({len(frames)} frames @ {fps}fps)"

    def _try_reconnect_robot(self) -> None:
        """Attempt to disconnect and reconnect the robot after a serial failure."""
        if self._robot is None:
            return
        logger.warning("Attempting robot reconnect after serial failure...")
        try:
            self._robot.disconnect()
        except Exception:
            pass
        try:
            self._robot.connect()
            logger.info("Robot reconnected successfully")
        except Exception as e:
            logger.error("Robot reconnect failed: %s — arm is offline", e)
            self._robot = None

    # -- LED control --------------------------------------------------------

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

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _scale_color(color: dict[str, int], brightness: float) -> dict[str, int]:
        brightness = max(0.0, min(1.0, float(brightness)))
        return {
            "r": max(0, min(255, int(round(color["r"] * brightness)))),
            "g": max(0, min(255, int(round(color["g"] * brightness)))),
            "b": max(0, min(255, int(round(color["b"] * brightness)))),
        }

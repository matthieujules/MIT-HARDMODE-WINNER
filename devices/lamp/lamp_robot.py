from __future__ import annotations

import importlib
import inspect
import json
import time
from pathlib import Path
from typing import Any


def _load_lerobot_feetech() -> tuple[type[Any], type[Any], type[Any], type[Any], Any]:
    candidates = [
        (
            "lerobot.motors.feetech",
            "lerobot.motors",
            "FeetechMotorsBus",
            "Motor",
            "MotorNormMode",
            "MotorCalibration",
            "OperatingMode",
        ),
        (
            "lerobot.common.robot_devices.motors.feetech",
            "lerobot.common.robot_devices.motors.utils",
            "FeetechMotorsBus",
            "Motor",
            "MotorNormMode",
            "MotorCalibration",
            "OperatingMode",
        ),
    ]

    last_error: Exception | None = None
    for bus_module_name, model_module_name, bus_attr, motor_attr, norm_attr, calib_attr, op_mode_attr in candidates:
        try:
            bus_module = importlib.import_module(bus_module_name)
            model_module = importlib.import_module(model_module_name)
            return (
                getattr(bus_module, bus_attr),
                getattr(model_module, motor_attr),
                getattr(model_module, norm_attr),
                getattr(model_module, calib_attr),
                getattr(bus_module, op_mode_attr, None),
            )
        except Exception as error:  # pragma: no cover - import compatibility branch
            last_error = error

    raise RuntimeError(
        "LeRobot Feetech support is not installed. Install the Lamp dependencies and ensure "
        "`lerobot[feetech]` is available on the Pi."
    ) from last_error


class LampRobot:
    def __init__(self, config: dict):
        self.config = config
        self.arm_config = config["hardware"]["arm"]
        self.joint_config = self.arm_config["joints"]
        self.joint_names = list(self.joint_config.keys())
        self.lerobot_config = self.arm_config.get("lerobot", {})
        self.motion_config = self.arm_config.get("motion", {})
        self._bus: Any | None = None
        self._operating_mode: Any | None = None
        self._calibration = self._load_calibration()

    @property
    def is_connected(self) -> bool:
        return self._bus is not None

    def connect(self) -> None:
        if self._bus is not None:
            return

        FeetechMotorsBus, Motor, MotorNormMode, MotorCalibration, OperatingMode = _load_lerobot_feetech()
        self._operating_mode = OperatingMode

        norm_mode_name = str(self.arm_config.get("norm_mode", "RANGE_M100_100"))
        try:
            norm_mode = getattr(MotorNormMode, norm_mode_name)
        except AttributeError as error:
            raise RuntimeError(f"Unsupported MotorNormMode '{norm_mode_name}' in lamp config.") from error

        motor_model = str(self.arm_config.get("motor_model", "sts3215"))
        motors = {
            joint_name: Motor(
                int(joint["servo_id"]),
                motor_model,
                norm_mode,
            )
            for joint_name, joint in self.joint_config.items()
        }
        calibration = {
            joint_name: MotorCalibration(**values)
            for joint_name, values in self._calibration.items()
        }

        serial_config = self.arm_config["serial"]
        bus_kwargs: dict[str, Any] = {
            "port": serial_config["port"],
            "motors": motors,
            "calibration": calibration,
        }
        try:
            signature = inspect.signature(FeetechMotorsBus)
            if "baudrate" in signature.parameters:
                bus_kwargs["baudrate"] = int(serial_config.get("baud_rate", 1000000))
        except (TypeError, ValueError):
            pass

        self._bus = FeetechMotorsBus(**bus_kwargs)
        self._bus.connect()
        hold_pose = self.get_raw_positions()
        self._configure_bus(hold_pose)
        self.enable_torque()
        self._sync_write("Goal_Position", hold_pose, normalize=False)

    def disconnect(self) -> None:
        if self._bus is not None and hasattr(self._bus, "disconnect"):
            try:
                self._bus.disconnect()
            except RuntimeError as error:
                if "Overload error" not in str(error):
                    raise
        self._bus = None

    def enable_torque(self) -> None:
        self._ensure_connected()
        self._write_all_motors("Torque_Enable", 1)

    def disable_torque(self) -> None:
        self._ensure_connected()
        self._write_all_motors("Torque_Enable", 0)

    def get_observation(self) -> dict[str, float]:
        self._ensure_connected()
        positions = self._sync_read("Present_Position", normalize=True)
        return {
            f"{joint_name}.pos": self._bus_value_to_angle(joint_name, float(positions[joint_name]))
            for joint_name in self.joint_names
        }

    def get_raw_positions(self) -> dict[str, float]:
        self._ensure_connected()
        positions = self._sync_read("Present_Position", normalize=False)
        return {
            joint_name: float(positions[joint_name])
            for joint_name in self.joint_names
        }

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self._ensure_connected()
        values = self._normalize_action(action)
        bus_values = {
            joint_name: self._angle_to_bus_value(joint_name, float(values[joint_name]))
            for joint_name in self.joint_names
        }
        self._sync_write("Goal_Position", bus_values, normalize=True)
        return {
            f"{joint_name}.pos": float(values[joint_name])
            for joint_name in self.joint_names
        }

    def interpolate(
        self,
        start_joints: dict[str, float],
        end_joints: dict[str, float],
        steps: int,
    ) -> list[dict[str, float]]:
        if steps < 2:
            return [self._normalize_action(end_joints)]

        frames: list[dict[str, float]] = []
        for index in range(steps):
            alpha = index / (steps - 1)
            frame = {
                joint_name: round(
                    float(start_joints[joint_name])
                    + (float(end_joints[joint_name]) - float(start_joints[joint_name])) * alpha,
                    3,
                )
                for joint_name in self.joint_names
            }
            frames.append(frame)
        return frames

    def move_to_pose(
        self,
        joints: dict[str, float],
        duration_ms: int,
        frame_ms: int = 100,
    ) -> dict[str, float]:
        self._ensure_connected()
        observation = self.get_observation()
        start = {
            joint_name: float(observation[f"{joint_name}.pos"])
            for joint_name in self.joint_names
        }
        target = self._normalize_action(joints)
        frame_ms = max(20, int(frame_ms))
        steps = max(2, int(round(max(0, duration_ms) / frame_ms)) + 1)

        for frame in self.interpolate(start, target, steps):
            self.send_action(frame)
            time.sleep(frame_ms / 1000.0)
        return target

    def move_to_raw_positions(
        self,
        positions: dict[str, float],
        duration_ms: int,
        frame_ms: int = 100,
    ) -> dict[str, float]:
        self._ensure_connected()
        start = self.get_raw_positions()
        target = {
            joint_name: float(positions[joint_name])
            for joint_name in self.joint_names
        }
        configured_frame_ms = int(self.motion_config.get("raw_frame_ms", frame_ms))
        requested_frame_ms = max(20, configured_frame_ms)
        min_frames = max(2, int(self.motion_config.get("min_raw_frames", 3)))
        max_frames = max(min_frames, int(self.motion_config.get("max_raw_frames", 8)))
        time_steps = max(1, int(round(max(0, duration_ms) / requested_frame_ms)))
        steps = max(min_frames, min(max_frames, time_steps)) + 1

        for frame in self.interpolate(start, target, steps):
            self._sync_write("Goal_Position", frame, normalize=False)
            time.sleep(requested_frame_ms / 1000.0)
        return target

    def _configure_bus(self, hold_pose: dict[str, float] | None = None) -> None:
        bus = self._ensure_connected()
        torque_disabled = getattr(bus, "torque_disabled", None)
        configure_motors = getattr(bus, "configure_motors", None)

        if callable(torque_disabled):
            with torque_disabled():
                if callable(configure_motors):
                    configure_motors()
                self._apply_motor_settings()
                if hold_pose is not None:
                    self._sync_write("Goal_Position", hold_pose, normalize=False)
            return

        self.disable_torque()
        try:
            if callable(configure_motors):
                configure_motors()
            self._apply_motor_settings()
            if hold_pose is not None:
                self._sync_write("Goal_Position", hold_pose, normalize=False)
        finally:
            self.enable_torque()

    def _apply_motor_settings(self) -> None:
        position_mode = 0
        if self._operating_mode is not None and hasattr(self._operating_mode, "POSITION"):
            position_mode = getattr(self._operating_mode.POSITION, "value", 0)

        for register_name in ("Mode", "Operating_Mode"):
            self._write_all_motors(register_name, position_mode)

        for register_name, value in (
            ("P_Coefficient", 16),
            ("I_Coefficient", 0),
            ("D_Coefficient", 32),
            ("Lock", 0),
            ("Maximum_Acceleration", 50),
            ("Acceleration", 50),
        ):
            self._write_all_motors(register_name, value)

    def _normalize_action(self, action: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for joint_name in self.joint_names:
            action_key = f"{joint_name}.pos"
            if action_key in action:
                value = action[action_key]
            elif joint_name in action:
                value = action[joint_name]
            else:
                raise KeyError(f"Missing joint target '{joint_name}' in action.")
            normalized[joint_name] = self._clamp_angle(joint_name, float(value))
        return normalized

    def _ensure_connected(self) -> Any:
        if self._bus is None:
            self.connect()
        return self._bus

    def _write_all_motors(self, register_name: str, value: Any) -> None:
        bus = self._ensure_connected()
        write = getattr(bus, "write", None)
        if not callable(write):
            return

        motor_names = list(self.joint_names)
        attempts = [
            (register_name, motor_names, value),
            (register_name, {motor: value for motor in motor_names}),
            (register_name, value),
        ]
        for attempt in attempts:
            try:
                write(*attempt)
                return
            except TypeError:
                continue
            except Exception:
                continue

    def _sync_read(self, register_name: str, normalize: bool = True) -> dict[str, float]:
        bus = self._ensure_connected()
        method = getattr(bus, "sync_read", None)
        if method is None:
            raise RuntimeError("Feetech bus does not expose sync_read().")
        try:
            values = method(register_name, normalize=normalize)
        except TypeError:
            values = method(register_name)

        if isinstance(values, dict):
            return {
                str(joint_name): float(value)
                for joint_name, value in values.items()
            }

        sequence = list(values)
        if len(sequence) != len(self.joint_names):
            raise RuntimeError(
                f"Expected {len(self.joint_names)} values from sync_read, received {len(sequence)}"
            )
        return {
            joint_name: float(sequence[index])
            for index, joint_name in enumerate(self.joint_names)
        }

    def _sync_write(self, register_name: str, values: dict[str, float], normalize: bool = True) -> None:
        bus = self._ensure_connected()
        sync_write = getattr(bus, "sync_write", None)
        if callable(sync_write):
            attempts = [
                (register_name, values, normalize),
                (register_name, values, None),
            ]
            for register, payload, normalized in attempts:
                try:
                    if normalized is None:
                        sync_write(register, payload)
                    else:
                        sync_write(register, payload, normalize=normalized)
                    return
                except TypeError:
                    continue

        write = getattr(bus, "write", None)
        if callable(write):
            attempts = [
                (register_name, values),
                (register_name, list(values.keys()), list(values.values())),
            ]
            for attempt in attempts:
                try:
                    write(*attempt)
                    return
                except TypeError:
                    continue

        raise RuntimeError("Feetech bus does not expose a compatible sync_write()/write() method.")

    def _load_calibration(self) -> dict:
        calibration_path = Path(
            self.lerobot_config.get("calibration_path", "")
        ).expanduser()
        if not calibration_path.exists():
            raise RuntimeError(
                "LeRobot calibration file not found.\n"
                f"Expected: {calibration_path}"
            )

        with calibration_path.open("r", encoding="utf-8") as handle:
            raw_calibration = json.load(handle)

        remapped: dict[str, dict] = {}
        missing: list[str] = []
        for joint_name, joint in self.joint_config.items():
            calibration_name = str(joint.get("calibration_name", joint_name))
            calibration_values = raw_calibration.get(calibration_name)
            if calibration_values is None:
                missing.append(f"{joint_name} -> {calibration_name}")
                continue
            remapped[joint_name] = calibration_values

        if missing:
            raise RuntimeError(
                "Calibration file is missing expected joint names.\n"
                f"Missing mappings: {', '.join(missing)}"
            )
        return remapped

    def _clamp_angle(self, joint_name: str, value: float) -> float:
        joint = self.joint_config[joint_name]
        return max(float(joint["min_angle"]), min(float(joint["max_angle"]), float(value)))

    def _normalized_range(self) -> tuple[float, float] | None:
        mode = str(self.arm_config.get("norm_mode", "")).upper()
        if mode == "RANGE_M100_100":
            return (-100.0, 100.0)
        if mode == "RANGE_0_100":
            return (0.0, 100.0)
        return None

    def _bus_value_to_angle(self, joint_name: str, value: float) -> float:
        norm_range = self._normalized_range()
        if norm_range is None:
            return float(value)

        joint = self.joint_config[joint_name]
        min_angle = float(joint["min_angle"])
        max_angle = float(joint["max_angle"])
        low, high = norm_range
        alpha = (float(value) - low) / (high - low)
        return self._clamp_angle(joint_name, min_angle + alpha * (max_angle - min_angle))

    def _angle_to_bus_value(self, joint_name: str, angle: float) -> float:
        norm_range = self._normalized_range()
        if norm_range is None:
            return float(angle)

        joint = self.joint_config[joint_name]
        min_angle = float(joint["min_angle"])
        max_angle = float(joint["max_angle"])
        clamped_angle = self._clamp_angle(joint_name, angle)
        if max_angle == min_angle:
            return float(norm_range[0])
        alpha = (clamped_angle - min_angle) / (max_angle - min_angle)
        low, high = norm_range
        return low + alpha * (high - low)



class LerobotLampPlayer:
    def __init__(self, config: dict):
        self.robot = LampRobot(config)

    def connect(self) -> None:
        self.robot.connect()

    def disconnect(self) -> None:
        self.robot.disconnect()

    def play_pose(self, joints: dict[str, float], duration_ms: int = 1000) -> dict[str, float]:
        return self.robot.move_to_pose(joints=joints, duration_ms=duration_ms)

    def play_raw_pose(self, positions: dict[str, float], duration_ms: int = 1000) -> dict[str, float]:
        return self.robot.move_to_raw_positions(positions=positions, duration_ms=duration_ms)

    def interpolate(
        self,
        start_joints: dict[str, float],
        end_joints: dict[str, float],
        steps: int,
    ) -> list[dict[str, float]]:
        return self.robot.interpolate(start_joints, end_joints, steps)

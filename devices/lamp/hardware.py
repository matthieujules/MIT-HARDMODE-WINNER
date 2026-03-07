from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
from typing import Any

from kinematics import forward_kinematics_mm, geometry_from_config
from light_controller import LEMPLightController
from planner import ArmPlan


def _load_lerobot_feetech() -> tuple[type[Any], type[Any], type[Any], type[Any], type[Any]]:
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
                getattr(bus_module, op_mode_attr),
            )
        except Exception as error:  # pragma: no cover - import compatibility branch
            last_error = error

    raise RuntimeError(
        "LeRobot Feetech support is not installed. Install the Lamp dependencies and ensure "
        "`lerobot[feetech]` is available on the Pi."
    ) from last_error


class LEMHardwareController:
    def __init__(self, config: dict, simulate: bool = True, enable_light: bool = True):
        self.config = config
        self.simulate = simulate
        self.enable_light = enable_light
        self.geometry = geometry_from_config(config)
        self.arm_config = config["hardware"]["arm"]
        self.lerobot_config = self.arm_config.get("lerobot", {})
        self.joint_config = self.arm_config["joints"]
        self.readback_config = self.arm_config.get("readback", {})
        self.calibration = {} if simulate else self._load_calibration()
        self.current_joints = {
            joint_name: float(joint["home_angle"])
            for joint_name, joint in self.joint_config.items()
        }
        self.current_color = {
            channel: int(value)
            for channel, value in config["hardware"]["lemp"]["default_color"].items()
        }
        self.light_controller = (
            LEMPLightController(
                config["hardware"]["lemp"],
                simulate=simulate,
            )
            if enable_light
            else None
        )
        self._bus: Any | None = None

    def apply_plan(self, plan: ArmPlan) -> dict:
        payload = self.apply_pose(
            joints=plan.joints,
            duration_ms=plan.duration_ms,
            color=plan.color,
            brightness=plan.brightness,
        )
        if plan.light_frames:
            payload["light_animation"] = self.play_light_animation(
                frames=plan.light_frames,
                brightness=plan.brightness,
                loop_count=1,
            )
        return payload

    def apply_pose(
        self,
        joints: dict[str, float],
        duration_ms: int = 1000,
        color: dict[str, int] | None = None,
        brightness: float = 1.0,
    ) -> dict:
        clamped_joints = {
            joint_name: self._clamp_angle(joint_name, joints.get(joint_name, self.current_joints[joint_name]))
            for joint_name in self.joint_config
        }
        resolved_color = color or self.current_color
        scaled_color = self._scale_color(resolved_color, brightness)
        pose_xyz = forward_kinematics_mm(clamped_joints, self.geometry)

        payload = {
            "duration_ms": int(duration_ms),
            "joints": [
                {
                    "name": joint_name,
                    "servo_id": int(self.joint_config[joint_name]["servo_id"]),
                    "angle_deg": round(clamped_joints[joint_name], 3),
                }
                for joint_name in self.joint_config
            ],
            "lemp": {
                "rgb": scaled_color,
                "brightness": round(brightness, 3),
                "pins": self.config["hardware"]["lemp"]["pins"],
            },
            "pose_preview_mm": pose_xyz,
        }

        if not self.simulate:
            self.enable_torque()
        self._emit_joint_targets(clamped_joints, duration_ms)
        if self.enable_light:
            self._emit_light_payload(payload["lemp"])

        self.current_joints = clamped_joints
        self.current_color = scaled_color
        return payload

    def read_current_joints(self) -> dict[str, float]:
        if self.simulate:
            return dict(self.current_joints)

        bus = self._get_bus()
        register_name = str(self.readback_config.get("register", "Present_Position"))
        raw_positions = self._sync_read(bus, register_name, normalize=True)
        if len(raw_positions) != len(self.joint_config):
            raise RuntimeError(
                f"Expected {len(self.joint_config)} motor positions, received {len(raw_positions)}"
            )

        resolved = {}
        for joint_name in self.joint_config.keys():
            bus_value = raw_positions[joint_name]
            resolved[joint_name] = self._clamp_angle(
                joint_name,
                self._bus_value_to_angle(joint_name, float(bus_value)),
            )
        self.current_joints.update(resolved)
        return dict(self.current_joints)

    def read_current_bus_positions(self) -> dict[str, float]:
        if self.simulate:
            return {
                joint_name: float(angle)
                for joint_name, angle in self.current_joints.items()
            }

        bus = self._get_bus()
        register_name = str(self.readback_config.get("register", "Present_Position"))
        positions = self._sync_read(bus, register_name, normalize=False)
        if len(positions) != len(self.joint_config):
            raise RuntimeError(
                f"Expected {len(self.joint_config)} motor positions, received {len(positions)}"
            )
        return {
            joint_name: float(positions[joint_name])
            for joint_name in self.joint_config.keys()
        }

    def enable_torque(self) -> None:
        if self.simulate:
            print("SIMULATED_TORQUE_ON")
            return
        bus = self._get_bus()
        if hasattr(bus, "enable_torque"):
            try:
                bus.enable_torque()
                return
            except TypeError:
                pass
        self._write_all_motors(bus, "Torque_Enable", 1)

    def disable_torque(self) -> None:
        if self.simulate:
            print("SIMULATED_TORQUE_OFF")
            return
        bus = self._get_bus()
        if hasattr(bus, "disable_torque"):
            try:
                bus.disable_torque()
                return
            except TypeError:
                pass
        self._write_all_motors(bus, "Torque_Enable", 0)

    def interpolate_poses(
        self,
        start_joints: dict[str, float],
        end_joints: dict[str, float],
        steps: int,
    ) -> list[dict[str, float]]:
        if steps < 2:
            return [dict(end_joints)]

        frames: list[dict[str, float]] = []
        joint_names = list(self.joint_config.keys())
        for index in range(steps):
            alpha = index / (steps - 1)
            frame = {
                joint_name: round(
                    float(start_joints[joint_name])
                    + (float(end_joints[joint_name]) - float(start_joints[joint_name])) * alpha,
                    3,
                )
                for joint_name in joint_names
            }
            frames.append(frame)
        return frames

    def close(self) -> None:
        if self.light_controller is not None:
            self.light_controller.close()
        if self._bus is not None and hasattr(self._bus, "disconnect"):
            self._bus.disconnect()
            self._bus = None

    def _emit_joint_targets(self, joints: dict[str, float], duration_ms: int) -> None:
        if self.simulate:
            print("SIMULATED_FEETECH_GOAL")
            print(
                json.dumps(
                    {
                        "duration_ms": int(duration_ms),
                        "goal_positions": {
                            joint_name: round(joints[joint_name], 3)
                            for joint_name, angle in joints.items()
                        },
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return

        bus = self._get_bus()
        goal_positions = {
            joint_name: self._angle_to_bus_value(joint_name, float(joints[joint_name]))
            for joint_name in self.joint_config
        }
        self._sync_write(bus, "Goal_Position", goal_positions, normalize=True)

    def _emit_light_payload(self, lemp_payload: dict) -> None:
        if self.light_controller is None:
            return
        self.light_controller.set_rgb(
            lemp_payload["rgb"],
            brightness=float(lemp_payload.get("brightness", 1.0)),
        )

    def play_light_animation(
        self,
        frames: list[tuple[int, int, int, int]],
        brightness: float = 1.0,
        loop_count: int = 1,
    ) -> list[dict]:
        payloads = self.light_controller.play_frames(
            frames=frames,
            brightness=brightness,
            loop_count=loop_count,
        )
        if payloads:
            self.current_color = dict(payloads[-1]["rgb"])
        return payloads

    def _get_bus(self) -> Any:
        if self._bus is not None:
            return self._bus

        FeetechMotorsBus, Motor, MotorNormMode, MotorCalibration, OperatingMode = _load_lerobot_feetech()
        norm_mode_name = str(self.arm_config.get("norm_mode", "RANGE_M100_100"))
        try:
            norm_mode = getattr(MotorNormMode, norm_mode_name)
        except AttributeError as error:
            raise RuntimeError(f"Unsupported MotorNormMode '{norm_mode_name}' in lamp config.") from error

        motor_model = self.arm_config.get("motor_model", "sts3215")
        motor_definitions = {
            joint_name: Motor(
                int(joint["servo_id"]),
                motor_model,
                norm_mode,
            )
            for joint_name, joint in self.joint_config.items()
        }

        serial_config = self.arm_config["serial"]
        bus_kwargs = {
            "port": serial_config["port"],
            "motors": motor_definitions,
            "calibration": {
                joint_name: MotorCalibration(**calibration_values)
                for joint_name, calibration_values in self.calibration.items()
            },
        }
        try:
            signature = inspect.signature(FeetechMotorsBus)
            if "baudrate" in signature.parameters:
                bus_kwargs["baudrate"] = int(serial_config.get("baud_rate", 1000000))
        except (TypeError, ValueError):
            pass

        self._bus = FeetechMotorsBus(**bus_kwargs)
        self._bus.connect()
        self._configure_bus(OperatingMode)
        return self._bus

    def _configure_bus(self, OperatingMode: Any) -> None:
        bus = self._bus
        if bus is None:
            return

        torque_disabled = getattr(bus, "torque_disabled", None)
        configure_motors = getattr(bus, "configure_motors", None)

        if callable(torque_disabled) and callable(configure_motors):
            with torque_disabled():
                configure_motors()
                for motor in self.joint_config:
                    self._write_motor_setting(bus, "Operating_Mode", motor, OperatingMode.POSITION.value)
                    self._write_motor_setting(bus, "P_Coefficient", motor, 16)
                    self._write_motor_setting(bus, "I_Coefficient", motor, 0)
                    self._write_motor_setting(bus, "D_Coefficient", motor, 32)
            return

        if callable(configure_motors):
            configure_motors()

    def _write_motor_setting(self, bus: Any, register_name: str, motor: str, value: Any) -> None:
        write = getattr(bus, "write", None)
        if not callable(write):
            return

        attempts = [
            (register_name, {motor: value}),
            (register_name, [motor], value),
            (register_name, motor, value),
            (register_name, value, motor),
        ]
        for attempt in attempts:
            try:
                write(*attempt)
                return
            except TypeError:
                continue

    def _write_all_motors(self, bus: Any, register_name: str, value: Any) -> None:
        write = getattr(bus, "write", None)
        if not callable(write):
            return

        motor_names = list(self.joint_config.keys())
        attempts = [
            (register_name, motor_names, value),
            (register_name, {motor: value for motor in motor_names}),
        ]
        for attempt in attempts:
            try:
                write(*attempt)
                return
            except TypeError:
                continue

    def _sync_read(self, bus: Any, register_name: str, normalize: bool = True) -> dict[str, float]:
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
        if len(sequence) != len(self.joint_config):
            raise RuntimeError(
                f"Expected {len(self.joint_config)} values from sync_read, received {len(sequence)}"
            )
        return {
            joint_name: float(sequence[index])
            for index, joint_name in enumerate(self.joint_config.keys())
        }

    def _sync_write(self, bus: Any, register_name: str, values: Any, normalize: bool = True) -> None:
        sync_write = getattr(bus, "sync_write", None)
        if callable(sync_write):
            try:
                sync_write(register_name, values, normalize=normalize)
            except TypeError:
                sync_write(register_name, values)
            return

        write = getattr(bus, "write", None)
        if callable(write):
            try:
                write(register_name, values, normalize=normalize)
                return
            except TypeError:
                try:
                    write(register_name, values)
                    return
                except TypeError:
                    write(register_name, values, list(self.joint_config.keys()))
                    return

        raise RuntimeError("Feetech bus does not expose sync_write() or write().")

    def _clamp_angle(self, joint_name: str, angle: float) -> float:
        limits = self.joint_config[joint_name]
        return max(float(limits["min_angle"]), min(float(limits["max_angle"]), float(angle)))

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
        return min_angle + alpha * (max_angle - min_angle)

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

    @staticmethod
    def _scale_color(color: dict[str, int], brightness: float) -> dict[str, int]:
        brightness = max(0.0, min(1.0, float(brightness)))
        return {
            channel: max(0, min(255, int(round(int(value) * brightness))))
            for channel, value in color.items()
        }

    def _load_calibration(self) -> dict | None:
        calibration_path_value = self.lerobot_config.get("calibration_path")
        if calibration_path_value:
            calibration_path = Path(calibration_path_value).expanduser()
        else:
            robot_type = self.lerobot_config.get("robot_type", "so101_follower")
            robot_id = self.lerobot_config.get("robot_id", "lamp_arm")
            calibration_path = (
                Path.home()
                / ".cache"
                / "huggingface"
                / "lerobot"
                / "calibration"
                / "robots"
                / str(robot_type)
                / f"{robot_id}.json"
            )

        if not calibration_path.exists():
            robot_type = self.lerobot_config.get("robot_type", "so101_follower")
            robot_id = self.lerobot_config.get("robot_id", "lamp_arm")
            raise RuntimeError(
                "LeRobot calibration file not found.\n"
                f"Expected: {calibration_path}\n"
                "Run calibration on the Pi first, for example:\n"
                f"  lerobot-calibrate --robot.type={robot_type} "
                f"--robot.port={self.arm_config['serial']['port']} --robot.id={robot_id}"
            )

        with calibration_path.open("r", encoding="utf-8") as handle:
            raw_calibration = json.load(handle)

        if not isinstance(raw_calibration, dict):
            raise RuntimeError(
                f"Unexpected calibration file format in {calibration_path}: expected a JSON object."
            )

        remapped: dict = {}
        missing_keys: list[str] = []
        for joint_name, joint in self.joint_config.items():
            calibration_name = str(joint.get("calibration_name", joint_name))
            if calibration_name not in raw_calibration:
                missing_keys.append(f"{joint_name} -> {calibration_name}")
                continue
            remapped[joint_name] = raw_calibration[calibration_name]

        if missing_keys:
            available = ", ".join(sorted(raw_calibration.keys()))
            missing = ", ".join(missing_keys)
            raise RuntimeError(
                "Calibration file is missing expected motor names for this Lamp config.\n"
                f"Missing mappings: {missing}\n"
                f"Available calibration keys: {available}"
            )

        return remapped

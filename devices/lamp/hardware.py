from __future__ import annotations

import json

import serial

from kinematics import forward_kinematics_mm, geometry_from_config
from planner import ArmPlan


class LEMHardwareController:
    def __init__(self, config: dict, simulate: bool = True):
        self.config = config
        self.simulate = simulate
        self.geometry = geometry_from_config(config)
        self.joint_config = config["hardware"]["arm"]["joints"]
        self.current_joints = {
            joint_name: float(joint["home_angle"])
            for joint_name, joint in self.joint_config.items()
        }
        self.current_color = {
            channel: int(value)
            for channel, value in config["hardware"]["lemp"]["default_color"].items()
        }
        self._serial_connection: serial.Serial | None = None

    def apply_plan(self, plan: ArmPlan) -> dict:
        clamped_joints = {
            joint_name: self._clamp_angle(joint_name, angle)
            for joint_name, angle in plan.joints.items()
        }
        scaled_color = self._scale_color(plan.color, plan.brightness)
        pose_xyz = forward_kinematics_mm(clamped_joints, self.geometry)

        payload = {
            "topic": self.config["hardware"]["arm"]["controller"]["command_topic"],
            "duration_ms": int(plan.duration_ms),
            "joints": [
                {
                    "name": joint_name,
                    "servo_id": int(self.joint_config[joint_name]["servo_id"]),
                    "angle_deg": round(clamped_joints[joint_name], 2),
                }
                for joint_name in self.joint_config
            ],
            "lemp": {
                "rgb": scaled_color,
                "brightness": round(plan.brightness, 3),
                "pins": self.config["hardware"]["lemp"]["pins"],
            },
            "pose_preview_mm": pose_xyz,
        }

        self._emit(payload)

        self.current_joints = clamped_joints
        self.current_color = scaled_color

        return payload

    def close(self) -> None:
        if self._serial_connection is not None and self._serial_connection.is_open:
            self._serial_connection.close()

    def _emit(self, payload: dict) -> None:
        if self.simulate:
            print("SIMULATED_SERIAL_PAYLOAD")
            print(json.dumps(payload, indent=2, sort_keys=True))
            return

        connection = self._get_serial_connection()
        connection.write((json.dumps(payload) + "\n").encode("utf-8"))
        connection.flush()
        print("SERIAL_WRITE_OK")
        print(json.dumps(payload, indent=2, sort_keys=True))

    def _get_serial_connection(self) -> serial.Serial:
        if self._serial_connection is None or not self._serial_connection.is_open:
            serial_config = self.config["hardware"]["arm"]["serial"]
            self._serial_connection = serial.Serial(
                port=serial_config["port"],
                baudrate=int(serial_config["baud_rate"]),
                timeout=float(serial_config.get("timeout_s", 1.0)),
            )
        return self._serial_connection

    def _clamp_angle(self, joint_name: str, angle: float) -> float:
        limits = self.joint_config[joint_name]
        return max(float(limits["min_angle"]), min(float(limits["max_angle"]), float(angle)))

    @staticmethod
    def _scale_color(color: dict[str, int], brightness: float) -> dict[str, int]:
        brightness = max(0.0, min(1.0, float(brightness)))
        return {
            channel: max(0, min(255, int(round(int(value) * brightness))))
            for channel, value in color.items()
        }

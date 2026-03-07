from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sin


@dataclass(frozen=True)
class ArmGeometry:
    base_height: float
    upper_arm_length: float
    forearm_length: float
    wrist_to_lemp: float
    tool_vertical_offset: float = 0.0


def geometry_from_config(config: dict) -> ArmGeometry:
    geometry = config["hardware"]["arm"]["geometry"]
    return ArmGeometry(
        base_height=float(geometry["base_height"]),
        upper_arm_length=float(geometry["upper_arm_length"]),
        forearm_length=float(geometry["forearm_length"]),
        wrist_to_lemp=float(geometry["wrist_to_lemp"]),
        tool_vertical_offset=float(geometry.get("tool_vertical_offset", 0.0)),
    )


def forward_kinematics_mm(joint_angles: dict[str, float], geometry: ArmGeometry) -> dict[str, float]:
    base = radians(float(joint_angles.get("base_yaw", 90.0)) - 90.0)
    shoulder = radians(float(joint_angles.get("shoulder_pitch", 90.0)) - 90.0)
    elbow = radians(float(joint_angles.get("elbow", 90.0)) - 90.0)
    wrist = radians(float(joint_angles.get("wrist_pitch", 90.0)) - 90.0)
    wrist_roll = float(joint_angles.get("wrist_roll", 90.0))

    shoulder_line = shoulder
    elbow_line = shoulder_line + elbow
    wrist_line = elbow_line + wrist

    radial = (
        geometry.upper_arm_length * cos(shoulder_line)
        + geometry.forearm_length * cos(elbow_line)
        + geometry.wrist_to_lemp * cos(wrist_line)
    )
    height = (
        geometry.base_height
        + geometry.upper_arm_length * sin(shoulder_line)
        + geometry.forearm_length * sin(elbow_line)
        + geometry.wrist_to_lemp * sin(wrist_line)
        + geometry.tool_vertical_offset
    )

    return {
        "x": round(radial * cos(base), 2),
        "y": round(radial * sin(base), 2),
        "z": round(height, 2),
        "tool_roll_deg": round(wrist_roll, 2),
    }

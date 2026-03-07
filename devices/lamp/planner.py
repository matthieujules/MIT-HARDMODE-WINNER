from __future__ import annotations

from dataclasses import dataclass, field
import re


COLOR_MAP = {
    "red": {"r": 255, "g": 0, "b": 0},
    "green": {"r": 0, "g": 255, "b": 0},
    "blue": {"r": 0, "g": 80, "b": 255},
    "white": {"r": 255, "g": 255, "b": 255},
    "warm white": {"r": 255, "g": 180, "b": 120},
    "orange": {"r": 255, "g": 120, "b": 0},
    "yellow": {"r": 255, "g": 200, "b": 0},
    "purple": {"r": 160, "g": 60, "b": 255},
    "pink": {"r": 255, "g": 70, "b": 160},
    "cyan": {"r": 0, "g": 255, "b": 255},
}

JOINT_ALIASES = {
    "base": "base_yaw",
    "base_yaw": "base_yaw",
    "yaw": "base_yaw",
    "shoulder": "shoulder_pitch",
    "shoulder_pitch": "shoulder_pitch",
    "pitch": "wrist_pitch",
    "elbow": "elbow",
    "wrist": "wrist_pitch",
    "wrist_pitch": "wrist_pitch",
    "wristpitch": "wrist_pitch",
    "roll": "wrist_roll",
    "wrist_roll": "wrist_roll",
    "wristroll": "wrist_roll",
}

PRESET_HINTS = {
    "focus": "focus",
    "lock in": "focus",
    "relax": "relax",
    "calm": "relax",
    "alert": "alert",
    "warning": "alert",
    "curious": "curious",
    "inspect": "curious",
    "home": "home",
    "reset": "home",
    "look at user": "curious",
    "look_at_user": "curious",
}


@dataclass
class ArmPlan:
    raw_instruction: str
    joints: dict[str, float]
    color: dict[str, int]
    brightness: float
    duration_ms: int
    light_frames: list[tuple[int, int, int, int]] = field(default_factory=list)
    preset: str | None = None
    notes: list[str] = field(default_factory=list)


class InstructionPlanner:
    def __init__(self, config: dict):
        self.config = config
        self.presets = config.get("presets", {})
        self.joint_names = tuple(config["hardware"]["arm"]["joints"].keys())

    def plan(self, instruction: str, current_joints: dict[str, float], current_color: dict[str, int]) -> ArmPlan:
        text = instruction.strip()
        lowered = text.lower()

        preset_name = self._detect_preset(lowered)
        notes: list[str] = []

        base_joints = dict(current_joints)
        base_color = dict(current_color)
        duration_ms = 1000

        if preset_name:
            preset = self.presets[preset_name]
            base_joints.update({name: float(value) for name, value in preset["joints"].items()})
            base_color.update({channel: int(value) for channel, value in preset["color"].items()})
            duration_ms = int(preset.get("duration_ms", duration_ms))
            notes.append(f"matched preset '{preset_name}'")

        explicit_joints = self._parse_joint_angles(lowered)
        if explicit_joints:
            base_joints.update(explicit_joints)
            notes.append("applied explicit joint angles from instruction")

        explicit_color = self._parse_color(lowered)
        if explicit_color:
            base_color.update(explicit_color)
            notes.append("applied explicit RGB/color override")

        explicit_frames = self._parse_light_frames(lowered)
        if explicit_frames:
            notes.append("applied explicit RGB frame animation")

        brightness = self._parse_brightness(lowered)
        if brightness is None:
            brightness = float(self.config["hardware"]["lemp"].get("brightness_scale", 1.0))
        else:
            notes.append("applied explicit brightness override")

        explicit_duration = self._parse_duration_ms(lowered)
        if explicit_duration is not None:
            duration_ms = explicit_duration
            notes.append("applied explicit duration override")

        if not notes:
            notes.append("no strong keyword matched, using current pose/light state")

        return ArmPlan(
            raw_instruction=text,
            joints=base_joints,
            color=base_color,
            light_frames=explicit_frames,
            brightness=brightness,
            duration_ms=duration_ms,
            preset=preset_name,
            notes=notes,
        )

    def _detect_preset(self, lowered: str) -> str | None:
        for hint, preset_name in PRESET_HINTS.items():
            if hint in lowered and preset_name in self.presets:
                return preset_name
        return None

    def _parse_joint_angles(self, lowered: str) -> dict[str, float]:
        matches: dict[str, float] = {}
        pattern = re.compile(
            r"\b(base_yaw|base|yaw|shoulder|shoulder_pitch|elbow|wrist|wrist_pitch|wristpitch|roll|wrist_roll|wristroll|pitch)\s*(?:to|at|=|:)?\s*(-?\d+(?:\.\d+)?)\b"
        )
        for alias, value in pattern.findall(lowered):
            matches[JOINT_ALIASES[alias]] = float(value)
        return matches

    def _parse_color(self, lowered: str) -> dict[str, int] | None:
        rgb_match = re.search(
            r"\brgb\s*\(?\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*\)?",
            lowered,
        )
        if rgb_match:
            return {
                "r": self._clamp_rgb(int(rgb_match.group(1))),
                "g": self._clamp_rgb(int(rgb_match.group(2))),
                "b": self._clamp_rgb(int(rgb_match.group(3))),
            }

        for color_name in sorted(COLOR_MAP, key=len, reverse=True):
            if color_name in lowered:
                return dict(COLOR_MAP[color_name])
        return None

    def _parse_brightness(self, lowered: str) -> float | None:
        scalar_match = re.search(r"\bbrightness\s*(?:to|at|=|:)?\s*(0(?:\.\d+)?|1(?:\.0+)?)\b", lowered)
        if scalar_match:
            return max(0.0, min(1.0, float(scalar_match.group(1))))

        percent_match = re.search(r"\bbrightness\s*(?:to|at|=|:)?\s*(\d{1,3})\b", lowered)
        if percent_match:
            percent = max(0.0, min(100.0, float(percent_match.group(1))))
            return round(percent / 100.0, 3)
        return None

    def _parse_light_frames(self, lowered: str) -> list[tuple[int, int, int, int]]:
        matches = re.findall(
            r"(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,5})",
            lowered,
        )
        frames: list[tuple[int, int, int, int]] = []
        for r_value, g_value, b_value, t_value in matches:
            frames.append(
                (
                    self._clamp_rgb(int(r_value)),
                    self._clamp_rgb(int(g_value)),
                    self._clamp_rgb(int(b_value)),
                    max(0, int(t_value)),
                )
            )
        return frames

    def _parse_duration_ms(self, lowered: str) -> int | None:
        seconds_match = re.search(r"\b(?:duration|over|for)\s*(\d+(?:\.\d+)?)\s*s(?:ec(?:ond)?s?)?\b", lowered)
        if seconds_match:
            return max(100, int(float(seconds_match.group(1)) * 1000))

        ms_match = re.search(r"\b(?:duration|over|for)\s*(\d{2,5})\s*ms\b", lowered)
        if ms_match:
            return max(100, int(ms_match.group(1)))
        return None

    @staticmethod
    def _clamp_rgb(value: int) -> int:
        return max(0, min(255, value))

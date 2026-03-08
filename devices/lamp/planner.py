"""Regex planner for Layer 1 direct commands.

The InstructionPlanner is used by ws_client.py for direct (non-LLM) command
handling. The agent.py now handles all LLM-based decisions directly.
"""

from __future__ import annotations

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

# Map text hints to pose names (used by Layer 1 planner)
POSE_HINTS = {
    "focus": "home",      # will be overridden if pose exists
    "lock in": "home",
    "relax": "home",
    "calm": "home",
    "alert": "home",
    "warning": "home",
    "curious": "look_at_user",
    "inspect": "look_at_user",
    "home": "home",
    "reset": "home",
    "look at user": "look_at_user",
    "look_at_user": "look_at_user",
}


class InstructionPlanner:
    """Regex-based planner for Layer 1 direct commands.

    Used by ws_client.py handle_command() for quick non-LLM responses.
    """

    def __init__(self, config: dict):
        self.config = config

    def detect_pose(self, text: str) -> str | None:
        """Detect a pose name from text. Returns pose name or None."""
        lowered = text.strip().lower()
        for hint, pose_name in POSE_HINTS.items():
            if hint in lowered:
                return pose_name
        return None

    def parse_color(self, text: str) -> dict[str, int] | None:
        """Parse a color from text. Returns {r, g, b} or None."""
        lowered = text.strip().lower()

        # Try RGB(r, g, b) format
        rgb_match = re.search(
            r"\brgb\s*\(?\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*\)?",
            lowered,
        )
        if rgb_match:
            return {
                "r": _clamp_rgb(int(rgb_match.group(1))),
                "g": _clamp_rgb(int(rgb_match.group(2))),
                "b": _clamp_rgb(int(rgb_match.group(3))),
            }

        # Try named colors
        for color_name in sorted(COLOR_MAP, key=len, reverse=True):
            if color_name in lowered:
                return dict(COLOR_MAP[color_name])
        return None

    def parse_brightness(self, text: str) -> float | None:
        """Parse brightness from text. Returns 0.0-1.0 or None."""
        lowered = text.strip().lower()
        scalar_match = re.search(r"\bbrightness\s*(?:to|at|=|:)?\s*(0(?:\.\d+)?|1(?:\.0+)?)\b", lowered)
        if scalar_match:
            return max(0.0, min(1.0, float(scalar_match.group(1))))

        percent_match = re.search(r"\bbrightness\s*(?:to|at|=|:)?\s*(\d{1,3})\b", lowered)
        if percent_match:
            percent = max(0.0, min(100.0, float(percent_match.group(1))))
            return round(percent / 100.0, 3)
        return None


def _clamp_rgb(value: int) -> int:
    return max(0, min(255, value))

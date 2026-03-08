"""Regex planner for Layer 1 direct commands.

Used by ws_client.py for non-LLM command handling.
"""

from __future__ import annotations

import re


def plan_command(action: str, params: dict) -> dict | None:
    """Try to match a direct command action. Returns result dict or None."""
    action_lower = action.strip().lower()

    if action_lower in ("stop", "quiet", "silence", "pause"):
        return {"action": "stop", "params": {}}

    if action_lower in ("play", "play_music", "speak"):
        return {"action": "play", "params": params}

    if action_lower in ("spin_dial", "turn_dial"):
        return {"action": "spin_dial", "params": params}

    return None


def plan_from_text(text: str) -> dict | None:
    """Parse freeform text into a command. Returns dict or None."""
    lowered = text.strip().lower()

    if re.search(r"\b(stop|quiet|silence|shut\s*up|pause)\b", lowered):
        return {"action": "stop", "params": {}}

    return None

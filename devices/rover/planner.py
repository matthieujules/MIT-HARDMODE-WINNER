"""Regex planner for Layer 1 direct commands.

Used by ws_client.py for quick non-LLM command routing.
Returns a dict with action + params, or None if no regex match.
"""

from __future__ import annotations

import re
from typing import Any


def plan_command(action: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Try to plan a direct command from action + params.

    Returns a dict {"action": ..., "params": ...} if matched,
    or None to fall through to the default handler.
    """
    # If the action is already a known rover action, pass through
    if action in ("move", "rotate", "stop", "emote"):
        return None  # let ws_client handle directly

    # Try to interpret text-based actions
    text = action.lower().strip()

    # Stop patterns
    if text in ("stop", "halt", "freeze", "brake"):
        return {"action": "stop", "params": {}}

    # Move patterns: "move forward 30cm", "go forward", "back up"
    move_match = re.search(
        r"(?:move|go|drive)\s+(?:forward|ahead|straight)\s*(\d+)?",
        text,
    )
    if move_match:
        cm = float(move_match.group(1)) if move_match.group(1) else 20
        return {"action": "move", "params": {"distance_cm": cm, "speed": 40}}

    back_match = re.search(
        r"(?:move|go|drive|back)\s*(?:backward|back|reverse|up)\s*(\d+)?",
        text,
    )
    if back_match:
        cm = float(back_match.group(1)) if back_match.group(1) else 20
        return {"action": "move", "params": {"distance_cm": -cm, "speed": 40}}

    # Rotate patterns: "turn left 90", "rotate right", "spin"
    turn_match = re.search(
        r"(?:turn|rotate)\s+(?:left|ccw|counter)\s*(\d+)?",
        text,
    )
    if turn_match:
        deg = float(turn_match.group(1)) if turn_match.group(1) else 90
        return {"action": "rotate", "params": {"degrees": -deg, "speed": 40}}

    turn_right_match = re.search(
        r"(?:turn|rotate)\s+(?:right|cw|clockwise)\s*(\d+)?",
        text,
    )
    if turn_right_match:
        deg = float(turn_right_match.group(1)) if turn_right_match.group(1) else 90
        return {"action": "rotate", "params": {"degrees": deg, "speed": 40}}

    # Emote patterns
    if text in ("spin", "excited", "excitement", "happy", "celebrate"):
        return {"action": "emote", "params": {"emotion": "excitement"}}

    if text in ("sad", "unhappy", "mope"):
        return {"action": "emote", "params": {"emotion": "sad"}}

    if text in ("ponder", "think", "hmm", "wonder"):
        return {"action": "emote", "params": {"emotion": "ponder"}}

    if text in ("deliver", "food", "pass", "serve", "fetch"):
        return {"action": "emote", "params": {"emotion": "deliver"}}

    # Go home / return
    if text in ("go home", "return", "return home", "come back"):
        return {"action": "emote", "params": {"emotion": "deliver"}}

    return None


def plan_from_text(text: str) -> dict[str, Any] | None:
    """Parse freeform text into a rover command.

    Used for Layer 1 direct text interpretation.
    Returns a dict {"action": ..., "params": ...} or None.
    """
    return plan_command(text, {})

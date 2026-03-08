"""Transcript safety, voice lock, and direct command routing."""

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Emergency stop ────────────────────────────────────────────────

EMERGENCY_STOP_PATTERN = re.compile(
    r"\b(stop stop|stop all|emergency stop|freeze all|no\s+no\s+no)\b",
    re.IGNORECASE,
)


def is_emergency_stop(text: str) -> bool:
    """Return True if transcript matches an emergency stop pattern."""
    return bool(EMERGENCY_STOP_PATTERN.search(text))


# ── Direct commands (bypass master reasoning) ────────────────────

_DIRECT_COMMANDS: list[tuple[re.Pattern, str, str, dict[str, Any]]] = [
    # Radio: stop music
    (re.compile(r"\b(stop|turn off|cut|kill)\b.{0,10}\b(music|song|radio|audio|playing)\b", re.I),
     "radio", "stop", {}),
    (re.compile(r"\b(music|song|radio|audio)\b.{0,10}\b(off|stop)\b", re.I),
     "radio", "stop", {}),
    (re.compile(r"\b(quiet|silence|shut\s*up|hush)\b", re.I),
     "radio", "stop", {}),

    # Lamp: lights off
    (re.compile(r"\b(turn off|switch off|kill)\b.{0,10}\b(lights?|lamp)\b", re.I),
     "lamp", "set_brightness", {"brightness": 0}),
    (re.compile(r"\b(lights?|lamp)\b.{0,10}\b(off)\b", re.I),
     "lamp", "set_brightness", {"brightness": 0}),

    # Lamp: reset
    (re.compile(r"\b(lamp|light)\s*(reset|home)\b", re.I),
     "lamp", "reset_pose", {}),
    (re.compile(r"\breset\b.{0,10}\b(lamp|light)\b", re.I),
     "lamp", "reset_pose", {}),

    # Mirror: screen off
    (re.compile(r"\b(turn off|switch off|hide|dismiss|clear)\b.{0,10}\b(screen|mirror|display)\b", re.I),
     "mirror", "stop", {}),
    (re.compile(r"\b(screen|mirror|display)\b.{0,10}\b(off|hide|dismiss|clear)\b", re.I),
     "mirror", "stop", {}),

    # Rover: stop
    (re.compile(r"\b(rover|car|robot)\b.{0,10}\b(stop|halt|freeze)\b", re.I),
     "rover", "stop", {}),
    (re.compile(r"\b(stop|halt|freeze)\b.{0,10}\b(rover|car|robot)\b", re.I),
     "rover", "stop", {}),
]


def match_direct_command(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """Match transcript to a direct device command. Returns (device_id, action, params) or None."""
    for pattern, device_id, action, params in _DIRECT_COMMANDS:
        if pattern.search(text):
            logger.info("Direct command matched: %s -> %s(%s)", text[:60], device_id, action)
            return (device_id, action, params)
    return None

# ── Voice lock ────────────────────────────────────────────────────

_VOICE_LOCK_TIMEOUT_S = 10


def check_voice_lock(state_manager) -> bool:
    """Return True if any device is currently speaking (voice lock active)."""
    state = state_manager.read_state()
    voice_lock = state.get("voice_lock", {})
    now = time.time()
    for device_id, lock_info in voice_lock.items():
        if lock_info.get("is_speaking"):
            locked_at = lock_info.get("locked_at", 0)
            if now - locked_at < _VOICE_LOCK_TIMEOUT_S:
                return True
            # Timeout expired — clear stale lock
            logger.info("Voice lock for %s expired (timeout), clearing", device_id)
            clear_voice_lock(device_id, state_manager)
    return False


def set_voice_lock(device_id: str, state_manager) -> None:
    """Mark a device as currently speaking."""
    state = state_manager.read_state()
    voice_lock = state.get("voice_lock", {})
    voice_lock[device_id] = {"is_speaking": True, "locked_at": time.time()}
    state_manager.write_state({"voice_lock": voice_lock})
    logger.info("Voice lock set for %s", device_id)


def clear_voice_lock(device_id: str, state_manager) -> None:
    """Clear the speaking flag for a device.

    Note: we set is_speaking=False rather than deleting the key, because
    write_state uses deep_merge which cannot remove keys from nested dicts.
    """
    state_manager.write_state({
        "voice_lock": {device_id: {"is_speaking": False}}
    })
    logger.info("Voice lock cleared for %s", device_id)

"""Transcript safety and voice lock helpers."""

import logging
import re
import time

logger = logging.getLogger(__name__)

# ── Emergency stop ────────────────────────────────────────────────

EMERGENCY_STOP_PATTERN = re.compile(
    r"\b(stop stop|stop all|emergency stop|freeze all|no\s+no\s+no)\b",
    re.IGNORECASE,
)


def is_emergency_stop(text: str) -> bool:
    """Return True if transcript matches an emergency stop pattern."""
    return bool(EMERGENCY_STOP_PATTERN.search(text))

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
    """Clear the speaking flag for a device."""
    state = state_manager.read_state()
    voice_lock = state.get("voice_lock", {})
    if device_id in voice_lock:
        del voice_lock[device_id]
        state_manager.write_state({"voice_lock": voice_lock})
        logger.info("Voice lock cleared for %s", device_id)

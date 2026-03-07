"""Deterministic command router with emergency stop and voice lock."""

import logging
import re
import time

logger = logging.getLogger(__name__)

# ── Emergency stop ────────────────────────────────────────────────

EMERGENCY_STOP_PATTERN = re.compile(
    r"\b(stop|halt|freeze|wait|no\s*no\s*no)\b", re.IGNORECASE
)


def is_emergency_stop(text: str) -> bool:
    """Return True if transcript matches an emergency stop pattern."""
    return bool(EMERGENCY_STOP_PATTERN.search(text))


# ── Deterministic patterns ────────────────────────────────────────
# Each entry: (compiled_regex, device_id, action, param_extractor)
# param_extractor is a callable(match) -> dict

DETERMINISTIC_PATTERNS: list[tuple[re.Pattern, str, str, callable]] = [
    # Lamp color commands
    (
        re.compile(r"\blamp\s+(red|green|blue|white|yellow|orange|purple|pink|cyan|warm|cool)\b", re.IGNORECASE),
        "lamp",
        "set_color",
        lambda m: {"color": m.group(1).lower()},
    ),
    # Lamp brightness
    (
        re.compile(r"\blamp\s+brightness\s+(\d+)\b", re.IGNORECASE),
        "lamp",
        "set_brightness",
        lambda m: {"brightness": int(m.group(1))},
    ),
    # Lights off
    (
        re.compile(r"\b(?:lights?\s+off|turn\s+off\s+(?:the\s+)?lights?)\b", re.IGNORECASE),
        "lamp",
        "set_brightness",
        lambda _: {"brightness": 0},
    ),
    # Mirror tilt
    (
        re.compile(r"\bmirror\s+tilt\s+(up|down)\b", re.IGNORECASE),
        "mirror",
        "tilt",
        lambda m: {"direction": m.group(1).lower()},
    ),
    # Rover stop (also caught by emergency stop, but explicit here)
    (
        re.compile(r"\brover\s+stop\b", re.IGNORECASE),
        "rover",
        "stop",
        lambda _: {},
    ),
    # Rover drive commands
    (
        re.compile(r"\brover\s+(?:go|drive|move)\s+(forward|backward|left|right|home)\b", re.IGNORECASE),
        "rover",
        "drive_to",
        lambda m: {"direction": m.group(1).lower()},
    ),
    # Radio volume
    (
        re.compile(r"\b(?:radio\s+)?volume\s+(up|down|\d+)\b", re.IGNORECASE),
        "radio",
        "set_volume",
        lambda m: {"level": m.group(1).lower()},
    ),
]


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


# ── Main router ───────────────────────────────────────────────────


def route_transcript(text: str, state_manager) -> tuple:
    """Route a transcript through the deterministic pipeline.

    Processing order (per spec §8):
      1. Emergency stop check
      2. Voice lock filter
      3. Deterministic regex match
      4. Fallback to master reasoning

    Returns one of:
      ("emergency_stop",)
      ("dropped",)
      ("deterministic", device_id, action, params_dict)
      ("master",)
    """
    # 1. Emergency stop — bypasses voice lock
    if is_emergency_stop(text):
        logger.info("Emergency stop triggered: %r", text)
        return ("emergency_stop",)

    # 2. Voice lock — drop non-emergency transcripts while speaking
    if check_voice_lock(state_manager):
        logger.info("Transcript dropped (voice lock active): %r", text)
        return ("dropped",)

    # 3. Deterministic regex matching
    for pattern, device_id, action, param_extractor in DETERMINISTIC_PATTERNS:
        match = pattern.search(text)
        if match:
            params = param_extractor(match)
            logger.info(
                "Deterministic match: %r -> %s.%s(%s)", text, device_id, action, params
            )
            return ("deterministic", device_id, action, params)

    # 4. Fallback to master reasoning
    logger.info("No deterministic match, routing to master: %r", text)
    return ("master",)

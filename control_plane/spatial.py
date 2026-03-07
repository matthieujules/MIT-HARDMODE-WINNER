"""Spatial state management for the 2D room map."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge patch into base. Returns modified base."""
    for key, value in patch.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_room_config(path: str = "data/room.json") -> dict:
    """Load and return the static room configuration."""
    p = Path(path)
    if not p.exists():
        logger.warning("Room config not found: %s", path)
        return {}
    return json.loads(p.read_text())


def init_spatial_state(room_config: dict) -> dict:
    """Create initial spatial state from room config anchors.

    Returns the spatial dict to be stored in state.json.
    """
    if not room_config:
        return {}

    devices = {}

    # Stationary devices from anchors
    for device_id, anchor in room_config.get("anchors", {}).items():
        devices[device_id] = {
            "x_cm": anchor["x_cm"],
            "y_cm": anchor["y_cm"],
            "theta_deg": anchor.get("theta_deg", 0),
            "fixed": True,
            "source": "room_config",
            "status": "idle",
        }

    # Rover starts at dock waypoint
    waypoints = room_config.get("waypoints", [])
    dock = next((wp for wp in waypoints if wp["id"] == "dock"), None)
    if dock:
        devices["rover"] = {
            "x_cm": dock["x_cm"],
            "y_cm": dock["y_cm"],
            "theta_deg": 0,
            "fixed": False,
            "source": "room_config",
            "status": "idle",
            "motion": None,
        }

    # User position
    user_pos = room_config.get("user_default_position", {})
    user = {
        "x_cm": user_pos.get("x_cm", 250),
        "y_cm": user_pos.get("y_cm", 200),
        "label": user_pos.get("label", "User"),
    }

    return {
        "devices": devices,
        "user": user,
    }


def resolve_target(target: dict, room_config: dict) -> tuple[float, float]:
    """Resolve a target dict (waypoint name or explicit coords) to (x_cm, y_cm).

    target can be {"waypoint": "desk"} or {"x_cm": 300, "y_cm": 150}
    """
    if "waypoint" in target:
        wp_name = target["waypoint"]
        for wp in room_config.get("waypoints", []):
            if wp["id"] == wp_name:
                return (wp["x_cm"], wp["y_cm"])
        logger.warning("Unknown waypoint: %s, returning room center", wp_name)
        return (room_config.get("width_cm", 500) / 2, room_config.get("height_cm", 400) / 2)

    if "x_cm" in target and "y_cm" in target:
        return (target["x_cm"], target["y_cm"])

    logger.warning("Invalid target format: %s", target)
    return (room_config.get("width_cm", 500) / 2, room_config.get("height_cm", 400) / 2)


def clamp_to_room(x: float, y: float, room_config: dict) -> tuple[float, float]:
    """Clamp coordinates to room bounds."""
    w = room_config.get("width_cm", 500)
    h = room_config.get("height_cm", 400)
    return (max(0, min(x, w)), max(0, min(y, h)))


def update_device_activity(spatial: dict, device_id: str, status: str) -> dict:
    """Update a device's activity status (idle, executing, speaking, etc.)"""
    devices = spatial.get("devices", {})
    if device_id in devices:
        devices[device_id]["status"] = status
    return spatial

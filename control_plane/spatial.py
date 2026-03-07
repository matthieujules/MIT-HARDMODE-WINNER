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


def default_people_from_room(room_config: dict) -> list[dict]:
    """Return default person positions from room config."""
    templates = room_config.get("people_default_positions", [])
    people: list[dict] = []

    for index, template in enumerate(templates):
        x_cm = template.get("x_cm")
        y_cm = template.get("y_cm")
        if x_cm is None or y_cm is None:
            continue
        people.append({
            "id": template.get("id", f"person_{index + 1}"),
            "label": template.get("label", f"Person {index + 1}"),
            "role": template.get("role", "guest" if index > 0 else "primary"),
            "x_cm": x_cm,
            "y_cm": y_cm,
            "source": template.get("source", "room_config"),
        })

    if people:
        return people

    user_pos = room_config.get("user_default_position", {})
    return [{
        "id": "sally",
        "label": user_pos.get("label", "Sally"),
        "role": "primary",
        "x_cm": user_pos.get("x_cm", 250),
        "y_cm": user_pos.get("y_cm", 200),
        "source": "room_config",
    }]


def primary_user_from_people(people: list[dict]) -> dict:
    """Return legacy single-user alias from a people list."""
    if not people:
        return {}

    primary = next((person for person in people if person.get("role") == "primary"), people[0])
    return {
        "x_cm": primary.get("x_cm"),
        "y_cm": primary.get("y_cm"),
        "label": primary.get("label", "User"),
        "source": primary.get("source", "derived_people"),
    }


def normalize_spatial_state(spatial: dict | None, room_config: dict) -> dict:
    """Backfill newer spatial fields into existing persisted state."""
    normalized = dict(spatial or {})
    defaults = init_spatial_state(room_config) if room_config else {}

    devices = dict(defaults.get("devices", {}))
    devices.update(normalized.get("devices", {}))
    if devices:
        normalized["devices"] = devices

    if "people" not in normalized or not normalized.get("people"):
        legacy_user = normalized.get("user")
        if legacy_user and legacy_user.get("x_cm") is not None and legacy_user.get("y_cm") is not None:
            normalized["people"] = [{
                "id": "sally",
                "label": legacy_user.get("label", "Sally"),
                "role": "primary",
                "x_cm": legacy_user.get("x_cm"),
                "y_cm": legacy_user.get("y_cm"),
                "source": legacy_user.get("source", "legacy_user"),
            }]
        else:
            normalized["people"] = defaults.get("people", [])

    if "user" not in normalized or not normalized.get("user"):
        normalized["user"] = primary_user_from_people(normalized.get("people", []))

    return normalized


def merge_people_observations(previous_people: list[dict], observations: list[dict], room_config: dict) -> list[dict]:
    """Convert camera observations into stable person slots."""
    if not room_config:
        room_config = {}

    candidates = []
    for observation in observations:
        if not isinstance(observation, dict):
            continue

        if "x_cm" in observation and "y_cm" in observation:
            x_cm = observation["x_cm"]
            y_cm = observation["y_cm"]
        elif "x_pct" in observation and "y_pct" in observation:
            x_cm = observation["x_pct"] / 100.0 * room_config.get("width_cm", 500)
            y_cm = observation["y_pct"] / 100.0 * room_config.get("height_cm", 400)
        else:
            continue

        x_cm, y_cm = clamp_to_room(x_cm, y_cm, room_config)
        candidates.append({
            "x_cm": round(x_cm),
            "y_cm": round(y_cm),
            "confidence": observation.get("confidence"),
            "description": observation.get("description") or observation.get("label"),
            "source": observation.get("source", "camera"),
        })

    if not candidates:
        return []

    previous = [
        person for person in (previous_people or [])
        if person.get("x_cm") is not None and person.get("y_cm") is not None
    ]

    results: list[dict] = []
    unmatched_prev = set(range(len(previous)))
    unmatched_candidates = set(range(len(candidates)))

    while unmatched_prev and unmatched_candidates:
        best_pair = None
        best_distance = None

        for prev_index in unmatched_prev:
            prev = previous[prev_index]
            for candidate_index in unmatched_candidates:
                candidate = candidates[candidate_index]
                distance = (
                    (prev.get("x_cm", 0) - candidate["x_cm"]) ** 2
                    + (prev.get("y_cm", 0) - candidate["y_cm"]) ** 2
                )
                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_pair = (prev_index, candidate_index)

        if best_pair is None:
            break

        prev_index, candidate_index = best_pair
        merged = dict(previous[prev_index])
        merged.update(candidates[candidate_index])
        results.append(merged)
        unmatched_prev.remove(prev_index)
        unmatched_candidates.remove(candidate_index)

    templates = room_config.get("people_templates") or default_people_from_room(room_config)
    used_ids = {person.get("id") for person in results}

    for candidate_index in sorted(unmatched_candidates):
        candidate = candidates[candidate_index]
        template = next((item for item in templates if item.get("id") not in used_ids), None)

        if template:
            person = {
                "id": template.get("id"),
                "label": template.get("label", "Guest"),
                "role": template.get("role", "guest"),
                **candidate,
            }
        else:
            guest_count = 1 + sum(
                1 for person in results
                if str(person.get("id", "")).startswith("guest_")
            )
            person = {
                "id": f"guest_{guest_count}",
                "label": f"Guest {guest_count}",
                "role": "guest",
                **candidate,
            }

        used_ids.add(person["id"])
        results.append(person)

    results.sort(key=lambda person: (0 if person.get("role") == "primary" else 1, person.get("id", "")))
    return results


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

    # Rover starts at configured waypoint (defaults to dock)
    waypoints = room_config.get("waypoints", [])
    rover_start_id = room_config.get("rover_default_waypoint", "dock")
    rover_start = next((wp for wp in waypoints if wp["id"] == rover_start_id), None)
    if rover_start:
        devices["rover"] = {
            "x_cm": rover_start["x_cm"],
            "y_cm": rover_start["y_cm"],
            "theta_deg": 0,
            "fixed": False,
            "source": "room_config",
            "status": "idle",
            "motion": None,
        }

    people = default_people_from_room(room_config)
    user = primary_user_from_people(people)

    return {
        "devices": devices,
        "people": people,
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

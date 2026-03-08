"""State persistence layer for the ClaudeHome control plane.

File-backed state using stdlib json. Thread-safe writes via threading.Lock.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .schemas import DeviceEvent, DeviceInfo, DeviceRegistration
from .spatial import deep_merge, init_spatial_state, load_room_config, normalize_spatial_state, primary_user_from_people

logger = logging.getLogger(__name__)


class StateManager:
    def __init__(self, data_dir: Path = Path("data")):
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self._state_path = self._data_dir / "state.json"
        self._devices_path = self._data_dir / "devices.json"
        self._event_log_path = self._data_dir / "event_log.jsonl"
        self._user_md_path = self._data_dir / "user.md"

        # Ensure files exist with defaults
        if not self._state_path.exists():
            self._state_path.write_text(json.dumps({
                "mode": "idle", "mood": "neutral", "energy": "normal",
                "people_count": 0, "activity": None, "overrides": {},
                "voice_lock": {},
            }))
        if not self._devices_path.exists():
            self._devices_path.write_text("[]")
        if not self._event_log_path.exists():
            self._event_log_path.touch()

        # Initialize / migrate spatial state from room config
        state = json.loads(self._state_path.read_text())
        room_config = load_room_config(str(self._data_dir / "room.json"))
        if room_config:
            original_spatial = state.get("spatial")
            state["spatial"] = normalize_spatial_state(original_spatial, room_config)
            if original_spatial != state["spatial"]:
                self._state_path.write_text(json.dumps(state, default=str))
                logger.info("Initialized/migrated spatial state from room config")

    # ── State (state.json) ─────────────────────────────────────────

    def read_state(self) -> dict:
        return json.loads(self._state_path.read_text())

    def write_state(self, patch: dict) -> None:
        with self._lock:
            state = json.loads(self._state_path.read_text())
            deep_merge(state, patch)
            self._state_path.write_text(json.dumps(state, default=str))
        logger.info("State updated: %s", list(patch.keys()))

    def read_room_config(self) -> dict:
        return load_room_config(str(self._data_dir / "room.json"))

    def update_spatial_device(self, device_id: str, patch: dict) -> None:
        """Update a single device's spatial state with a patch dict."""
        with self._lock:
            state = json.loads(self._state_path.read_text())
            spatial = state.get("spatial", {})
            if device_id == "user":
                user = spatial.get("user", {})
                user.update(patch)
                spatial["user"] = user
                people = spatial.get("people", [])
                if people:
                    people[0].update({
                        key: value for key, value in patch.items()
                        if key in {"x_cm", "y_cm", "label", "source"}
                    })
                    spatial["people"] = people
            else:
                devices = spatial.get("devices", {})
                if device_id in devices:
                    devices[device_id].update(patch)
                else:
                    devices[device_id] = patch
                spatial["devices"] = devices
            state["spatial"] = spatial
            self._state_path.write_text(json.dumps(state, default=str))
        logger.info("Spatial device updated: %s <- %s", device_id, list(patch.keys()))

    def update_spatial_people(self, people: list[dict]) -> None:
        """Replace tracked people positions and keep legacy user alias in sync."""
        normalized = []
        for index, person in enumerate(people):
            if person.get("x_cm") is None or person.get("y_cm") is None:
                continue
            normalized_person = dict(person)
            normalized_person.setdefault("id", f"person_{index + 1}")
            normalized_person.setdefault("label", f"User {index + 1}")
            normalized.append(normalized_person)

        with self._lock:
            state = json.loads(self._state_path.read_text())
            spatial = state.get("spatial", {})
            spatial["people"] = normalized
            spatial["user"] = primary_user_from_people(normalized)
            state["spatial"] = spatial
            state["people_count"] = len(normalized)
            self._state_path.write_text(json.dumps(state, default=str))
        logger.info("Spatial people updated: %d tracked", len(normalized))

    # ── Device health (last action result per device, in state.json) ──

    def update_device_health(self, device_id: str, status: str, detail: str) -> None:
        """Record the last action result for a device in state.json."""
        with self._lock:
            state = json.loads(self._state_path.read_text())
            health = state.get("device_health", {})
            health[device_id] = {
                "status": status,
                "detail": detail[:120],
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            state["device_health"] = health
            self._state_path.write_text(json.dumps(state, default=str))
        logger.info("Device health updated: %s -> %s", device_id, status)

    def read_device_health(self) -> dict:
        """Return the device_health dict from state.json."""
        state = json.loads(self._state_path.read_text())
        return state.get("device_health", {})

    # ── Devices (devices.json) ─────────────────────────────────────

    def read_devices(self) -> list[DeviceInfo]:
        raw = json.loads(self._devices_path.read_text())
        return [DeviceInfo(**d) for d in raw]

    def _write_devices(self, devices: list[DeviceInfo]) -> None:
        self._devices_path.write_text(
            json.dumps([d.model_dump(mode="json") for d in devices], default=str)
        )

    def register_device(self, reg: DeviceRegistration) -> str:
        with self._lock:
            devices = self.read_devices()
            for i, d in enumerate(devices):
                if d.device_id == reg.device_id:
                    devices[i] = DeviceInfo(
                        **reg.model_dump(),
                        status="online",
                        last_seen=datetime.now(timezone.utc),
                        is_virtual=d.is_virtual,
                    )
                    self._write_devices(devices)
                    logger.info("Device updated: %s", reg.device_id)
                    return "updated"
            devices.append(DeviceInfo(
                **reg.model_dump(),
                status="online",
                last_seen=datetime.now(timezone.utc),
            ))
            self._write_devices(devices)
            logger.info("Device registered: %s", reg.device_id)
            return "registered"

    def update_device_status(self, device_id: str, status: str) -> None:
        with self._lock:
            devices = self.read_devices()
            for d in devices:
                if d.device_id == device_id:
                    d.status = status
                    break
            self._write_devices(devices)

    def update_device_last_seen(self, device_id: str) -> None:
        with self._lock:
            devices = self.read_devices()
            for d in devices:
                if d.device_id == device_id:
                    d.last_seen = datetime.now(timezone.utc)
                    break
            self._write_devices(devices)

    def get_device(self, device_id: str) -> DeviceInfo | None:
        for d in self.read_devices():
            if d.device_id == device_id:
                return d
        return None

    # ── Event log (event_log.jsonl) ────────────────────────────────

    def append_event(self, event: DeviceEvent) -> None:
        with self._lock:
            with self._event_log_path.open("a") as f:
                f.write(json.dumps(event.model_dump(mode="json"), default=str) + "\n")
        logger.debug("Event logged: %s/%s", event.device_id, event.kind)

    def read_recent_events(self, max_chars: int = 8000, include_heartbeats: bool = False) -> list[dict]:
        if not self._event_log_path.exists():
            return []
        lines = self._event_log_path.read_text().strip().splitlines()
        result: list[dict] = []
        total_chars = 0
        for line in reversed(lines):
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if not include_heartbeats and parsed.get("kind") == "heartbeat":
                continue
            if total_chars + len(line) > max_chars:
                break
            result.append(parsed)
            total_chars += len(line)
        result.reverse()
        return result

    def compact_log(self, max_lines: int = 500) -> None:
        with self._lock:
            if not self._event_log_path.exists():
                return
            lines = self._event_log_path.read_text().strip().splitlines()
            if len(lines) <= max_lines:
                return
            kept = lines[-max_lines:]
            self._event_log_path.write_text("\n".join(kept) + "\n")
            logger.info("Event log compacted: %d -> %d lines", len(lines), max_lines)

    # ── Master log (master_log.jsonl) ─────────────────────────────

    def append_master_log(self, entry: dict) -> None:
        path = self._data_dir / "master_log.jsonl"
        with self._lock:
            with path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")

    def read_master_log(self, limit: int = 50) -> list[dict]:
        path = self._data_dir / "master_log.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().strip().splitlines()
        return [json.loads(l) for l in lines[-limit:]]

    # ── User profile ───────────────────────────────────────────────

    def read_user_md(self) -> str:
        if not self._user_md_path.exists():
            return ""
        text = self._user_md_path.read_text()
        return text[:8000]

    # ── SOUL.md reader ─────────────────────────────────────────────

    def read_soul(self, path: str) -> str:
        p = Path(path)
        if not p.exists():
            logger.warning("SOUL.md not found: %s", path)
            return ""
        text = p.read_text()
        return text[:4000]

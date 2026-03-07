"""State persistence layer for the ClaudeHome control plane.

File-backed state using stdlib json. Thread-safe writes via threading.Lock.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .schemas import DeviceEvent, DeviceInfo, DeviceRegistration

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

    # ── State (state.json) ─────────────────────────────────────────

    def read_state(self) -> dict:
        return json.loads(self._state_path.read_text())

    def write_state(self, patch: dict) -> None:
        with self._lock:
            state = json.loads(self._state_path.read_text())
            state.update(patch)
            self._state_path.write_text(json.dumps(state, default=str))
        logger.info("State updated: %s", list(patch.keys()))

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

    def read_recent_events(self, max_chars: int = 8000) -> list[dict]:
        if not self._event_log_path.exists():
            return []
        lines = self._event_log_path.read_text().strip().splitlines()
        result: list[dict] = []
        total_chars = 0
        for line in reversed(lines):
            if total_chars + len(line) > max_chars:
                break
            result.append(json.loads(line))
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

"""
In order to run live control, start this file server

then on ur laptop:
scp rover@100.97.253.17:~/Rover/teleop_client.py .
source ~/rover-teleop-venv/bin/activate
python3 teleop_client.py --url ws://100.97.253.17:8765

"""



#!/usr/bin/env python3
import asyncio
import json
import os
import threading
from typing import Any, Callable, Dict

import motion

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except Exception as e:
    raise SystemExit(
        "Missing dependency 'websockets'.\n"
        "On Raspberry Pi OS/Debian: sudo apt install python3-websockets\n"
        "Or create a venv: python3 -m venv .venv && .venv/bin/pip install websockets\n"
        f"Original error: {e}"
    )


_ACTIONS: Dict[str, Callable[..., Any]] = {
    "excitement": motion.excitement,
    "pass_food": motion.pass_food,
    "act_sad": motion.act_sad,
}

_EXEC_LOCK = threading.Lock()
_current_action: str | None = None

_drive_last_ts: float | None = None
_drive_timeout_s = 0.35
_drive_left = 0.0
_drive_right = 0.0


def _run_action_sync(action: str, args: dict) -> None:
    global _current_action
    fn = _ACTIONS[action]

    with _EXEC_LOCK:
        _current_action = action
        try:
            fn(**args)
        finally:
            try:
                motion.stop()
            finally:
                _current_action = None


async def _run_action(action: str, args: dict) -> None:
    await asyncio.to_thread(_run_action_sync, action, args)


def _run_teleop_sync(linear_cm: float, angular_deg: float, speed: int) -> None:
    global _current_action

    with _EXEC_LOCK:
        _current_action = "teleop"
        try:
            if linear_cm:
                motion.move(float(linear_cm), int(speed))
            if angular_deg:
                motion.rotate(float(angular_deg), int(speed))
        finally:
            try:
                motion.stop()
            finally:
                _current_action = None


async def _run_teleop(linear_cm: float, angular_deg: float, speed: int) -> None:
    await asyncio.to_thread(_run_teleop_sync, linear_cm, angular_deg, speed)


def _safe_args(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ValueError("args must be an object")


async def _handle_message(ws: WebSocketServerProtocol, raw: str) -> None:
    global _drive_last_ts, _drive_left, _drive_right, _current_action
    try:
        msg = json.loads(raw)
    except Exception:
        await ws.send(json.dumps({"ok": False, "error": "invalid_json"}))
        return

    msg_type = msg.get("type")
    req_id = msg.get("id")

    def resp(payload: dict) -> str:
        if req_id is not None:
            payload = {"id": req_id, **payload}
        return json.dumps(payload)

    if msg_type == "list":
        await ws.send(resp({"ok": True, "actions": sorted(_ACTIONS.keys()), "teleop": True, "drive": True}))
        return

    if msg_type == "status":
        await ws.send(resp({"ok": True, "busy": _current_action is not None, "current_action": _current_action}))
        return

    if msg_type == "stop":
        _drive_last_ts = None
        _drive_left = 0.0
        _drive_right = 0.0
        motion.stop()
        if _current_action == "drive":
            _current_action = None
            try:
                _EXEC_LOCK.release()
            except RuntimeError:
                pass
        await ws.send(resp({"ok": True}))
        return

    if msg_type == "drive":
        if _EXEC_LOCK.locked() and _current_action not in (None, "drive"):
            await ws.send(resp({"ok": False, "error": "busy"}))
            return

        if not _EXEC_LOCK.locked():
            if not _EXEC_LOCK.acquire(blocking=False):
                await ws.send(resp({"ok": False, "error": "busy"}))
                return
            _current_action = "drive"

        try:
            left = float(msg.get("left", 0.0) or 0.0)
            right = float(msg.get("right", 0.0) or 0.0)
        except Exception as e:
            await ws.send(resp({"ok": False, "error": str(e)}))
            return

        _drive_left = left
        _drive_right = right
        _drive_last_ts = asyncio.get_running_loop().time()

        motion.drive(_drive_left, _drive_right)
        await ws.send(resp({"ok": True}))
        return

    if msg_type == "run":
        action = msg.get("action")
        if action not in _ACTIONS:
            await ws.send(resp({"ok": False, "error": f"unknown_action: {action}"}))
            return

        try:
            args = _safe_args(msg.get("args"))
        except Exception as e:
            await ws.send(resp({"ok": False, "error": str(e)}))
            return

        if _EXEC_LOCK.locked():
            await ws.send(resp({"ok": False, "error": "busy"}))
            return

        await ws.send(resp({"ok": True, "started": True, "action": action}))
        try:
            await _run_action(action, args)
            await ws.send(resp({"ok": True, "finished": True, "action": action}))
        except Exception as e:
            await ws.send(resp({"ok": False, "error": str(e), "action": action}))
        return

    if msg_type == "teleop":
        if _EXEC_LOCK.locked():
            await ws.send(resp({"ok": False, "error": "busy"}))
            return

        try:
            linear_cm = float(msg.get("linear_cm", 0.0) or 0.0)
            angular_deg = float(msg.get("angular_deg", 0.0) or 0.0)
            speed = int(msg.get("speed", 40) or 40)
        except Exception as e:
            await ws.send(resp({"ok": False, "error": str(e)}))
            return

        await ws.send(resp({"ok": True, "started": True, "type": "teleop"}))
        try:
            await _run_teleop(linear_cm, angular_deg, speed)
            await ws.send(resp({"ok": True, "finished": True, "type": "teleop"}))
        except Exception as e:
            await ws.send(resp({"ok": False, "error": str(e), "type": "teleop"}))
        return

    await ws.send(resp({"ok": False, "error": "unknown_type"}))


async def _ws_main() -> None:
    host = os.environ.get("ROVER_HOST", "0.0.0.0")
    port = int(os.environ.get("ROVER_PORT", "8765"))

    async def drive_watchdog() -> None:
        global _drive_last_ts, _drive_left, _drive_right, _current_action
        while True:
            await asyncio.sleep(0.05)
            if _drive_last_ts is None:
                continue
            if (asyncio.get_running_loop().time() - _drive_last_ts) <= _drive_timeout_s:
                continue

            _drive_last_ts = None
            _drive_left = 0.0
            _drive_right = 0.0
            motion.stop()
            if _current_action == "drive":
                _current_action = None
                try:
                    _EXEC_LOCK.release()
                except RuntimeError:
                    pass

    async def handler(ws: WebSocketServerProtocol):
        async for message in ws:
            await _handle_message(ws, message)

    watchdog_task = asyncio.create_task(drive_watchdog())
    async with websockets.serve(handler, host, port):
        await asyncio.Future()
    watchdog_task.cancel()


def main() -> None:
    try:
        asyncio.run(_ws_main())
    finally:
        motion.cleanup()


if __name__ == "__main__":
    main()

"""FastAPI control plane for ClaudeHome.

Endpoints, WebSocket ConnectionManager, and event processing pipeline.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()  # Must load before importing master (reads MASTER_MODEL, MASTER_PROVIDER)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.websockets import WebSocketState

from .master import apply_state_update, execute_master_turn, extract_device_instructions, extract_rover_targets
from .spatial import resolve_target, update_device_activity
from .router import (
    check_voice_lock,
    clear_voice_lock,
    is_emergency_stop,
    route_transcript,
    set_voice_lock,
)
from .schemas import (
    DeviceCommand,
    DeviceEvent,
    DeviceRegistration,
    DeviceSpawn,
    ManualOverridePayload,
)
from .state import StateManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ClaudeHome Control Plane")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
state_manager = StateManager()


# ── ConnectionManager ──────────────────────────────────────────────


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, WebSocket] = {}

    async def connect(self, device_id: str, ws: WebSocket) -> None:
        await ws.accept()
        self.active_connections[device_id] = ws
        state_manager.update_device_status(device_id, "online")
        logger.info("WebSocket connected: %s", device_id)

    async def disconnect(self, device_id: str) -> None:
        self.active_connections.pop(device_id, None)
        state_manager.update_device_status(device_id, "offline")
        logger.info("WebSocket disconnected: %s", device_id)

    async def send_to_device(self, device_id: str, message: dict) -> dict:
        device = state_manager.get_device(device_id)
        if device and device.is_virtual:
            logger.info("[VIRTUAL %s] %s", device_id, message)
            return {"status": "ok", "detail": "virtual device logged"}

        ws = self.active_connections.get(device_id)
        if ws is None or ws.client_state != WebSocketState.CONNECTED:
            logger.warning("Device %s not connected, cannot send", device_id)
            return {"status": "offline", "detail": f"device {device_id} not connected"}

        try:
            await ws.send_json(message)
            return {"status": "ok", "detail": "sent"}
        except Exception as e:
            logger.error("Failed to send to %s: %s", device_id, e)
            return {"status": "error", "detail": str(e)}

    async def broadcast(self, message: dict, device_ids: list[str] | None = None) -> None:
        targets = device_ids or list(self.active_connections.keys())
        for device_id in targets:
            await self.send_to_device(device_id, message)


manager = ConnectionManager()


# ── Helper: generate request_id ────────────────────────────────────


def _new_request_id() -> str:
    return f"req_{uuid4().hex[:8]}"


# ── Dispatchers ────────────────────────────────────────────────────


async def dispatch_command(device_id: str, action: str, params: dict) -> dict:
    cmd = DeviceCommand(
        type="command",
        action=action,
        params=params,
        request_id=_new_request_id(),
    )
    logger.info("Dispatching command to %s: %s(%s)", device_id, action, params)
    return await manager.send_to_device(device_id, cmd.model_dump())


async def dispatch_spawn(device_id: str, instruction: str) -> dict:
    spawn = DeviceSpawn(
        type="spawn",
        instruction=instruction,
        request_id=_new_request_id(),
    )
    logger.info("Dispatching spawn to %s: %s", device_id, instruction[:80])
    return await manager.send_to_device(device_id, spawn.model_dump())


# ── Event processing pipeline (implements spec section 8) ──────────────


async def process_event(event: DeviceEvent) -> dict:
    """Route a DeviceEvent per the spec section 8 Event Routing Table."""
    state_manager.append_event(event)

    kind = event.kind

    if kind == "transcript":
        return await _handle_transcript(event)
    elif kind == "action_result":
        return _handle_action_result(event)
    elif kind == "heartbeat":
        return _handle_heartbeat(event)
    elif kind == "manual_override":
        return await _handle_manual_override(event)
    elif kind == "tick":
        return await _run_master_reasoning(event)
    elif kind == "vision_result":
        return await _run_master_reasoning(event)
    elif kind == "frame":
        # TODO: vision pipeline integration
        logger.info("Frame received from %s — vision pipeline not yet integrated", event.device_id)
        return {"status": "ok", "detail": "frame logged, vision not yet integrated"}
    else:
        logger.warning("Unknown event kind: %s", kind)
        return {"status": "error", "detail": f"unknown event kind: {kind}"}


async def _handle_transcript(event: DeviceEvent) -> dict:
    text = event.payload.get("text", "")
    result = route_transcript(text, state_manager)
    route_type = result[0]

    if route_type == "emergency_stop":
        # Broadcast stop command to rover (the only mobile device)
        await dispatch_command("rover", "stop", {})
        return {"status": "ok", "detail": "emergency stop broadcast"}

    elif route_type == "dropped":
        return {"status": "ok", "detail": "transcript dropped (voice lock active)"}

    elif route_type == "deterministic":
        _, device_id, action, params = result
        send_result = await dispatch_command(device_id, action, params)
        return {"status": "ok", "detail": f"deterministic: {device_id}.{action}", "dispatch": send_result}

    elif route_type == "master":
        return await _run_master_reasoning(event)

    return {"status": "error", "detail": f"unexpected route type: {route_type}"}


def _handle_action_result(event: DeviceEvent) -> dict:
    device_id = event.device_id
    payload = event.payload

    # Clear voice lock if this device was speaking
    clear_voice_lock(device_id, state_manager)

    # Reset spatial status to idle
    state_manager.update_spatial_device(device_id, {"status": "idle"})

    logger.info(
        "Action result from %s: %s — %s",
        device_id,
        payload.get("status"),
        payload.get("detail", ""),
    )
    return {"status": "ok", "detail": "action_result logged"}


def _handle_heartbeat(event: DeviceEvent) -> dict:
    state_manager.update_device_last_seen(event.device_id)
    return {"status": "ok", "detail": "heartbeat recorded"}


async def _handle_manual_override(event: DeviceEvent) -> dict:
    try:
        override = ManualOverridePayload(**event.payload)
    except Exception as e:
        logger.error("Invalid manual_override payload: %s", e)
        return {"status": "error", "detail": f"invalid payload: {e}"}

    if override.type == "command" and override.action:
        result = await dispatch_command(override.target, override.action, override.params)
        return {"status": "ok", "detail": f"manual command dispatched to {override.target}", "dispatch": result}
    elif override.type == "spawn" and override.instruction:
        result = await dispatch_spawn(override.target, override.instruction)
        return {"status": "ok", "detail": f"manual spawn dispatched to {override.target}", "dispatch": result}
    else:
        return {"status": "error", "detail": "manual_override missing action or instruction"}


# ── Master reasoning integration ──────────────────────────────────

# Serial master lock — spec requires one reasoning turn at a time
_master_lock = asyncio.Lock()

# Devices that trigger voice lock when receiving spawn instructions
_SPEAKING_DEVICES = {"mirror", "radio"}


async def _run_master_reasoning(event: DeviceEvent) -> dict:
    """Execute a full master reasoning turn and dispatch results.

    Per spec section 19 Execution Semantics:
    1. Parse tool_use blocks
    2. Check no_op (must be only call)
    3. Apply all update_user_state calls (merge into one patch)
    4. Persist state.json before dispatching
    5. Dispatch spawns to devices
    6. Parallel dispatch to different devices
    7. Sequential dispatch to same device
    """
    async with _master_lock:
        return await _run_master_reasoning_inner(event)


async def _run_master_reasoning_inner(event: DeviceEvent) -> dict:
    """Inner implementation, called under _master_lock."""
    try:
        result = execute_master_turn(state_manager, event)
    except Exception as e:
        logger.error("Master reasoning failed: %s", e)
        # Log the failure
        state_manager.append_master_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trigger": event.model_dump(mode="json"),
            "error": str(e),
            "outcome": "error",
        })
        return {"status": "error", "detail": f"master reasoning error: {e}"}

    tool_calls = result["tool_calls"]
    meta = result["turn_metadata"]

    if not tool_calls:
        state_manager.append_master_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **meta,
            "outcome": "empty",
            "dispatches": [],
        })
        return {"status": "ok", "detail": "master returned no actions"}

    # Check for no_op
    if meta["is_no_op"]:
        reason = tool_calls[0]["input"].get("reason", "no reason")
        logger.info("Master no_op: %s", reason)
        state_manager.append_master_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **meta,
            "tool_calls": tool_calls,
            "outcome": "no_op",
            "no_op_reason": reason,
            "dispatches": [],
        })
        return {"status": "ok", "detail": f"no_op: {reason}"}

    # Step 3-4: Apply state updates and persist before dispatch
    apply_state_update(state_manager, tool_calls)
    state_after = state_manager.read_state()

    # Step 5-7: Extract and dispatch device instructions
    instructions = extract_device_instructions(tool_calls)

    # Extract rover targets and update spatial state
    rover_targets = extract_rover_targets(tool_calls)
    room_config = state_manager.read_room_config()
    for target in rover_targets:
        if room_config:
            x, y = resolve_target(target, room_config)
            state_manager.update_spatial_device("rover", {
                "x_cm": x, "y_cm": y, "source": "master_reasoning",
            })

    if not instructions:
        state_manager.append_master_log({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **meta,
            "tool_calls": tool_calls,
            "state_after": state_after,
            "outcome": "state_update_only",
            "dispatches": [],
        })
        return {"status": "ok", "detail": "master updated state only", "tool_calls": len(tool_calls)}

    # Group instructions by device for sequential ordering per device
    device_queues: dict[str, list[str]] = {}
    for device_id, instruction in instructions:
        device_queues.setdefault(device_id, []).append(instruction)

    # Dispatch: parallel across devices, sequential within same device
    dispatch_log: list[dict] = []

    async def _dispatch_device_queue(device_id: str, queue: list[str]) -> list[dict]:
        results = []
        # Set spatial status to executing
        state_manager.update_spatial_device(device_id, {"status": "executing"})
        for instruction in queue:
            result = await dispatch_spawn(device_id, instruction)
            # Set voice lock for speaking devices
            if device_id in _SPEAKING_DEVICES:
                set_voice_lock(device_id, state_manager)
            dispatch_log.append({"device": device_id, "instruction": instruction, "result": result})
            results.append(result)
        return results

    dispatch_tasks = [
        _dispatch_device_queue(device_id, queue)
        for device_id, queue in device_queues.items()
    ]
    dispatch_results = await asyncio.gather(*dispatch_tasks, return_exceptions=True)

    # Log any dispatch errors
    for device_id, result in zip(device_queues.keys(), dispatch_results):
        if isinstance(result, Exception):
            logger.error("Dispatch to %s failed: %s", device_id, result)

    # Write full master turn log
    state_manager.append_master_log({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **meta,
        "tool_calls": tool_calls,
        "state_after": state_after,
        "outcome": "dispatch",
        "dispatches": dispatch_log,
    })

    return {
        "status": "ok",
        "detail": f"master dispatched to {len(device_queues)} device(s)",
        "tool_calls": len(tool_calls),
        "devices": list(device_queues.keys()),
    }


# ── Endpoints ──────────────────────────────────────────────────────


@app.get("/health")
async def health():
    online = len(manager.active_connections)
    return {"status": "ok", "devices_online": online}


@app.post("/register")
async def register_device(reg: DeviceRegistration):
    result = state_manager.register_device(reg)
    return {"status": result}


@app.websocket("/ws/{device_id}")
async def websocket_endpoint(ws: WebSocket, device_id: str):
    device = state_manager.get_device(device_id)
    if device is None:
        await ws.close(code=4001, reason="device not registered")
        return

    await manager.connect(device_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            event = DeviceEvent(**data)
            await process_event(event)
    except WebSocketDisconnect:
        await manager.disconnect(device_id)
    except Exception as e:
        logger.error("WebSocket error for %s: %s", device_id, e)
        await manager.disconnect(device_id)


@app.post("/events")
async def inject_event(event: DeviceEvent):
    result = await process_event(event)
    return result


@app.post("/commands/{device_id}")
async def inject_command(device_id: str, body: dict):
    msg_type = body.get("type")
    if msg_type == "command":
        cmd = DeviceCommand(**body)
        result = await manager.send_to_device(device_id, cmd.model_dump())
        return {"status": "ok", "dispatch": result}
    elif msg_type == "spawn":
        spawn = DeviceSpawn(**body)
        result = await manager.send_to_device(device_id, spawn.model_dump())
        return {"status": "ok", "dispatch": result}
    else:
        return {"status": "error", "detail": f"unknown message type: {msg_type}"}


@app.get("/state")
async def get_state():
    return state_manager.read_state()


@app.get("/devices")
async def get_devices():
    return [d.model_dump(mode="json") for d in state_manager.read_devices()]


@app.get("/events")
async def get_events():
    return state_manager.read_recent_events()


@app.get("/master-log")
async def get_master_log(limit: int = 50):
    return state_manager.read_master_log(limit=limit)


# ── Spatial endpoints ──────────────────────────────────────────────


@app.get("/room")
async def get_room():
    """Return static room configuration."""
    return state_manager.read_room_config()


@app.get("/spatial")
async def get_spatial():
    """Return current spatial state."""
    state = state_manager.read_state()
    return state.get("spatial", {})


@app.post("/spatial/calibrate")
async def calibrate_position(body: dict):
    """Manual position override (drag-drop from dashboard).

    Body: {"device_id": "rover", "x_cm": 250, "y_cm": 200}
    """
    device_id = body.get("device_id")
    x_cm = body.get("x_cm")
    y_cm = body.get("y_cm")
    if not device_id or x_cm is None or y_cm is None:
        return {"status": "error", "detail": "missing device_id, x_cm, or y_cm"}
    state_manager.update_spatial_device(device_id, {
        "x_cm": x_cm, "y_cm": y_cm, "source": "manual_calibration"
    })
    return {"status": "ok"}


# ── Dashboard ──────────────────────────────────────────────────────

@app.get("/")
async def dashboard_root():
    return FileResponse("dashboard/index.html")

app.mount("/dashboard", StaticFiles(directory="dashboard"), name="dashboard")

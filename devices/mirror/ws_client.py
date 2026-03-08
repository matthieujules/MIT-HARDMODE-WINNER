"""WebSocket client runtime for the Mirror device.

Boot sequence:
1. Load config.yaml
2. POST /register to control plane
3. Connect WebSocket to ws://{master_url}/ws/{device_id}
4. Listen for incoming JSON messages (command / spawn)
5. Send action_result back over WS after each completes
6. Send periodic heartbeat events
7. Auto-reconnect on disconnect with exponential backoff
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import websockets
import websockets.exceptions
import yaml

try:
    from .agent import run_agent_loop, warmup
    from .camera import MirrorCamera
    from .display import MirrorDisplay
    from .image_generation import MirrorImageGenerator
    from .planner import MirrorInstructionPlanner
except ImportError:
    from agent import run_agent_loop, warmup
    from camera import MirrorCamera
    from display import MirrorDisplay
    from image_generation import MirrorImageGenerator
    from planner import MirrorInstructionPlanner

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")
HEARTBEAT_INTERVAL_S = 30
RECONNECT_BASE_S = 1
RECONNECT_MAX_S = 30


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _master_url(config: dict) -> str:
    return os.environ.get("MASTER_URL", config["network"]["master_url"])


def _ws_url(base_url: str, device_id: str) -> str:
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base.rstrip('/')}/ws/{device_id}"


def register_device(config: dict) -> bool:
    """POST /register to the control plane. Returns True on success."""
    base_url = _master_url(config)
    url = f"{base_url.rstrip('/')}/register"

    actions = list(config.get("actions", {}).keys())
    capabilities = config.get("capabilities", [])

    body = {
        "device_id": config["device_id"],
        "device_name": config["device_name"],
        "device_type": config["device_type"],
        "capabilities": capabilities,
        "actions": actions,
        "ip": config["network"]["ip"],
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
            logger.info("Registered with control plane: %s", result)
            return True
    except urllib.error.HTTPError as exc:
        logger.error("Registration HTTP error %d: %s", exc.code, exc.read().decode("utf-8", errors="replace"))
        return False
    except Exception as exc:
        logger.error("Registration failed: %s", exc)
        return False


def handle_command(
    action: str,
    params: dict[str, Any],
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    skip_camera: bool,
) -> dict[str, Any]:
    """Execute a direct command and return a rich result dict."""
    start_time = time.monotonic()
    try:
        if action == "display_image":
            instruction = str(params.get("instruction", ""))
            if not instruction:
                return {"status": "error", "detail": "display_image requires 'instruction' param", "layer": "command"}

            frame = camera.placeholder_frame("camera skipped") if skip_camera else camera.get_frame()
            plan = planner.plan(instruction)
            result = generator.generate(plan, frame)
            screen_path = display.show_generated(result.image, ttl_s=20)

            detail = (
                f"displayed '{plan.icon_name}' ({plan.display_mode}), "
                f"image source: {result.source}, saved: {result.saved_path}"
            )
            elapsed = time.monotonic() - start_time
            return {
                "status": "ok",
                "detail": detail,
                "layer": "command",
                "action": action,
                "params": params,
                "elapsed_ms": round(elapsed * 1000),
                "display_state": {
                    "state": display.GENERATED,
                    "icon": plan.icon_name,
                    "mode": plan.display_mode,
                    "image_source": result.source,
                    "saved_path": str(result.saved_path),
                    "screen_path": str(screen_path),
                },
            }

        if action == "show_mirror":
            display.show_mirror()
            elapsed = time.monotonic() - start_time
            return {
                "status": "ok",
                "detail": "reverted to mirror view",
                "layer": "command",
                "action": action,
                "params": params,
                "elapsed_ms": round(elapsed * 1000),
                "display_state": {"state": display.MIRROR},
            }

        if action == "stop":
            display.show_mirror()
            elapsed = time.monotonic() - start_time
            return {
                "status": "ok",
                "detail": "stop acknowledged; reverted to mirror view",
                "layer": "command",
                "action": action,
                "params": params,
                "elapsed_ms": round(elapsed * 1000),
                "display_state": {"state": display.MIRROR},
            }

        logger.warning("Unknown command action: %s", action)
        return {"status": "error", "detail": f"unsupported action: {action}", "layer": "command"}

    except Exception as exc:
        logger.error("Command execution error (%s): %s", action, exc)
        elapsed = time.monotonic() - start_time
        return {
            "status": "error",
            "detail": f"command error: {exc}",
            "layer": "command",
            "action": action,
            "elapsed_ms": round(elapsed * 1000),
        }


async def _send_action_result(
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
    request_id: str,
    result: dict[str, Any],
) -> None:
    msg = {
        "device_id": device_id,
        "kind": "action_result",
        "payload": {
            "request_id": request_id,
            **result,
        },
    }
    try:
        await ws.send(json.dumps(msg))
        logger.info("Sent action_result: request_id=%s status=%s", request_id, result.get("status"))
    except Exception as exc:
        logger.error("Failed to send action_result: %s", exc)


async def _send_heartbeat(
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
) -> None:
    msg = {
        "device_id": device_id,
        "kind": "heartbeat",
        "payload": {},
    }
    try:
        await ws.send(json.dumps(msg))
        logger.debug("Heartbeat sent")
    except Exception as exc:
        logger.error("Failed to send heartbeat: %s", exc)


async def _sleep_or_stop(stop_event: threading.Event | None, seconds: float) -> bool:
    if stop_event is None:
        await asyncio.sleep(seconds)
        return False

    deadline = time.monotonic() + seconds
    while not stop_event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.25, remaining))
    return True


async def _heartbeat_task(
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
    stop_event: threading.Event | None,
) -> None:
    while True:
        if await _sleep_or_stop(stop_event, HEARTBEAT_INTERVAL_S):
            return
        await _send_heartbeat(ws, device_id)


async def _recv_or_stop(
    ws: websockets.WebSocketClientProtocol,
    stop_event: threading.Event | None,
) -> str:
    if stop_event is None:
        return await ws.recv()

    while True:
        if stop_event.is_set():
            raise asyncio.CancelledError("stop requested")
        try:
            return await asyncio.wait_for(ws.recv(), timeout=0.25)
        except asyncio.TimeoutError:
            continue


async def _handle_message(
    msg_data: dict,
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    skip_camera: bool,
) -> None:
    msg_type = msg_data.get("type")
    request_id = msg_data.get("request_id", "unknown")

    if msg_type == "command":
        action = msg_data.get("action", "")
        params = msg_data.get("params", {})
        logger.info("Received command: %s(%s) request_id=%s", action, params, request_id)
        result = await asyncio.to_thread(
            handle_command,
            action,
            params,
            camera,
            planner,
            generator,
            display,
            skip_camera,
        )
        await _send_action_result(ws, device_id, request_id, result)
        return

    if msg_type == "spawn":
        instruction = msg_data.get("instruction", "")
        max_iterations = msg_data.get("max_iterations", 10)
        time_budget_ms = msg_data.get("time_budget_ms", 30000)
        logger.info("Received spawn: %r request_id=%s", instruction[:80], request_id)

        result = await asyncio.to_thread(
            run_agent_loop,
            instruction,
            camera,
            planner,
            generator,
            display,
            max_iterations,
            time_budget_ms,
            skip_camera,
        )
        result["layer"] = "agent_loop"
        result["instruction"] = instruction
        await _send_action_result(ws, device_id, request_id, result)
        return

    logger.warning("Unknown message type: %s", msg_type)


async def _ws_session(
    config: dict,
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    skip_camera: bool,
    stop_event: threading.Event | None,
) -> None:
    device_id = config["device_id"]
    base_url = _master_url(config)
    ws_url = _ws_url(base_url, device_id)

    logger.info("Connecting to %s", ws_url)

    async with websockets.connect(ws_url) as ws:
        logger.info("WebSocket connected to control plane")
        hb_task = asyncio.create_task(_heartbeat_task(ws, device_id, stop_event))

        try:
            while True:
                try:
                    raw_message = await _recv_or_stop(ws, stop_event)
                except asyncio.CancelledError:
                    logger.info("WebSocket stop requested")
                    await ws.close()
                    return

                try:
                    msg_data = json.loads(raw_message)
                except json.JSONDecodeError:
                    logger.error("Received non-JSON message: %s", raw_message[:200])
                    continue

                try:
                    await _handle_message(
                        msg_data,
                        ws,
                        device_id,
                        camera,
                        planner,
                        generator,
                        display,
                        skip_camera,
                    )
                except Exception as exc:
                    logger.error("Error handling message: %s", exc, exc_info=True)
                    request_id = msg_data.get("request_id", "unknown")
                    await _send_action_result(
                        ws,
                        device_id,
                        request_id,
                        {"status": "error", "detail": str(exc)},
                    )
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass


async def run_ws_client(
    config: dict,
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    skip_camera: bool = False,
    stop_event: threading.Event | None = None,
) -> None:
    """Register, connect, and keep the WS loop alive until stopped."""
    device_id = config["device_id"]
    logger.info(
        "Mirror runtime starting: device_id=%s skip_camera=%s master=%s",
        device_id,
        skip_camera,
        _master_url(config),
    )

    backoff = RECONNECT_BASE_S
    while stop_event is None or not stop_event.is_set():
        registered = await asyncio.to_thread(register_device, config)
        if registered:
            break
        logger.warning("Registration failed, retrying in %ds...", backoff)
        if await _sleep_or_stop(stop_event, backoff):
            logger.info("WebSocket client stop requested during registration backoff")
            return
        backoff = min(backoff * 2, RECONNECT_MAX_S)

    if stop_event is not None and stop_event.is_set():
        logger.info("WebSocket client stop requested before warmup")
        return

    await asyncio.to_thread(warmup)

    backoff = RECONNECT_BASE_S
    while stop_event is None or not stop_event.is_set():
        try:
            await _ws_session(
                config,
                camera,
                planner,
                generator,
                display,
                skip_camera,
                stop_event,
            )
            if stop_event is not None and stop_event.is_set():
                break
            logger.info("WebSocket disconnected cleanly, reconnecting...")
            backoff = RECONNECT_BASE_S
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning("WebSocket connection closed: %s", exc)
        except ConnectionRefusedError:
            logger.warning("Connection refused by control plane")
        except OSError as exc:
            logger.warning("Network error: %s", exc)
        except Exception as exc:
            logger.error("Unexpected error in WS session: %s", exc, exc_info=True)

        if stop_event is not None and stop_event.is_set():
            break

        logger.info("Reconnecting in %ds...", backoff)
        if await _sleep_or_stop(stop_event, backoff):
            break
        backoff = min(backoff * 2, RECONNECT_MAX_S)
        if stop_event is None or not stop_event.is_set():
            await asyncio.to_thread(register_device, config)

    logger.info("WebSocket client stopped")

"""WebSocket client runtime for the Rover device.

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
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

import yaml
import websockets
import websockets.exceptions

from planner import plan_command
from agent import run_agent_loop

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.yaml")
HEARTBEAT_INTERVAL_S = 30
RECONNECT_BASE_S = 1
RECONNECT_MAX_S = 30


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _master_url(config: dict) -> str:
    """Resolve the control plane URL. Env var overrides config."""
    return os.environ.get("MASTER_URL", config["network"]["master_url"])


def _ws_url(base_url: str, device_id: str) -> str:
    """Convert http(s) base URL to a ws(s) URL for the device WebSocket."""
    ws_base = base_url.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws_base.rstrip('/')}/ws/{device_id}"


# -- Motion module (with sim fallback) ----------------------------------------

_motion = None

def _get_motion():
    """Lazy-load motion module. Returns None if not on Pi hardware."""
    global _motion
    if _motion is None:
        try:
            import motion as m
            _motion = m
        except Exception:
            pass
    return _motion


# -- Registration --------------------------------------------------------------


def register_device(config: dict) -> bool:
    """POST /register to the control plane. Returns True on success."""
    base_url = _master_url(config)
    url = f"{base_url.rstrip('/')}/register"

    body = {
        "device_id": config["device_id"],
        "device_name": config["device_name"],
        "device_type": config["device_type"],
        "capabilities": config.get("capabilities", []),
        "actions": list(config.get("actions", {}).keys()),
        "ip": config["network"].get("tailscale_hostname", config["network"].get("ip", "rover-pi")),
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            logger.info("Registered with control plane: %s", result)
            return True
    except urllib.error.HTTPError as e:
        logger.error("Registration HTTP error %d: %s", e.code, e.read().decode("utf-8", errors="replace"))
        return False
    except Exception as e:
        logger.error("Registration failed: %s", e)
        return False


# -- Command handling (Layer 1) ------------------------------------------------


def handle_command(
    action: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Execute a direct command and return rich result dict."""
    start_time = time.monotonic()
    motion = _get_motion()

    try:
        if action == "move":
            distance_cm = float(params.get("distance_cm", 0))
            speed = int(params.get("speed", 40))
            if motion is not None:
                motion.move(distance_cm, speed)
            detail = f"move {distance_cm}cm at speed {speed}"

        elif action == "rotate":
            degrees = float(params.get("degrees", 0))
            speed = int(params.get("speed", 40))
            if motion is not None:
                motion.rotate(degrees, speed)
            detail = f"rotate {degrees} degrees at speed {speed}"

        elif action == "stop":
            if motion is not None:
                motion.stop()
            detail = "stopped all motors"

        elif action == "emote":
            emotion = str(params.get("emotion", "excitement"))
            if motion is not None:
                if emotion == "excitement":
                    motion.excitement()
                elif emotion == "sad":
                    motion.act_sad()
                elif emotion == "ponder":
                    motion.say_no()
                elif emotion == "deliver":
                    motion.pass_food()
                else:
                    return {"status": "error", "detail": f"unknown emotion: {emotion}", "layer": "command"}
            detail = f"emote '{emotion}'"

        else:
            logger.warning("Unknown command action: %s", action)
            return {"status": "error", "detail": f"unknown action: {action}", "layer": "command"}

        elapsed = time.monotonic() - start_time
        sim_note = " (sim)" if motion is None else ""
        return {
            "status": "ok",
            "detail": f"{detail}{sim_note}",
            "layer": "command",
            "action": action,
            "params": params,
            "elapsed_ms": round(elapsed * 1000),
        }

    except Exception as e:
        logger.error("Command execution error (%s): %s", action, e)
        elapsed = time.monotonic() - start_time
        return {
            "status": "error",
            "detail": f"command error: {e}",
            "layer": "command",
            "action": action,
            "elapsed_ms": round(elapsed * 1000),
        }


# -- WebSocket loop ------------------------------------------------------------


async def _send_action_result(
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
    request_id: str,
    result: dict[str, Any],
) -> None:
    """Send an action_result event back to the control plane."""
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
    except Exception as e:
        logger.error("Failed to send action_result: %s", e)


async def _send_heartbeat(
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
) -> None:
    """Send a heartbeat event."""
    msg = {
        "device_id": device_id,
        "kind": "heartbeat",
        "payload": {},
    }
    try:
        await ws.send(json.dumps(msg))
        logger.debug("Heartbeat sent")
    except Exception as e:
        logger.error("Failed to send heartbeat: %s", e)


async def _heartbeat_task(
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
) -> None:
    """Background coroutine that sends heartbeats at a fixed interval."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        await _send_heartbeat(ws, device_id)


async def _handle_message(
    msg_data: dict,
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
) -> None:
    """Route a single incoming message by type."""
    msg_type = msg_data.get("type")
    request_id = msg_data.get("request_id", "unknown")

    if msg_type == "command":
        action = msg_data.get("action", "")
        params = msg_data.get("params", {})
        logger.info("Received command: %s(%s) request_id=%s", action, params, request_id)

        # Try planner for Layer 1 direct routing
        planned = plan_command(action, params)
        if planned is not None:
            action = planned["action"]
            params = planned.get("params", params)

        # Run command in a thread to avoid blocking the event loop
        result = await asyncio.to_thread(handle_command, action, params)
        await _send_action_result(ws, device_id, request_id, result)

    elif msg_type == "spawn":
        instruction = msg_data.get("instruction", "")
        max_iterations = msg_data.get("max_iterations", 10)
        time_budget_ms = msg_data.get("time_budget_ms", 45000)
        logger.info("Received spawn: %r request_id=%s", instruction[:80], request_id)

        # Run agent loop in a thread (it may do blocking LLM calls + motor ops)
        result = await asyncio.to_thread(
            run_agent_loop,
            instruction,
            max_iterations,
            time_budget_ms,
        )
        result["layer"] = "agent_loop"
        result["instruction"] = instruction
        await _send_action_result(ws, device_id, request_id, result)

    else:
        logger.warning("Unknown message type: %s", msg_type)


async def _ws_session(config: dict) -> None:
    """Run a single WebSocket session until disconnected or errored."""
    device_id = config["device_id"]
    base_url = _master_url(config)
    ws_url = _ws_url(base_url, device_id)

    logger.info("Connecting to %s", ws_url)

    async with websockets.connect(ws_url) as ws:
        logger.info("WebSocket connected to control plane")

        # Start heartbeat background task
        hb_task = asyncio.create_task(_heartbeat_task(ws, device_id))

        try:
            async for raw_message in ws:
                try:
                    msg_data = json.loads(raw_message)
                except json.JSONDecodeError:
                    logger.error("Received non-JSON message: %s", raw_message[:200])
                    continue

                try:
                    await _handle_message(msg_data, ws, device_id)
                except Exception as e:
                    logger.error("Error handling message: %s", e, exc_info=True)
                    request_id = msg_data.get("request_id", "unknown")
                    await _send_action_result(ws, device_id, request_id, {
                        "status": "error", "detail": str(e),
                    })
        finally:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass


async def run_ws_client(config: dict, simulate: bool = True) -> None:
    """Main entry point: register, connect, and run the WS loop with auto-reconnect."""
    device_id = config["device_id"]

    logger.info(
        "Rover runtime starting: device_id=%s simulate=%s master=%s",
        device_id,
        simulate,
        _master_url(config),
    )

    # Registration (retry until success)
    backoff = RECONNECT_BASE_S
    while True:
        if register_device(config):
            break
        logger.warning("Registration failed, retrying in %ds...", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX_S)

    # Warm up LLM connection so first real instruction is fast
    from agent import warmup
    await asyncio.to_thread(warmup)

    # WebSocket loop with auto-reconnect
    backoff = RECONNECT_BASE_S
    while True:
        try:
            await _ws_session(config)
            # Clean disconnect -- still reconnect
            logger.info("WebSocket disconnected cleanly, reconnecting...")
            backoff = RECONNECT_BASE_S

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("WebSocket connection closed: %s", e)
        except ConnectionRefusedError:
            logger.warning("Connection refused by control plane")
        except OSError as e:
            logger.warning("Network error: %s", e)
        except Exception as e:
            logger.error("Unexpected error in WS session: %s", e, exc_info=True)

        logger.info("Reconnecting in %ds...", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, RECONNECT_MAX_S)

        # Re-register before reconnecting (control plane may have restarted)
        register_device(config)

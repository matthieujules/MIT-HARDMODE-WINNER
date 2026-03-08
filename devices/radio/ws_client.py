"""WebSocket client runtime for the Radio device.

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
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

import yaml
import websockets
import websockets.exceptions

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


# -- Runtime creation -----------------------------------------------------


def _create_runtime():
    """Create a RadioRuntime instance. Handles both repo and flat deployment layouts."""
    try:
        from config import load_runtime_config
    except ImportError:
        from RASPi.config import load_runtime_config

    try:
        from runtime import RadioRuntime
    except ImportError:
        from RASPi.runtime import RadioRuntime

    config_path = Path(__file__).resolve().with_name("config.yaml")
    runtime_config = load_runtime_config(config_path)
    return RadioRuntime(runtime_config)


# -- Registration ----------------------------------------------------------


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
        "ip": config["network"].get("ip", config["network"].get("tailscale_hostname", "radiohost")),
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


# -- Command handling (Layer 1) --------------------------------------------


def handle_command(action: str, params: dict[str, Any], runtime: Any) -> dict[str, Any]:
    """Execute a direct command and return rich result dict."""
    start_time = time.monotonic()
    try:
        if action in ("stop", "quiet", "silence", "pause"):
            runtime.interrupt_playback()
            detail = "Playback stopped"

        elif action in ("play", "play_music", "speak"):
            selection = params.get("selection", params.get("instruction", params.get("text", "01")))
            result = runtime.play_code(str(selection))
            detail = f"Played audio: selection={result.get('selection', 'unknown')}"

        elif action in ("spin_dial", "turn_dial"):
            direction = params.get("direction", "clockwise")
            duration = float(params.get("duration_seconds", params.get("duration_ms", 600)))
            if duration > 10:  # duration_ms -> seconds
                duration = duration / 1000.0
            if direction == "counterclockwise":
                runtime.dial.nudge_counterclockwise(duration_seconds=duration)
            else:
                runtime.dial.nudge_clockwise(duration_seconds=duration)
            detail = f"Dial spun {direction} for {duration}s"

        else:
            detail = f"Unknown action: {action}"

        elapsed = time.monotonic() - start_time
        return {
            "status": "ok",
            "detail": detail,
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


# -- WebSocket loop --------------------------------------------------------


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


_active_spawn_task: asyncio.Task | None = None
_active_cancel_event: threading.Event | None = None


async def _run_spawn(
    instruction: str,
    runtime: Any,
    max_iterations: int,
    time_budget_ms: int,
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
    request_id: str,
    cancel_event: threading.Event,
) -> None:
    """Run a spawn in the background. Sends action_result when done."""
    try:
        result = await asyncio.to_thread(
            run_agent_loop,
            instruction,
            runtime,
            max_iterations,
            time_budget_ms,
            cancel_event,
        )
        # If the thread noticed cancellation itself, don't double-report
        if cancel_event.is_set():
            return
        result["layer"] = "agent_loop"
        result["instruction"] = instruction
        await _send_action_result(ws, device_id, request_id, result)
    except asyncio.CancelledError:
        logger.info("Spawn cancelled: request_id=%s", request_id)
        await _send_action_result(ws, device_id, request_id, {
            "status": "cancelled",
            "detail": "preempted by newer spawn",
            "layer": "agent_loop",
            "instruction": instruction,
        })
    except Exception as e:
        logger.error("Spawn error: %s", e, exc_info=True)
        await _send_action_result(ws, device_id, request_id, {
            "status": "error",
            "detail": str(e),
            "layer": "agent_loop",
            "instruction": instruction,
        })


async def _handle_message(
    msg_data: dict,
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
    runtime: Any,
) -> None:
    """Route a single incoming message by type."""
    global _active_spawn_task, _active_cancel_event
    msg_type = msg_data.get("type")
    request_id = msg_data.get("request_id", "unknown")

    if msg_type == "command":
        action = msg_data.get("action", "")
        params = msg_data.get("params", {})
        logger.info("Received command: %s(%s) request_id=%s", action, params, request_id)

        result = await asyncio.to_thread(handle_command, action, params, runtime)
        await _send_action_result(ws, device_id, request_id, result)

    elif msg_type == "spawn":
        instruction = msg_data.get("instruction", "")
        max_iterations = msg_data.get("max_iterations", 10)
        time_budget_ms = msg_data.get("time_budget_ms", 45000)
        logger.info("Received spawn: %r request_id=%s", instruction[:80], request_id)

        # Cancel previous spawn + signal its thread to stop + interrupt playback
        if _active_spawn_task is not None and not _active_spawn_task.done():
            logger.info("Cancelling previous spawn for new request")
            if _active_cancel_event is not None:
                _active_cancel_event.set()  # Signal the thread to exit
            _active_spawn_task.cancel()
            runtime.interrupt_playback()

        # Fresh cancel event for the new spawn
        _active_cancel_event = threading.Event()
        _active_spawn_task = asyncio.create_task(
            _run_spawn(instruction, runtime, max_iterations, time_budget_ms, ws, device_id, request_id, _active_cancel_event)
        )

    else:
        logger.warning("Unknown message type: %s", msg_type)


async def _ws_session(config: dict, runtime: Any) -> None:
    """Run a single WebSocket session until disconnected or errored."""
    device_id = config["device_id"]
    base_url = _master_url(config)
    ws_url = _ws_url(base_url, device_id)

    logger.info("Connecting to %s", ws_url)

    async with websockets.connect(ws_url) as ws:
        logger.info("WebSocket connected to control plane")

        hb_task = asyncio.create_task(_heartbeat_task(ws, device_id))

        try:
            async for raw_message in ws:
                try:
                    msg_data = json.loads(raw_message)
                except json.JSONDecodeError:
                    logger.error("Received non-JSON message: %s", raw_message[:200])
                    continue

                try:
                    await _handle_message(msg_data, ws, device_id, runtime)
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


async def run_ws_client(config: dict) -> None:
    """Main entry point: register, connect, and run the WS loop with auto-reconnect."""
    device_id = config["device_id"]

    # Create hardware runtime
    runtime = _create_runtime()

    logger.info(
        "Radio runtime starting: device_id=%s master=%s",
        device_id,
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
            await _ws_session(config, runtime)
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

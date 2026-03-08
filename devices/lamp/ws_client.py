"""WebSocket client runtime for the Lamp device.

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

from hardware import LEMHardwareController
from planner import ArmPlan, COLOR_MAP, InstructionPlanner
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


# ── Registration ──────────────────────────────────────────────────


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


# ── Command handling (Layer 1) ────────────────────────────────────


def handle_command(
    action: str,
    params: dict[str, Any],
    hw: LEMHardwareController,
    planner: InstructionPlanner,
) -> dict[str, Any]:
    """Execute a direct command and return rich result dict."""
    start_time = time.monotonic()
    try:
        if action == "set_color":
            # Support both named colors (from router) and explicit RGB
            _COLOR_ALIASES = {"warm": "warm white", "cool": "cyan"}
            color_name = params.get("color", "").lower()
            color_name = _COLOR_ALIASES.get(color_name, color_name)
            if color_name and color_name in COLOR_MAP:
                color = dict(COLOR_MAP[color_name])
            else:
                color = {
                    "r": int(params.get("r", 255)),
                    "g": int(params.get("g", 180)),
                    "b": int(params.get("b", 120)),
                }
            plan = ArmPlan(
                raw_instruction=f"command:set_color({color})",
                joints=dict(hw.current_joints),
                color=color,
                brightness=float(hw.config["hardware"]["lemp"].get("brightness_scale", 1.0)),
                duration_ms=500,
            )
            hw.apply_plan(plan)
            detail = f"set_color to R={color['r']} G={color['g']} B={color['b']}"

        elif action == "set_brightness":
            raw_brightness = float(params.get("brightness", 1.0))
            # Router sends 0-100 integer, normalize to 0.0-1.0
            brightness = raw_brightness / 100.0 if raw_brightness > 1.0 else raw_brightness
            plan = ArmPlan(
                raw_instruction=f"command:set_brightness({brightness})",
                joints=dict(hw.current_joints),
                color=dict(hw.current_color),
                brightness=brightness,
                duration_ms=300,
            )
            hw.apply_plan(plan)
            detail = f"set_brightness to {brightness}"

        elif action == "set_joint_angles":
            joints = {
                "base_yaw": float(params.get("base_yaw", hw.current_joints["base_yaw"])),
                "shoulder": float(params.get("shoulder", hw.current_joints["shoulder"])),
                "elbow": float(params.get("elbow", hw.current_joints["elbow"])),
                "wrist": float(params.get("wrist", hw.current_joints["wrist"])),
            }
            plan = ArmPlan(
                raw_instruction=f"command:set_joint_angles({joints})",
                joints=joints,
                color=dict(hw.current_color),
                brightness=float(hw.config["hardware"]["lemp"].get("brightness_scale", 1.0)),
                duration_ms=1000,
            )
            hw.apply_plan(plan)
            detail = f"set_joint_angles to {joints}"

        elif action == "move_to_preset":
            preset = str(params.get("preset", "home"))
            plan = planner.plan(preset, hw.current_joints, hw.current_color)
            hw.apply_plan(plan)
            detail = f"move_to_preset '{preset}'"

        elif action == "reset_pose":
            plan = planner.plan("home", hw.current_joints, hw.current_color)
            hw.apply_plan(plan)
            detail = "reset_pose to home"

        elif action == "emote":
            emotion = str(params.get("emotion", "curious"))
            plan = planner.plan(emotion, hw.current_joints, hw.current_color)
            hw.apply_plan(plan)
            detail = f"emote '{emotion}'"

        elif action == "stop":
            plan = planner.plan("home", hw.current_joints, hw.current_color)
            hw.apply_plan(plan)
            detail = "stop — returned to home"

        else:
            logger.warning("Unknown command action: %s", action)
            return {"status": "error", "detail": f"unknown action: {action}", "layer": "command"}

        elapsed = time.monotonic() - start_time
        return {
            "status": "ok",
            "detail": detail,
            "layer": "command",
            "action": action,
            "params": params,
            "elapsed_ms": round(elapsed * 1000),
            "hw_state": {"joints": dict(hw.current_joints), "color": dict(hw.current_color)},
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


# ── WebSocket loop ────────────────────────────────────────────────


async def _send_action_result(
    ws: websockets.WebSocketClientProtocol,
    device_id: str,
    request_id: str,
    result: dict[str, Any],
) -> None:
    """Send an action_result event back to the control plane.

    result must have at least 'status' and 'detail'. May also include
    'execution_log', 'iterations', 'elapsed_ms', 'hw_state', 'mode'.
    """
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
    hw: LEMHardwareController,
    planner: InstructionPlanner,
) -> None:
    """Route a single incoming message by type."""
    msg_type = msg_data.get("type")
    request_id = msg_data.get("request_id", "unknown")

    if msg_type == "command":
        action = msg_data.get("action", "")
        params = msg_data.get("params", {})
        logger.info("Received command: %s(%s) request_id=%s", action, params, request_id)

        # Run command in a thread to avoid blocking the event loop
        result = await asyncio.to_thread(handle_command, action, params, hw, planner)
        await _send_action_result(ws, device_id, request_id, result)

    elif msg_type == "spawn":
        instruction = msg_data.get("instruction", "")
        max_iterations = msg_data.get("max_iterations", 10)
        time_budget_ms = msg_data.get("time_budget_ms", 30000)
        logger.info("Received spawn: %r request_id=%s", instruction[:80], request_id)

        # Run agent loop in a thread (it may do blocking LLM calls)
        result = await asyncio.to_thread(
            run_agent_loop,
            instruction,
            hw,
            planner,
            max_iterations,
            time_budget_ms,
        )
        result["layer"] = "agent_loop"
        result["instruction"] = instruction
        await _send_action_result(ws, device_id, request_id, result)

    else:
        logger.warning("Unknown message type: %s", msg_type)


async def _ws_session(
    config: dict,
    hw: LEMHardwareController,
    planner: InstructionPlanner,
) -> None:
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
                    await _handle_message(msg_data, ws, device_id, hw, planner)
                except Exception as e:
                    logger.error("Error handling message: %s", e, exc_info=True)
                    # Send error result if we have a request_id
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
    hw = LEMHardwareController(config, simulate=simulate)
    planner = InstructionPlanner(config)

    logger.info(
        "Lamp runtime starting: device_id=%s simulate=%s master=%s",
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
            await _ws_session(config, hw, planner)
            # Clean disconnect — still reconnect
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

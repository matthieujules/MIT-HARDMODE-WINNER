"""Layer 2 agent loop for the Rover device.

Receives a spawn instruction from the control plane, uses a fast LLM
(Cerebras gpt-oss-120b) to decide movement actions, then executes them via
motion.py.  Fails loudly if no API key is available.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# -- Configuration -------------------------------------------------------------

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
ROVER_AGENT_MODEL = os.environ.get("ROVER_AGENT_MODEL", "gpt-oss-120b")
LLM_CALL_TIMEOUT_S = 30

SOUL_PATH = Path(__file__).resolve().with_name("SOUL.md")

# -- Motion module (with sim fallback) ----------------------------------------

_motion = None
_motion_loaded = False


def _get_motion():
    """Lazy-load motion module. Returns None if not on Pi hardware."""
    global _motion, _motion_loaded
    if not _motion_loaded:
        _motion_loaded = True
        try:
            import motion as m
            _motion = m
        except Exception as e:
            logger.warning("motion.py not available (sim mode): %s", e)
            _motion = None
    return _motion


# -- Singleton client + cached soul --------------------------------------------

_client = None
_soul_text = None


def _get_client():
    """Lazy-init singleton OpenAI client."""
    global _client
    if _client is None:
        from openai import OpenAI
        if not CEREBRAS_API_KEY:
            raise RuntimeError(
                "FATAL: CEREBRAS_API_KEY is not set. "
                "The rover agent REQUIRES an LLM -- there is no fallback. "
                "Set CEREBRAS_API_KEY in your environment or .env file."
            )
        _client = OpenAI(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL)
    return _client


def _get_soul() -> str:
    """Lazy-load and cache SOUL.md."""
    global _soul_text
    if _soul_text is None:
        _soul_text = _load_soul()
    return _soul_text


def warmup():
    """Send a tiny request to warm up the Cerebras connection.

    Call this during boot so the first real instruction doesn't pay cold-start cost.
    """
    try:
        client = _get_client()
        t0 = time.monotonic()
        client.chat.completions.create(
            model=ROVER_AGENT_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            timeout=30,
        )
        elapsed = round((time.monotonic() - t0) * 1000)
        logger.info("LLM warmup complete in %dms", elapsed)
    except Exception as e:
        logger.error("LLM warmup failed: %s", e)


# -- Tool definitions exposed to the model -------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": "Move the rover forward or backward by a distance in centimeters. Positive = forward, negative = backward.",
            "parameters": {
                "type": "object",
                "properties": {
                    "distance_cm": {"type": "number", "description": "Distance in cm. Positive=forward, negative=backward. Keep between -100 and 100."},
                    "speed": {"type": "integer", "description": "Speed 0-100 (duty cycle %). Default 40. Min effective ~18.", "default": 40}
                },
                "required": ["distance_cm"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rotate",
            "description": "Rotate the rover in place. Positive degrees = clockwise, negative = counter-clockwise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "degrees": {"type": "number", "description": "Rotation in degrees. Positive=clockwise, negative=CCW. Keep between -360 and 360."},
                    "speed": {"type": "integer", "description": "Speed 0-100 (duty cycle %). Default 40.", "default": 40}
                },
                "required": ["degrees"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop",
            "description": "Emergency stop -- immediately halt all motors.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "emote",
            "description": "Express an emotion through movement. Available emotions: excitement (spins and dashes), sad (slow wobble), ponder (forward-backward with head shakes), deliver (drive to kitchen area and back).",
            "parameters": {
                "type": "object",
                "properties": {
                    "emotion": {
                        "type": "string",
                        "enum": ["excitement", "sad", "ponder", "deliver"],
                        "description": "The emotion to express through movement"
                    }
                },
                "required": ["emotion"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Call this when the instruction has been fully executed. MUST be the last tool call.",
            "parameters": {
                "type": "object",
                "properties": {
                    "detail": {"type": "string", "description": "Brief description of what was done"}
                },
                "required": ["detail"]
            }
        }
    }
]


def _load_soul() -> str:
    try:
        return SOUL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "You are an eager, puppy-like mobile coaster. Communicate through movement only."


def _build_system_prompt(soul: str) -> str:
    motion = _get_motion()
    hw_status = "live hardware" if motion is not None else "simulation mode"
    return (
        f"{soul}\n\n"
        f"## Current Hardware State\n"
        f"Hardware: {hw_status}\n"
        f"Movement: relative only (no absolute position tracking)\n\n"
        f"## Rules\n"
        f"- You have tools to control the rover's motors.\n"
        f"- Use `move` for forward/backward distance moves (in cm).\n"
        f"- Use `rotate` for turning in place (in degrees).\n"
        f"- Use `emote` for expressive movement routines.\n"
        f"- Use `stop` for emergency halts.\n"
        f"- Execute the instruction by calling the appropriate tools.\n"
        f"- You MUST call the `done` tool as your final tool call to signal completion.\n"
        f"- Be decisive. Prefer one or two tool calls plus `done` over many iterations.\n"
        f"- If the instruction is ambiguous, pick the most reasonable interpretation.\n"
        f"- Do NOT respond with plain text. Always use tool calls.\n"
        f"- All movement is RELATIVE -- you have no position tracking.\n"
        f"- Keep individual moves under 100cm and speeds between 18-100.\n"
    )


def _execute_tool_call(name: str, args: dict[str, Any]) -> str:
    """Execute a single tool call against motion hardware. Returns a result string."""
    motion = _get_motion()
    try:
        if name == "move":
            distance_cm = float(args.get("distance_cm", 0))
            speed = int(args.get("speed", 40))
            if motion is not None:
                motion.move(distance_cm, speed)
                return f"Moved {distance_cm}cm at speed {speed}"
            else:
                return f"[SIM] Would move {distance_cm}cm at speed {speed}"

        elif name == "rotate":
            degrees = float(args.get("degrees", 0))
            speed = int(args.get("speed", 40))
            if motion is not None:
                motion.rotate(degrees, speed)
                return f"Rotated {degrees} degrees at speed {speed}"
            else:
                return f"[SIM] Would rotate {degrees} degrees at speed {speed}"

        elif name == "stop":
            if motion is not None:
                motion.stop()
                return "All motors stopped"
            else:
                return "[SIM] Would stop all motors"

        elif name == "emote":
            emotion = str(args.get("emotion", "excitement"))
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
                    return f"Unknown emotion: {emotion}"
                return f"Expressed '{emotion}' through movement"
            else:
                return f"[SIM] Would express '{emotion}'"

        elif name == "done":
            return f"DONE: {args.get('detail', 'completed')}"

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        logger.error("Tool execution error (%s): %s", name, e)
        return f"Error executing {name}: {e}"


def _run_llm_loop(
    instruction: str,
    max_iterations: int,
    time_budget_ms: int,
) -> dict[str, Any]:
    """Run the LLM-backed agent loop with conversation history. Returns rich result."""
    client = _get_client()
    soul = _get_soul()
    start_time = time.monotonic()
    deadline_s = time_budget_ms / 1000.0
    execution_log: list[dict[str, Any]] = []

    # Build conversation with accumulated history
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(soul)},
        {"role": "user", "content": instruction},
    ]

    for iteration in range(max_iterations):
        elapsed = time.monotonic() - start_time
        if elapsed >= deadline_s:
            status = "ok" if execution_log else "timeout"
            logger.warning("Agent loop hit time budget (%dms) at iteration %d (status=%s)", time_budget_ms, iteration, status)
            return {
                "status": status,
                "detail": f"time budget reached after {iteration} iterations, {len(execution_log)} tools executed",
                "execution_log": execution_log,
                "iterations": iteration,
                "elapsed_ms": round(elapsed * 1000),
            }

        # Remaining time budget as API timeout
        remaining_s = deadline_s - elapsed
        api_timeout = min(LLM_CALL_TIMEOUT_S, remaining_s)
        if api_timeout <= 0:
            status = "ok" if execution_log else "timeout"
            return {
                "status": status,
                "detail": f"time budget reached before iteration {iteration}, {len(execution_log)} tools executed",
                "execution_log": execution_log,
                "iterations": iteration,
                "elapsed_ms": round(elapsed * 1000),
            }

        try:
            response = client.chat.completions.create(
                model=ROVER_AGENT_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="required",
                timeout=api_timeout,
            )
        except Exception as e:
            logger.error("LLM call failed at iteration %d: %s", iteration, e)
            raise RuntimeError(
                f"LLM call failed at iteration {iteration}: {e}. "
                "The rover agent REQUIRES an LLM -- there is no fallback."
            ) from e

        message = response.choices[0].message

        if not message.tool_calls:
            # tool_choice="required" should prevent this, but guard anyway
            logger.error("Agent iteration %d: model returned no tool calls despite tool_choice=required", iteration)
            continue

        # Append assistant message with tool calls to history
        messages.append(message.model_dump())

        # Execute each tool call and build tool result messages
        for tool_call in message.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                logger.error("Bad tool call arguments: %s", tool_call.function.arguments)
                fn_args = {}

            logger.info("Agent iteration %d: %s(%s)", iteration, fn_name, fn_args)
            result = _execute_tool_call(fn_name, fn_args)

            execution_log.append({
                "iteration": iteration,
                "tool": fn_name,
                "args": fn_args,
                "result": result,
            })

            # Append tool result to conversation history
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

            # If the model called done, we're finished
            if fn_name == "done":
                detail = fn_args.get("detail", "completed")
                logger.info("Agent loop completed: %s", detail)
                elapsed = time.monotonic() - start_time
                return {
                    "status": "ok",
                    "detail": detail,
                    "execution_log": execution_log,
                    "iterations": iteration + 1,
                    "elapsed_ms": round(elapsed * 1000),
                }

    elapsed = time.monotonic() - start_time
    logger.warning("Agent loop hit max iterations (%d)", max_iterations)
    return {
        "status": "timeout",
        "detail": f"max iterations ({max_iterations}) reached without calling done",
        "execution_log": execution_log,
        "iterations": max_iterations,
        "elapsed_ms": round(elapsed * 1000),
    }


def run_agent_loop(
    instruction: str,
    max_iterations: int = 10,
    time_budget_ms: int = 45000,
) -> dict[str, Any]:
    """Public entry point for the agent loop."""
    logger.info("Agent loop starting: instruction=%r, max_iter=%d, budget=%dms",
                instruction, max_iterations, time_budget_ms)
    return _run_llm_loop(instruction, max_iterations, time_budget_ms)

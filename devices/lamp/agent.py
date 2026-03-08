"""Layer 2 agent loop for the Lamp device.

Receives a spawn instruction from the control plane, uses a fast LLM
(Cerebras gpt-oss-120b) to decide hardware actions, then executes them via
LEMHardwareController.  Fails loudly if no API key is available.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from hardware import LEMHardwareController

logger = logging.getLogger(__name__)

# -- Configuration ---------------------------------------------------------

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
LAMP_AGENT_MODEL = os.environ.get("LAMP_AGENT_MODEL", "gpt-oss-120b")
LLM_CALL_TIMEOUT_S = 30

SOUL_PATH = Path(__file__).resolve().with_name("SOUL.md")

# -- Singleton client + cached soul ----------------------------------------

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
                "The lamp agent REQUIRES an LLM -- there is no fallback. "
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
            model=LAMP_AGENT_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            timeout=30,
        )
        elapsed = round((time.monotonic() - t0) * 1000)
        logger.info("LLM warmup complete in %dms", elapsed)
    except Exception as e:
        logger.error("LLM warmup failed: %s", e)


# -- Tool definitions exposed to the model ---------------------------------

def _build_tools(hw: LEMHardwareController) -> list[dict]:
    """Build tool definitions dynamically based on available poses."""
    pose_names = hw.get_pose_names()
    pose_enum = pose_names if pose_names else ["home"]

    return [
        {
            "type": "function",
            "function": {
                "name": "pose",
                "description": (
                    "Move the lamp arm to a named pose or play an animation. "
                    f"Available poses: {', '.join(pose_enum)}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "enum": pose_enum,
                            "description": "Name of the pose or animation to execute",
                        },
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_color",
                "description": "Set the lamp's RGB light color. Color persists until changed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "r": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Red channel (0-255)"},
                        "g": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Green channel (0-255)"},
                        "b": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Blue channel (0-255)"},
                    },
                    "required": ["r", "g", "b"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "set_brightness",
                "description": "Set overall light brightness. Persists until changed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "brightness": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "Brightness level (0.0 = off, 1.0 = full)",
                        },
                    },
                    "required": ["brightness"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "flash",
                "description": "Flash a color briefly then return to the previous color. Good for reactions and acknowledgments.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "r": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Red channel"},
                        "g": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Green channel"},
                        "b": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Blue channel"},
                        "duration_ms": {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 3000,
                            "description": "Flash duration in milliseconds (default 500)",
                        },
                    },
                    "required": ["r", "g", "b"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pulse",
                "description": "Pulse/breathe effect with a color. Good for ambient and emotional expressions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "r": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Red channel"},
                        "g": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Green channel"},
                        "b": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Blue channel"},
                        "cycles": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "description": "Number of pulse cycles (default 3)",
                        },
                        "period_ms": {
                            "type": "integer",
                            "minimum": 200,
                            "maximum": 3000,
                            "description": "Duration of one cycle in ms (default 800)",
                        },
                    },
                    "required": ["r", "g", "b"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "done",
                "description": "Signal that the instruction has been fully completed. MUST be called when finished.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "detail": {"type": "string", "description": "Brief summary of what was done"},
                    },
                    "required": ["detail"],
                },
            },
        },
    ]


def _load_soul() -> str:
    try:
        return SOUL_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "You are an expressive robotic lamp arm. Communicate with posture and color."


def _build_system_prompt(soul: str, hw: LEMHardwareController) -> str:
    state_summary = (
        f"Current joint positions: {json.dumps({k: round(v, 2) for k, v in hw.current_joints.items()})}\n"
        f"Current color: {json.dumps(hw.current_color)}\n"
        f"Current brightness: {hw.brightness}\n"
        f"Available poses: {', '.join(hw.get_pose_names())}"
    )
    return (
        f"{soul}\n\n"
        f"## Current Hardware State\n{state_summary}\n\n"
        f"## Rules\n"
        f"- You have tools to control the lamp arm and light.\n"
        f"- Use `pose` to move the arm to expressive positions.\n"
        f"- Use `set_color`, `flash`, or `pulse` for light effects.\n"
        f"- Execute the instruction by calling the appropriate tools.\n"
        f"- You MUST call the `done` tool as your final tool call to signal completion.\n"
        f"- Be decisive. Prefer one or two tool calls plus `done` over many iterations.\n"
        f"- If the instruction is ambiguous, pick the most reasonable interpretation.\n"
        f"- Do NOT respond with plain text. Always use tool calls.\n"
    )


def _execute_tool_call(
    name: str,
    args: dict[str, Any],
    hw: LEMHardwareController,
) -> str:
    """Execute a single tool call against the hardware. Returns a result string."""
    try:
        if name == "pose":
            pose_name = str(args["name"])
            result = hw.move_to_pose(pose_name)
            return result

        elif name == "set_color":
            r, g, b = int(args["r"]), int(args["g"]), int(args["b"])
            hw.set_color(r, g, b)
            return f"Color set to R={r} G={g} B={b}"

        elif name == "set_brightness":
            brightness = float(args["brightness"])
            hw.set_brightness(brightness)
            return f"Brightness set to {brightness}"

        elif name == "flash":
            r, g, b = int(args["r"]), int(args["g"]), int(args["b"])
            duration_ms = int(args.get("duration_ms", 500))
            hw.flash(r, g, b, duration_ms)
            return f"Flashed R={r} G={g} B={b} for {duration_ms}ms"

        elif name == "pulse":
            r, g, b = int(args["r"]), int(args["g"]), int(args["b"])
            cycles = int(args.get("cycles", 3))
            period_ms = int(args.get("period_ms", 800))
            hw.pulse(r, g, b, cycles, period_ms)
            return f"Pulsed R={r} G={g} B={b} for {cycles} cycles"

        elif name == "done":
            return f"DONE: {args.get('detail', 'completed')}"

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        logger.error("Tool execution error (%s): %s", name, e)
        return f"Error executing {name}: {e}"


def _run_llm_loop(
    instruction: str,
    hw: LEMHardwareController,
    max_iterations: int,
    time_budget_ms: int,
) -> dict[str, Any]:
    """Run the LLM-backed agent loop with conversation history. Returns rich result."""
    client = _get_client()
    soul = _get_soul()
    tools = _build_tools(hw)
    start_time = time.monotonic()
    deadline_s = time_budget_ms / 1000.0
    execution_log: list[dict[str, Any]] = []

    # Build conversation with accumulated history
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(soul, hw)},
        {"role": "user", "content": instruction},
    ]

    for iteration in range(max_iterations):
        elapsed = time.monotonic() - start_time
        if elapsed >= deadline_s:
            # If we executed tools, the work was done -- just couldn't call done in time
            status = "ok" if execution_log else "timeout"
            logger.warning("Agent loop hit time budget (%dms) at iteration %d (status=%s)", time_budget_ms, iteration, status)
            return {
                "status": status,
                "detail": f"time budget reached after {iteration} iterations, {len(execution_log)} tools executed",
                "execution_log": execution_log,
                "iterations": iteration,
                "elapsed_ms": round(elapsed * 1000),
                "hw_state": {"joints": dict(hw.current_joints), "color": dict(hw.current_color)},
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
                "hw_state": {"joints": dict(hw.current_joints), "color": dict(hw.current_color)},
            }

        try:
            response = client.chat.completions.create(
                model=LAMP_AGENT_MODEL,
                messages=messages,
                tools=tools,
                tool_choice="required",
                timeout=api_timeout,
            )
        except Exception as e:
            logger.error("LLM call failed at iteration %d: %s", iteration, e)
            raise RuntimeError(
                f"LLM call failed at iteration {iteration}: {e}. "
                "The lamp agent REQUIRES an LLM -- there is no fallback."
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
            result = _execute_tool_call(fn_name, fn_args, hw)

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
                    "hw_state": {"joints": dict(hw.current_joints), "color": dict(hw.current_color)},
                }

    elapsed = time.monotonic() - start_time
    logger.warning("Agent loop hit max iterations (%d)", max_iterations)
    return {
        "status": "timeout",
        "detail": f"max iterations ({max_iterations}) reached without calling done",
        "execution_log": execution_log,
        "iterations": max_iterations,
        "elapsed_ms": round(elapsed * 1000),
        "hw_state": {"joints": dict(hw.current_joints), "color": dict(hw.current_color)},
    }


def run_agent_loop(
    instruction: str,
    hw: LEMHardwareController,
    max_iterations: int = 10,
    time_budget_ms: int = 15000,
) -> dict[str, Any]:
    """Public entry point for the agent loop.

    Note: planner parameter removed -- agent handles all decisions now.
    Kept backward-compatible by accepting **kwargs.
    """
    logger.info("Agent loop starting: instruction=%r, max_iter=%d, budget=%dms",
                instruction, max_iterations, time_budget_ms)
    return _run_llm_loop(instruction, hw, max_iterations, time_budget_ms)

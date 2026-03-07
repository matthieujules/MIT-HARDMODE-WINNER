"""Layer 2 agent loop for the Lamp device.

Receives a spawn instruction from the control plane, uses a fast LLM
(Cerebras Qwen) to decide hardware actions, then executes them via
LEMHardwareController.  Falls back to the deterministic InstructionPlanner
when no API key is available.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from hardware import LEMHardwareController
from planner import ArmPlan, InstructionPlanner

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
LAMP_AGENT_MODEL = os.environ.get("LAMP_AGENT_MODEL", "gpt-oss-120b")
LLM_CALL_TIMEOUT_S = 30

SOUL_PATH = Path(__file__).resolve().with_name("SOUL.md")

# ── Singleton client + cached soul ───────────────────────────────

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
                "The lamp agent REQUIRES an LLM — there is no fallback. "
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

# ── Tool definitions exposed to the model ────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_color",
            "description": "Set the LEMP RGB light color.",
            "parameters": {
                "type": "object",
                "properties": {
                    "r": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Red channel"},
                    "g": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Green channel"},
                    "b": {"type": "integer", "minimum": 0, "maximum": 255, "description": "Blue channel"},
                },
                "required": ["r", "g", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": "Set overall light brightness (0.0-1.0).",
            "parameters": {
                "type": "object",
                "properties": {
                    "brightness": {"type": "number", "minimum": 0.0, "maximum": 1.0, "description": "Brightness level"},
                },
                "required": ["brightness"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_joint_angles",
            "description": "Set all four SO-101 joint targets in degrees.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_yaw": {"type": "number", "description": "Base yaw angle in degrees"},
                    "shoulder": {"type": "number", "description": "Shoulder angle in degrees"},
                    "elbow": {"type": "number", "description": "Elbow angle in degrees"},
                    "wrist": {"type": "number", "description": "Wrist angle in degrees"},
                },
                "required": ["base_yaw", "shoulder", "elbow", "wrist"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_to_preset",
            "description": "Move the arm to a named preset pose: home, focus, relax, alert, or curious.",
            "parameters": {
                "type": "object",
                "properties": {
                    "preset": {
                        "type": "string",
                        "enum": ["home", "focus", "relax", "alert", "curious"],
                        "description": "Preset name",
                    },
                },
                "required": ["preset"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emote",
            "description": "Express an emotion through arm pose and light. Maps to presets: focus, relax, alert, curious, home. For emotions not in this list, the closest preset is used.",
            "parameters": {
                "type": "object",
                "properties": {
                    "emotion": {"type": "string", "description": "The emotion to express — maps to closest preset (focus, relax, alert, curious, home)"},
                },
                "required": ["emotion"],
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
        f"Current joint angles: {json.dumps(hw.current_joints)}\n"
        f"Current color: {json.dumps(hw.current_color)}"
    )
    return (
        f"{soul}\n\n"
        f"## Current Hardware State\n{state_summary}\n\n"
        f"## Rules\n"
        f"- You have tools to control the lamp arm and light.\n"
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
    planner: InstructionPlanner,
) -> str:
    """Execute a single tool call against the hardware. Returns a result string."""
    try:
        if name == "set_color":
            color = {"r": int(args["r"]), "g": int(args["g"]), "b": int(args["b"])}
            plan = ArmPlan(
                raw_instruction=f"set_color({color})",
                joints=dict(hw.current_joints),
                color=color,
                brightness=1.0,
                duration_ms=500,
            )
            hw.apply_plan(plan)
            return f"Color set to R={color['r']} G={color['g']} B={color['b']}"

        elif name == "set_brightness":
            brightness = float(args["brightness"])
            plan = ArmPlan(
                raw_instruction=f"set_brightness({brightness})",
                joints=dict(hw.current_joints),
                color=dict(hw.current_color),
                brightness=brightness,
                duration_ms=300,
            )
            hw.apply_plan(plan)
            return f"Brightness set to {brightness}"

        elif name == "set_joint_angles":
            joints = {
                "base_yaw": float(args["base_yaw"]),
                "shoulder": float(args["shoulder"]),
                "elbow": float(args["elbow"]),
                "wrist": float(args["wrist"]),
            }
            plan = ArmPlan(
                raw_instruction=f"set_joint_angles({joints})",
                joints=joints,
                color=dict(hw.current_color),
                brightness=1.0,
                duration_ms=1000,
            )
            hw.apply_plan(plan)
            return f"Joints set to {joints}"

        elif name == "move_to_preset":
            preset = str(args["preset"])
            plan = planner.plan(preset, hw.current_joints, hw.current_color)
            hw.apply_plan(plan)
            return f"Moved to preset '{preset}'"

        elif name == "emote":
            emotion = str(args["emotion"])
            plan = planner.plan(emotion, hw.current_joints, hw.current_color)
            hw.apply_plan(plan)
            return f"Emoted '{emotion}'"

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
    planner: InstructionPlanner,
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
        {"role": "system", "content": _build_system_prompt(soul, hw)},
        {"role": "user", "content": instruction},
    ]

    for iteration in range(max_iterations):
        elapsed = time.monotonic() - start_time
        if elapsed >= deadline_s:
            # If we executed tools, the work was done — just couldn't call done in time
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
                tools=TOOLS,
                tool_choice="required",
                timeout=api_timeout,
            )
        except Exception as e:
            logger.error("LLM call failed at iteration %d: %s", iteration, e)
            raise RuntimeError(
                f"LLM call failed at iteration {iteration}: {e}. "
                "The lamp agent REQUIRES an LLM — there is no fallback."
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
            result = _execute_tool_call(fn_name, fn_args, hw, planner)

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
    planner: InstructionPlanner,
    max_iterations: int = 10,
    time_budget_ms: int = 15000,
) -> dict[str, Any]:
    """Public entry point for the agent loop."""
    logger.info("Agent loop starting: instruction=%r, max_iter=%d, budget=%dms",
                instruction, max_iterations, time_budget_ms)
    return _run_llm_loop(instruction, hw, planner, max_iterations, time_budget_ms)

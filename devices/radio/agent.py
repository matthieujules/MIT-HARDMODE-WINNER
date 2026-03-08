"""Layer 2 agent loop for the Radio device.

Receives a spawn instruction from the control plane, uses a fast LLM
(Cerebras gpt-oss-120b) to decide audio actions, then executes them via
the runtime.  The agent selects clips directly — no secondary LLM.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# -- Configuration ---------------------------------------------------------

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
RADIO_AGENT_MODEL = os.environ.get("RADIO_AGENT_MODEL", "gpt-oss-120b")
LLM_CALL_TIMEOUT_S = 30

SOUL_PATH = Path(__file__).resolve().with_name("SOUL.md")

# -- Singleton client + cached soul ----------------------------------------

_client = None
_soul_text = None
_clip_catalog = None


def _get_client():
    """Lazy-init singleton OpenAI client."""
    global _client
    if _client is None:
        from openai import OpenAI
        if not CEREBRAS_API_KEY:
            raise RuntimeError(
                "FATAL: CEREBRAS_API_KEY is not set. "
                "The radio agent REQUIRES an LLM -- there is no fallback. "
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


def _get_clip_catalog() -> list[dict[str, str]]:
    """Lazy-load clip catalog from brain.py."""
    global _clip_catalog
    if _clip_catalog is None:
        try:
            from brain import get_clip_catalog
            _clip_catalog = get_clip_catalog()
        except Exception as e:
            logger.warning("Failed to load clip catalog: %s", e)
            _clip_catalog = []
    return _clip_catalog


def warmup():
    """Send a tiny request to warm up the Cerebras connection.

    Call this during boot so the first real instruction doesn't pay cold-start cost.
    """
    try:
        client = _get_client()
        t0 = time.monotonic()
        client.chat.completions.create(
            model=RADIO_AGENT_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            timeout=30,
        )
        elapsed = round((time.monotonic() - t0) * 1000)
        logger.info("LLM warmup complete in %dms", elapsed)
    except Exception as e:
        logger.error("LLM warmup failed: %s", e)


# -- Tool definitions exposed to the model ---------------------------------

def _build_tools() -> list[dict]:
    """Build tool definitions dynamically based on available clips."""
    catalog = _get_clip_catalog()
    codes = [entry["code"] for entry in catalog]
    if not codes:
        codes = ["A", "B", "C", "D", "E", "F", "G"]

    catalog_desc = ", ".join(f"{e['code']}={e['label']}" for e in catalog) if catalog else "A-G music tracks"

    return [
        {
            "type": "function",
            "function": {
                "name": "play",
                "description": (
                    "Play one or more audio clips through the radio. "
                    "A glitch effect and dial spin play automatically before each clip (not after the last). "
                    "For communication, use 2 clips to piece together meaning (like Bumblebee). "
                    "Max 4 clips per call. "
                    f"Available: {catalog_desc}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selections": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": codes,
                            },
                            "minItems": 1,
                            "maxItems": 4,
                            "description": "Codes of audio clips to play in sequence",
                        },
                    },
                    "required": ["selections"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "stop",
                "description": "Stop any currently playing audio immediately.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "spin_dial",
                "description": (
                    "Spin the physical radio dial for visual effect. "
                    "The dial is a continuous-rotation servo that gives the radio character."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["clockwise", "counterclockwise"],
                            "description": "Direction to spin the dial",
                        },
                        "duration_seconds": {
                            "type": "number",
                            "minimum": 0.1,
                            "maximum": 3.0,
                            "description": "How long to spin (default 0.6s)",
                        },
                    },
                    "required": ["direction"],
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
        return "You are a radio that plays audio clips and music. Communicate through sound."


def _build_system_prompt(soul: str) -> str:
    catalog = _get_clip_catalog()
    if catalog:
        catalog_lines = [
            f"- {e['code']}: {e['label']} ({e['kind']})"
            for e in catalog
        ]
        catalog_text = "\n".join(catalog_lines)
    else:
        catalog_text = "(no clips loaded)"

    return (
        f"{soul}\n\n"
        f"## Available Audio Clips\n{catalog_text}\n\n"
        f"## Rules\n"
        f"- You have tools to control the radio's audio playback and physical dial.\n"
        f"- Use `play` with a clip code to play audio — pick the clip that best matches the instruction.\n"
        f"- Use `stop` to halt current playback.\n"
        f"- Use `spin_dial` for visual flair — the dial also spins automatically before clips.\n"
        f"- You MUST call the `done` tool as your final tool call to signal completion.\n"
        f"- Be decisive. Prefer one `play` call plus `done` over many iterations.\n"
        f"- If the instruction is ambiguous, pick the most reasonable interpretation.\n"
        f"- Do NOT respond with plain text. Always use tool calls.\n"
    )


def _execute_tool_call(name: str, args: dict[str, Any], runtime: Any) -> str:
    """Execute a single tool call against the runtime. Returns a result string."""
    try:
        if name == "play":
            selections = args.get("selections", [])
            # Backward compat: accept old single "selection" param
            if not selections and "selection" in args:
                selections = [str(args["selection"])]
            if not selections:
                return "Play error: no selections provided"
            result = runtime.play_codes(selections)
            catalog = _get_clip_catalog()
            parts = []
            for code in selections:
                label = next((e["label"] for e in catalog if e["code"] == code), "unknown")
                kind = next((e["kind"] for e in catalog if e["code"] == code), "unknown")
                parts.append(f"{code}={label} ({kind})")
            if result.get("ok") or result.get("raspi"):
                return f"Playing now: {', '.join(parts)}. Playback started successfully. Call done() to finish."
            else:
                return f"Play error: {result.get('error', 'unknown error')}"

        elif name == "stop":
            runtime.interrupt_playback()
            return "Playback stopped"

        elif name == "spin_dial":
            direction = str(args.get("direction", "clockwise"))
            duration = float(args.get("duration_seconds", 0.6))
            if direction == "counterclockwise":
                runtime.dial.nudge_counterclockwise(duration_seconds=duration)
            else:
                runtime.dial.nudge_clockwise(duration_seconds=duration)
            return f"Dial spun {direction} for {duration}s"

        elif name == "done":
            return f"DONE: {args.get('detail', 'completed')}"

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        logger.error("Tool execution error (%s): %s", name, e)
        return f"Error executing {name}: {e}"


def _run_llm_loop(
    instruction: str,
    runtime: Any,
    max_iterations: int,
    time_budget_ms: int,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run the LLM-backed agent loop with conversation history. Returns rich result."""
    client = _get_client()
    soul = _get_soul()
    tools = _build_tools()
    start_time = time.monotonic()
    deadline_s = time_budget_ms / 1000.0
    execution_log: list[dict[str, Any]] = []

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(soul)},
        {"role": "user", "content": instruction},
    ]

    for iteration in range(max_iterations):
        # Check if this spawn was preempted by a newer one
        if cancel_event is not None and cancel_event.is_set():
            elapsed = time.monotonic() - start_time
            logger.info("Agent loop cancelled at iteration %d (preempted)", iteration)
            return {
                "status": "cancelled",
                "detail": f"preempted at iteration {iteration}",
                "execution_log": execution_log,
                "iterations": iteration,
                "elapsed_ms": round(elapsed * 1000),
            }

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
                model=RADIO_AGENT_MODEL,
                messages=messages,
                tools=tools,
                tool_choice="required",
                timeout=api_timeout,
            )
        except Exception as e:
            logger.error("LLM call failed at iteration %d: %s", iteration, e)
            raise RuntimeError(
                f"LLM call failed at iteration {iteration}: {e}. "
                "The radio agent REQUIRES an LLM -- there is no fallback."
            ) from e

        message = response.choices[0].message

        if not message.tool_calls:
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
            result = _execute_tool_call(fn_name, fn_args, runtime)

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
    runtime: Any,
    max_iterations: int = 10,
    time_budget_ms: int = 15000,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Public entry point for the agent loop."""
    logger.info("Agent loop starting: instruction=%r, max_iter=%d, budget=%dms",
                instruction, max_iterations, time_budget_ms)
    return _run_llm_loop(instruction, runtime, max_iterations, time_budget_ms, cancel_event)

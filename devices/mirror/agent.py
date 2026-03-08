"""Layer 2 agent loop for the Mirror device.

Receives a spawn instruction from the control plane, uses a fast LLM
(Cerebras Qwen) to decide display actions, then executes the full pipeline:
camera capture -> planner -> image generation -> display.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from PIL import ImageOps

try:
    from .camera import MirrorCamera
    from .display import MirrorDisplay
    from .image_generation import MirrorImageGenerator
    from .planner import MirrorInstructionPlanner
except ImportError:
    from camera import MirrorCamera
    from display import MirrorDisplay
    from image_generation import MirrorImageGenerator
    from planner import MirrorInstructionPlanner

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
MIRROR_AGENT_MODEL = os.environ.get("MIRROR_AGENT_MODEL", "gpt-oss-120b")
LLM_CALL_TIMEOUT_S = 30

SOUL_PATH = Path(__file__).resolve().with_name("SOUL.md")
IMAGES_DIR = Path(__file__).resolve().with_name("images")

# ── Preset image catalog (built from images/ directory) ──────────

def _scan_presets() -> dict[str, Path]:
    """Scan images/ directory and build name→path mapping.

    Filenames like '01_outfit selection white scarf.png' become
    'outfit selection white scarf' (strip number prefix, strip extension).
    """
    presets: dict[str, Path] = {}
    if not IMAGES_DIR.is_dir():
        return presets
    for p in sorted(IMAGES_DIR.iterdir()):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            # Strip leading number + underscore: "01_Foo Bar.png" → "Foo Bar"
            stem = p.stem
            import re as _re
            stem = _re.sub(r"^\d+[_\-\s]*", "", stem).strip()
            if stem:
                presets[stem] = p
    return presets


PRESET_CATALOG: dict[str, Path] = _scan_presets()
logger.info("Preset images loaded: %s", list(PRESET_CATALOG.keys()))

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
                "The mirror agent REQUIRES an LLM — there is no fallback. "
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
            model=MIRROR_AGENT_MODEL,
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
            "name": "display",
            "description": (
                "Run the full mirror display pipeline: capture a camera frame, "
                "plan the visual, generate the image, and show it on the LCD screen. "
                "This is the primary action. Most instructions need exactly one display call."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "What to display — a short visual instruction like 'calm blue scene' or 'show a happy smile'",
                    },
                },
                "required": ["instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_photo",
            "description": (
                "Capture a camera frame of the user and apply a Snapchat-filter-style edit. "
                "Use for anything involving the user's appearance: makeup, accessories, "
                "costumes, hair, aging, style filters, face effects. "
                "The edit MUST preserve the user's exact position, pose, and background — "
                "think overlay effects on a mirror reflection, not a new photo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "Editing instruction. ALWAYS include: 'Keep the person's exact "
                            "position, pose, and background unchanged.' Then describe what "
                            "to add/modify ON the person. E.g. 'Add a golden crown and "
                            "royal jewelry. Keep the person's exact position, pose, and "
                            "background unchanged.'"
                        ),
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_frame",
            "description": (
                "Capture a camera frame and return metadata (source, size). "
                "Useful to check camera status before displaying. "
                "Most of the time you do NOT need this — display() and edit_photo() capture automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_original",
            "description": (
                "Show the original unedited photo on the display — the raw camera capture "
                "from the most recent edit_photo call, before any AI transformation. "
                "Use when the user wants to compare before/after or see what they actually "
                "looked like. The original is mirrored (like a real mirror reflection)."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_preset",
            "description": (
                "Show a pre-made image on the mirror display. These are high-quality "
                "pre-rendered images — use them when they match the instruction instead "
                "of generating a new image. Much faster than display or edit_photo."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the preset image to show",
                        "enum": list(PRESET_CATALOG.keys()) if PRESET_CATALOG else ["(none available)"],
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dismiss",
            "description": (
                "Clear the display back to black (invisible behind the mirror). "
                "Use when explicitly asked to turn off the display or hide the image."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
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
                    "detail": {"type": "string", "description": "Brief summary of what was displayed"},
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
        return "You are a visual companion mirror. Communicate through images on your LCD screen."


def _build_system_prompt(
    soul: str,
    camera: MirrorCamera,
    display: MirrorDisplay,
) -> str:
    state_summary = (
        f"Camera device: {camera.device}\n"
        f"Display output directory: {display.output_dir}"
    )
    return (
        f"{soul}\n\n"
        f"## Current Hardware State\n{state_summary}\n\n"
        f"## Rules\n"
        f"- You have tools to control the mirror display.\n"
        f"- Execute the instruction by calling the appropriate tools.\n"
        f"- You MUST call the `done` tool as your final tool call to signal completion.\n"
        f"- Be decisive. Most instructions need one `display` call then `done`.\n"
        f"- If the instruction is ambiguous, pick the most reasonable visual interpretation.\n"
        f"- Do NOT respond with plain text. Always use tool calls.\n"
    )


def _execute_tool_call(
    name: str,
    args: dict[str, Any],
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    skip_camera: bool,
) -> str:
    """Execute a single tool call. Returns a result string."""
    try:
        if name == "display":
            instruction = str(args.get("instruction", ""))
            if not instruction:
                return "Error: instruction is required"

            # Full pipeline: capture -> plan -> generate -> display
            if skip_camera:
                frame = camera.placeholder_frame("camera skipped")
            else:
                frame = camera.get_frame()

            plan = planner.plan(instruction)
            result = generator.generate(plan, frame)
            screen_path = display.show_generated(result.image, ttl_s=120)

            detail = (
                f"Displayed '{plan.icon_name}' ({plan.display_mode}). "
                f"Image source: {result.source}. "
                f"Saved to: {result.saved_path}. "
                f"Screen: {screen_path}"
            )
            if result.api_error:
                detail += f". API note: {result.api_error}"
            return detail

        elif name == "edit_photo":
            prompt = str(args.get("prompt", ""))
            if not prompt:
                return "Error: prompt is required"

            # Capture camera frame then edit it directly
            if skip_camera:
                frame = camera.placeholder_frame("camera skipped")
            else:
                frame = camera.get_frame()

            result = generator.edit_frame(frame, prompt)
            screen_path = display.show_generated(result.image, ttl_s=120)

            detail = (
                f"Edited camera frame with prompt. "
                f"Image source: {result.source}. "
                f"Saved to: {result.saved_path}. "
                f"Screen: {screen_path}"
            )
            if result.api_error:
                detail += f". API note: {result.api_error}"
            return detail

        elif name == "capture_frame":
            if skip_camera:
                frame = camera.placeholder_frame("camera skipped")
            else:
                frame = camera.get_frame()
            return (
                f"Frame captured. Source: {frame.source}. "
                f"Size: {frame.metadata.get('width', '?')}x{frame.metadata.get('height', '?')}"
            )

        elif name == "show_preset":
            preset_name = str(args.get("name", ""))
            if preset_name not in PRESET_CATALOG:
                available = ", ".join(PRESET_CATALOG.keys()) or "(none)"
                return f"Error: unknown preset '{preset_name}'. Available: {available}"
            from PIL import Image
            preset_img = Image.open(PRESET_CATALOG[preset_name]).convert("RGB")
            screen_path = display.show_generated(preset_img, ttl_s=120)
            return f"Showing preset '{preset_name}' on display. Screen: {screen_path}. Call done() to finish."

        elif name == "show_original":
            if generator.last_original_path is None or not generator.last_original_path.exists():
                return "No original photo available — no edit has been done yet this session."
            from PIL import Image
            original = Image.open(generator.last_original_path).convert("RGB")
            # Mirror it so it looks like a real reflection
            mirrored = ImageOps.mirror(original)
            display.show_generated(mirrored, ttl_s=120)
            return f"Showing original (pre-edit) photo on display. Source: {generator.last_original_path}"

        elif name == "dismiss":
            display.show_mirror()
            return "Display cleared to black."

        elif name == "done":
            return f"DONE: {args.get('detail', 'completed')}"

        else:
            return f"Unknown tool: {name}"

    except Exception as e:
        logger.error("Tool execution error (%s): %s", name, e)
        return f"Error executing {name}: {e}"


def _run_llm_loop(
    instruction: str,
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    max_iterations: int,
    time_budget_ms: int,
    skip_camera: bool,
) -> dict[str, Any]:
    """Run the LLM-backed agent loop with conversation history. Returns rich result."""
    client = _get_client()
    soul = _get_soul()
    start_time = time.monotonic()
    deadline_s = time_budget_ms / 1000.0
    execution_log: list[dict[str, Any]] = []

    # Build conversation with accumulated history
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _build_system_prompt(soul, camera, display)},
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
                model=MIRROR_AGENT_MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="required",
                timeout=api_timeout,
            )
        except Exception as e:
            logger.error("LLM call failed at iteration %d: %s", iteration, e)
            raise RuntimeError(
                f"LLM call failed at iteration {iteration}: {e}. "
                "The mirror agent REQUIRES an LLM — there is no fallback."
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
            result = _execute_tool_call(fn_name, fn_args, camera, planner, generator, display, skip_camera)

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
    camera: MirrorCamera,
    planner: MirrorInstructionPlanner,
    generator: MirrorImageGenerator,
    display: MirrorDisplay,
    max_iterations: int = 10,
    time_budget_ms: int = 60000,
    skip_camera: bool = False,
) -> dict[str, Any]:
    """Public entry point for the agent loop."""
    logger.info("Agent loop starting: instruction=%r, max_iter=%d, budget=%dms",
                instruction, max_iterations, time_budget_ms)
    return _run_llm_loop(instruction, camera, planner, generator, display, max_iterations, time_budget_ms, skip_camera)

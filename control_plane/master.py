"""Master reasoning engine for the ClaudeHome control plane.

Pure function module — does NOT import app.py or ConnectionManager.
Takes a StateManager and DeviceEvent, returns structured data.
Supports Anthropic (Claude) and OpenAI-compatible providers (Cerebras).
"""

import json
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

from .schemas import DeviceEvent
from .state import StateManager

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────

_MODEL = os.getenv("MASTER_MODEL", "claude-sonnet-4-6")
_PROVIDER = os.getenv("MASTER_PROVIDER", "auto")  # "anthropic", "cerebras", or "auto"
_FAST_MODE = os.getenv("MASTER_FAST_MODE", "false").lower() in ("true", "1", "yes")
_BETA_HEADER = "context-1m-2025-08-07"
_FAST_BETA = "fast-mode-2026-02-01"
_CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"


def _detect_provider() -> str:
    if _PROVIDER != "auto":
        return _PROVIDER
    if "claude" in _MODEL:
        return "anthropic"
    return "cerebras"


# ── Master tools (canonical format — Anthropic-style) ─────────────

MASTER_TOOLS = [
    {
        "name": "update_user_state",
        "description": "Update the home's inferred user state. Use when the event changes mode, mood, or energy.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Current mode: idle, focus, relax, sleep, away, etc.",
                },
                "mood": {
                    "type": "string",
                    "description": "Inferred mood: neutral, happy, stressed, tired, determined, sad, focused, relaxed, etc.",
                },
                "energy": {
                    "type": "string",
                    "description": "Energy level: low, normal, high.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "send_to_lamp",
        "description": "Send a natural language instruction to Lamp. Lamp is an expressive ambient actuator with RGB LED and servos. It communicates through color, brightness, and physical gestures. No speech.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Natural language instruction describing what Lamp should do and why.",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "send_to_mirror",
        "description": "Send a natural language instruction to Mirror. Mirror is the primary conversational companion with a camera, speaker, and tilt servo. Use for spoken responses and face-to-face interaction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Natural language instruction describing what Mirror should do and why.",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "send_to_radio",
        "description": "Send a natural language instruction to Radio. Radio is a stationary audio device with a speaker for music and speech.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Natural language instruction describing what Radio should do and why.",
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "send_to_rover",
        "description": "Send a natural language instruction to Rover. Rover is a small mobile coaster with motors that pulls a basket. The only mobile device. Optionally specify a target position on the room map.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Natural language instruction describing what Rover should do and why.",
                },
                "target": {
                    "type": "object",
                    "description": "Target position on the room map. Use a waypoint name OR explicit coordinates. Available waypoints: dock, center, desk, lamp_area, door.",
                    "properties": {
                        "waypoint": {
                            "type": "string",
                            "description": "Named waypoint: dock, center, desk, lamp_area, door",
                        },
                        "x_cm": {
                            "type": "number",
                            "description": "Explicit x coordinate in cm",
                        },
                        "y_cm": {
                            "type": "number",
                            "description": "Explicit y coordinate in cm",
                        },
                    },
                },
            },
            "required": ["instruction"],
        },
    },
    {
        "name": "no_op",
        "description": "Explicitly record that this event requires no action. Must be the only tool call.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Why no action is needed.",
                },
            },
            "required": ["reason"],
        },
    },
]

# Tool name -> device_id mapping for send_to_* tools
_SEND_TOOL_DEVICE_MAP = {
    "send_to_lamp": "lamp",
    "send_to_mirror": "mirror",
    "send_to_radio": "radio",
    "send_to_rover": "rover",
}

# ── Tool format conversion ───────────────────────────────────────


def _tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tools to OpenAI-format for Cerebras."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


# ── Prompt assembly ───────────────────────────────────────────────

_SOUL_DIR = Path(__file__).parent
_DEVICE_SOUL_DIRS = [
    Path("devices/lamp/SOUL.md"),
    Path("devices/mirror/SOUL.md"),
    Path("devices/radio/SOUL.md"),
    Path("devices/rover/SOUL.md"),
]

# Truncation limits per spec §13
_LIMITS = {
    "soul": 4000,
    "user_md": 8000,
    "devices_json": 4000,
    "state_json": 4000,
    "events": 8000,
    "total": 40000,
}


def assemble_prompt(state_manager: StateManager, triggering_event: DeviceEvent) -> dict:
    """Build the master prompt messages per spec §13 Assembly Order.

    Returns a dict with "system" and "messages" keys.
    """
    sections: list[str] = []

    # 1. Master SOUL.md (goes into system prompt, handled separately)
    master_soul = state_manager.read_soul(str(_SOUL_DIR / "SOUL.md"))

    # 2. All device SOUL.md files
    for soul_path in _DEVICE_SOUL_DIRS:
        soul_text = state_manager.read_soul(str(soul_path))
        if soul_text:
            sections.append(f"## Device Personality: {soul_path.parent.name}\n{soul_text}")

    # 3. user.md
    user_md = state_manager.read_user_md()
    if user_md:
        sections.append(f"## User Profile\n{user_md[:_LIMITS['user_md']]}")

    # 4. devices.json
    devices = state_manager.read_devices()
    devices_text = json.dumps(
        [d.model_dump(mode="json") for d in devices], default=str, indent=2
    )
    sections.append(f"## Device Registry\n```json\n{devices_text[:_LIMITS['devices_json']]}\n```")

    # 5. state.json
    state = state_manager.read_state()
    state_text = json.dumps(state, default=str, indent=2)
    sections.append(f"## Current State\n```json\n{state_text[:_LIMITS['state_json']]}\n```")

    # 5b. Spatial map context
    room_config = state_manager.read_room_config()
    if room_config:
        spatial = state.get("spatial", {})
        spatial_lines = [
            f"Room: {room_config['width_cm']}x{room_config['height_cm']} cm",
            "Furniture: " + ", ".join(
                f"{f['label']} ({f['x_cm']},{f['y_cm']})"
                for f in room_config.get("furniture", [])
            ),
            "Device positions:",
        ]
        for dev_id, dev in spatial.get("devices", {}).items():
            spatial_lines.append(
                f"  {dev_id}: ({dev.get('x_cm')},{dev.get('y_cm')}) "
                f"{'fixed' if dev.get('fixed') else 'mobile'} status={dev.get('status','idle')}"
            )
        user = spatial.get("user", {})
        if user:
            spatial_lines.append(f"User: ({user.get('x_cm')},{user.get('y_cm')}) {user.get('label','')}")
        spatial_lines.append(
            "Rover waypoints: " + ", ".join(
                f"{wp['id']}({wp['x_cm']},{wp['y_cm']})"
                for wp in room_config.get("waypoints", [])
            )
        )
        sections.append("## Spatial Map\n" + "\n".join(spatial_lines))

    # 6. Recent events
    recent_events = state_manager.read_recent_events(max_chars=_LIMITS["events"])
    if recent_events:
        events_text = "\n".join(json.dumps(e, default=str) for e in recent_events)
        sections.append(f"## Recent Events\n```\n{events_text}\n```")

    # 7. Triggering event
    event_text = json.dumps(triggering_event.model_dump(mode="json"), default=str, indent=2)
    sections.append(f"## Triggering Event\n```json\n{event_text}\n```")

    # Assemble user content with total cap
    user_content = "\n\n".join(sections)
    if len(user_content) > _LIMITS["total"]:
        user_content = user_content[:_LIMITS["total"]]
        logger.warning("Prompt truncated to %d chars", _LIMITS["total"])

    return {
        "system": master_soul,
        "messages": [{"role": "user", "content": user_content}],
    }


# ── API calls ─────────────────────────────────────────────────────


def _call_anthropic(prompt: dict, tools: list[dict]) -> dict:
    """Call Anthropic Claude API. Returns normalized response dict."""
    import anthropic

    client = anthropic.Anthropic()
    used_fast = False

    if _FAST_MODE:
        try:
            betas = [_FAST_BETA, _BETA_HEADER]
            response = client.beta.messages.create(
                model=_MODEL,
                max_tokens=4096,
                system=prompt["system"],
                messages=prompt["messages"],
                tools=tools,
                tool_choice={"type": "any"},
                speed="fast",
                betas=betas,
            )
            used_fast = True
        except anthropic.RateLimitError:
            logger.warning("Fast mode rate-limited, falling back to standard endpoint")

    if not used_fast:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            system=prompt["system"],
            messages=prompt["messages"],
            tools=tools,
            tool_choice={"type": "any"},
            extra_headers={"anthropic-beta": _BETA_HEADER},
        )
    logger.info(
        "Anthropic API call (%s): %d input tokens, %d output tokens",
        "fast" if used_fast else "normal",
        response.usage.input_tokens,
        response.usage.output_tokens,
    )

    # Normalize to common format
    raw_content = []
    for block in response.content:
        if block.type == "text":
            raw_content.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            raw_content.append({"type": "tool_use", "name": block.name, "input": block.input, "id": block.id})

    return {
        "raw_content": raw_content,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "stop_reason": response.stop_reason,
    }


def _call_cerebras(prompt: dict, tools: list[dict]) -> dict:
    """Call Cerebras API (OpenAI-compatible). Returns normalized response dict."""
    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("CEREBRAS_API_KEY"),
        base_url=_CEREBRAS_BASE_URL,
    )

    # Build messages: system + user
    messages = [{"role": "system", "content": prompt["system"]}]
    messages.extend(prompt["messages"])

    openai_tools = _tools_to_openai(tools)

    response = client.chat.completions.create(
        model=_MODEL,
        max_tokens=4096,
        messages=messages,
        tools=openai_tools,
        tool_choice="required",
    )

    choice = response.choices[0]
    usage = response.usage

    logger.info(
        "Cerebras API call: %d input tokens, %d output tokens",
        usage.prompt_tokens,
        usage.completion_tokens,
    )

    # Normalize to common format
    raw_content = []
    if choice.message.content:
        raw_content.append({"type": "text", "text": choice.message.content})
    if choice.message.tool_calls:
        for tc in choice.message.tool_calls:
            args = tc.function.arguments
            if isinstance(args, str):
                args = json.loads(args)
            raw_content.append({
                "type": "tool_use",
                "name": tc.function.name,
                "input": args,
                "id": tc.id,
            })

    return {
        "raw_content": raw_content,
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "stop_reason": choice.finish_reason,
    }


def call_master(prompt: dict, tools: list[dict] = MASTER_TOOLS) -> dict:
    """Call the master model. Auto-detects provider. Returns normalized response."""
    provider = _detect_provider()
    if provider == "anthropic":
        return _call_anthropic(prompt, tools)
    else:
        return _call_cerebras(prompt, tools)


# ── Response parsing ──────────────────────────────────────────────

_KNOWN_TOOLS = {"update_user_state", "send_to_lamp", "send_to_mirror",
                "send_to_radio", "send_to_rover", "no_op"}


def parse_tool_calls(response: dict) -> list[dict]:
    """Extract and validate tool_use blocks from the normalized response.

    Returns list of dicts: {"tool": name, "input": params, "id": tool_use_id}
    Invalid tool calls are logged and skipped.
    """
    calls = []
    for block in response["raw_content"]:
        if block.get("type") != "tool_use":
            continue
        name = block["name"]
        if name not in _KNOWN_TOOLS:
            logger.warning("Unknown tool call rejected: %s", name)
            continue
        if name in _SEND_TOOL_DEVICE_MAP and "instruction" not in block["input"]:
            logger.warning("Missing 'instruction' parameter for %s, rejected", name)
            continue
        calls.append({
            "tool": name,
            "input": block["input"],
            "id": block["id"],
        })
    return calls


# ── State update application ─────────────────────────────────────


def apply_state_update(state_manager: StateManager, tool_calls: list[dict]) -> None:
    """Merge all update_user_state calls into one state patch and persist.

    Per spec §19 step 3: apply all state updates first, merge into one patch.
    Per spec §19 step 4: persist state.json before dispatching device instructions.
    """
    patch = {}
    for tc in tool_calls:
        if tc["tool"] == "update_user_state":
            patch.update(tc["input"])
    if patch:
        state_manager.write_state(patch)
        logger.info("State updated by master: %s", patch)


# ── Device instruction extraction ────────────────────────────────


def extract_device_instructions(tool_calls: list[dict]) -> list[tuple[str, str]]:
    """Return (device_id, instruction) pairs from send_to_* tool calls.

    Preserves model order for sequential dispatch to the same device.
    """
    instructions = []
    for tc in tool_calls:
        if tc["tool"] in _SEND_TOOL_DEVICE_MAP:
            device_id = _SEND_TOOL_DEVICE_MAP[tc["tool"]]
            instruction = tc["input"]["instruction"]
            instructions.append((device_id, instruction))
    return instructions


def extract_rover_targets(tool_calls: list[dict]) -> list[dict]:
    """Return target dicts from send_to_rover tool calls that include a target."""
    targets = []
    for tc in tool_calls:
        if tc["tool"] == "send_to_rover" and "target" in tc["input"]:
            targets.append(tc["input"]["target"])
    return targets


# ── Full master turn orchestration ────────────────────────────────


def execute_master_turn(state_manager: StateManager, event: DeviceEvent) -> dict:
    """Orchestrate a full master reasoning turn.

    Per spec §19 Execution Semantics:
    1. Parse all tool_use blocks in response order.
    2. If no_op is present, it must be the only tool call. Ignore any others.
    3-4. Apply state updates and persist (done by caller via apply_state_update).
    5-7. Device instructions extracted (dispatch done by app.py).

    Returns a dict with tool_calls and full turn metadata for logging.
    """
    # Snapshot state before the call
    state_before = state_manager.read_state()

    # Assemble prompt
    prompt = assemble_prompt(state_manager, event)

    # Call master model with timing
    t0 = time.time()
    response = call_master(prompt)
    latency_ms = round((time.time() - t0) * 1000)

    # Parse and validate
    tool_calls = parse_tool_calls(response)

    is_no_op = False
    if not tool_calls:
        logger.warning("Master returned no valid tool calls")
        tool_calls = []
    else:
        no_ops = [tc for tc in tool_calls if tc["tool"] == "no_op"]
        if no_ops:
            reason = no_ops[0]["input"].get("reason", "no reason given")
            logger.info("Master decided no_op: %s", reason)
            tool_calls = [no_ops[0]]
            is_no_op = True

    return {
        "tool_calls": tool_calls,
        "turn_metadata": {
            "trigger": event.model_dump(mode="json"),
            "state_before": state_before,
            "model": _MODEL,
            "provider": _detect_provider(),
            "input_tokens": response["input_tokens"],
            "output_tokens": response["output_tokens"],
            "latency_ms": latency_ms,
            "stop_reason": response["stop_reason"],
            "raw_content": response["raw_content"],
            "is_no_op": is_no_op,
        },
    }

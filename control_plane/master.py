"""Master reasoning engine for the ClaudeHome control plane.

Pure function module — does NOT import app.py or ConnectionManager.
Takes a StateManager and DeviceEvent, returns structured data.
"""

import json
import logging
import os
from pathlib import Path
from uuid import uuid4

import anthropic

from .schemas import DeviceEvent
from .state import StateManager

logger = logging.getLogger(__name__)

# ── Master tools (Anthropic API format) ───────────────────────────

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
        "description": "Send a natural language instruction to Rover. Rover is a small mobile coaster with motors that pulls a basket. The only mobile device.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "Natural language instruction describing what Rover should do and why.",
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


def assemble_prompt(state_manager: StateManager, triggering_event: DeviceEvent) -> list[dict]:
    """Build the master prompt messages per spec §13 Assembly Order.

    Returns a list of message dicts for the Anthropic API.
    The system prompt is the master SOUL.md.
    The user message contains all context sources + triggering event.
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


# ── API call ──────────────────────────────────────────────────────

_MODEL = "claude-opus-4-6"
_BETA_HEADER = "context-1m-2025-08-07"


def call_master(prompt: dict, tools: list[dict] = MASTER_TOOLS) -> anthropic.types.Message:
    """Call Claude Opus 4.6 with the assembled prompt and tools.

    Uses tool_choice={"type": "any"} to force tool use (no prose).
    """
    client = anthropic.Anthropic()
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
        "Master API call: %d input tokens, %d output tokens",
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    return response


# ── Response parsing ──────────────────────────────────────────────

_KNOWN_TOOLS = {"update_user_state", "send_to_lamp", "send_to_mirror",
                "send_to_radio", "send_to_rover", "no_op"}


def parse_tool_calls(response: anthropic.types.Message) -> list[dict]:
    """Extract and validate tool_use blocks from the API response.

    Returns list of dicts: {"tool": name, "input": params, "id": tool_use_id}
    Invalid tool calls are logged and skipped.
    """
    calls = []
    for block in response.content:
        if block.type != "tool_use":
            continue
        name = block.name
        if name not in _KNOWN_TOOLS:
            logger.warning("Unknown tool call rejected: %s", name)
            continue
        if name in _SEND_TOOL_DEVICE_MAP and "instruction" not in block.input:
            logger.warning("Missing 'instruction' parameter for %s, rejected", name)
            continue
        calls.append({
            "tool": name,
            "input": block.input,
            "id": block.id,
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


# ── Full master turn orchestration ────────────────────────────────


def execute_master_turn(state_manager: StateManager, event: DeviceEvent) -> list[dict]:
    """Orchestrate a full master reasoning turn.

    Per spec §19 Execution Semantics:
    1. Parse all tool_use blocks in response order.
    2. If no_op is present, it must be the only tool call. Ignore any others.
    3-4. Apply state updates and persist (done by caller via apply_state_update).
    5-7. Device instructions extracted (dispatch done by app.py).

    Returns the validated tool_calls list.
    """
    # Assemble prompt
    prompt = assemble_prompt(state_manager, event)

    # Call master model
    response = call_master(prompt)

    # Parse and validate
    tool_calls = parse_tool_calls(response)

    if not tool_calls:
        logger.warning("Master returned no valid tool calls")
        return []

    # Check for no_op (must be only call)
    no_ops = [tc for tc in tool_calls if tc["tool"] == "no_op"]
    if no_ops:
        reason = no_ops[0]["input"].get("reason", "no reason given")
        logger.info("Master decided no_op: %s", reason)
        return [no_ops[0]]

    return tool_calls

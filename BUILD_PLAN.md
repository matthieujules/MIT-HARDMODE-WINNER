# ClaudeHome Control Plane — Agent Team Build Plan

This document is the execution plan for building the control plane. It is written for the orchestrating agent that will coordinate the agent team.

The **canonical reference** for all implementation details is `IMPLEMENTATION_SPEC.md`. This document tells you WHAT to build and WHERE to look in the spec for HOW. When in doubt, the spec wins.

## What We Are Building

The **control plane only** — a FastAPI server on the laptop that:
1. Receives events (voice transcripts, heartbeats, action results) from devices over WebSocket
2. Routes simple commands deterministically (regex match → direct `command` message)
3. Sends ambiguous/complex events to Claude Opus 4.6 for reasoning
4. Dispatches natural language instructions to devices as `spawn` messages

**We stop at the WebSocket boundary.** When the control plane sends a `spawn` or `command` JSON message down a WebSocket to a device, our job is done. We do not build device-side code (hardware drivers, device agent loops, model clients).

## What We Are NOT Building

- `device_runtime/` — agent.py, hardware.py, model_client.py, ws_client.py, main.py (all out of scope)
- `voice/voice.py` — VAD + Whisper pipeline (deferred)
- `control_plane/vision.py` — camera frame analysis (deferred, but integration hooks must exist — see Vision note below)
- Test frameworks, mocks, virtual harness — verification uses real server + curl
- Any UI or frontend

## Vision Integration Note

Vision is deferred but the architecture MUST support it cleanly. When `vision.py` is added later, it plugs into `process_event` with zero changes to other modules. The hooks are:

| Hook | Where | Spec Reference |
|------|-------|----------------|
| `DeviceEvent(kind="frame")` schema | `schemas.py` | Spec §9 "Per-Kind Payload Schemas" — `frame` payload |
| `DeviceEvent(kind="vision_result")` schema | `schemas.py` | Spec §9 "Per-Kind Payload Schemas" — `vision_result` payload |
| `frame` events received and logged, no processing | `app.py` process_event | Spec §8 "Event Routing Table" — frame row |
| `vision_result` events trigger master reasoning | `app.py` process_event | Spec §8 "Event Routing Table" — vision_result row |
| `people_count` and `activity` fields in state | `data/state.json` | Spec §10 "State Model" |
| Vision analysis prompt and decision gate | Future `vision.py` | Spec §7 "Vision Pipeline" (full section) |

## Master Model

- **Model:** Claude Opus 4.6
- **API ID:** `claude-opus-4-6`
- **Beta header:** `context-1m-2025-08-07` (enables 1M token context)
- **Tool choice:** `{"type": "any"}` (force tool use, no prose responses)
- **Spec reference:** §6 "Master Model"

## Conventions

- Python 3.11+, FastAPI, Pydantic v2, anthropic SDK
- `pathlib.Path` for file paths
- `stdlib json` for serialization — no database, no ORM (Spec §12: "No ORM, no database, no task queue")
- `threading.Lock` on state write operations
- `request_id` generated as `f"req_{uuid4().hex[:8]}"`
- All timestamps as ISO 8601 UTC strings
- Logging via `stdlib logging`, not `print()`
- Load .env with `python-dotenv`

## Phase Execution Order

```
Phase 1: schemas.py + seed data + requirements.txt
    |
    v
Phase 2A + 2B: state.py + router.py           (PARALLEL — no dependency on each other)
    |
    v
Phase 3A + 3B: app.py + master.py + SOUL.md   (PARALLEL — master depends on schemas+state only, not app)
    |
    v
Phase 4: Wire master into app.py event pipeline (depends on both 3A and 3B)
```

---

## Phase 1: Schemas + Seed Data

**Creates:**
- `control_plane/__init__.py` (empty)
- `control_plane/schemas.py`
- `data/state.json`
- `data/devices.json`
- `data/event_log.jsonl` (empty file)
- `data/user.md`
- `requirements.txt`

### Spec References

| What | Where in IMPLEMENTATION_SPEC.md |
|------|-------------------------------|
| All event payload schemas | §9 "Per-Kind Payload Schemas" — transcript, frame, vision_result, action_result, heartbeat, manual_override, tick |
| DeviceEvent envelope | §9 "DeviceEvent" — `{device_id, kind, ts, payload}` |
| Downward message schemas | §9 "Messages to Devices (Downward)" — command and spawn JSON |
| Registration schema | §9 "Registration Schema" — request/response format, actions field purpose |
| State file structure | §10 "State Model" — state.json, devices.json, event_log.jsonl, user.md |
| DeviceInfo fields | §17 "Virtual Device Fallback" — `is_virtual` flag |
| Dependencies | §12 "Dependencies" — control plane packages |

### schemas.py models

```
DeviceEvent:
  device_id: str
  kind: Literal["transcript","frame","vision_result","action_result","heartbeat","manual_override","tick"]
  ts: datetime (default=utcnow)
  payload: dict

TranscriptPayload:    { text: str }
FramePayload:         { image_b64: str, resolution: list[int], trigger: str }
VisionResultPayload:  { analysis: dict, previous_mood: str|None, source_device: str }
ActionResultPayload:  { request_id: str, status: Literal["ok","error","timeout","offline"], detail: str }
ManualOverridePayload: { target: str, type: Literal["command","spawn"], instruction: str|None, action: str|None, params: dict={} }
TickPayload:          { elapsed_since_last_interaction_s: int }

DeviceCommand:  { type: Literal["command"], action: str, params: dict={}, request_id: str }
DeviceSpawn:    { type: Literal["spawn"], instruction: str, request_id: str, max_iterations: int=10, time_budget_ms: int=15000 }

DeviceRegistration: { device_id: str, device_name: str, device_type: str, capabilities: list[str], actions: list[str], ip: str }
DeviceInfo:         extends DeviceRegistration + { status: str="online", last_seen: datetime|None, is_virtual: bool=False }
```

### Seed data
- `data/state.json`: `{"mode":"idle","mood":"neutral","energy":"normal","people_count":0,"activity":null,"overrides":{},"voice_lock":{}}`
- `data/devices.json`: `[]`
- `data/user.md`: minimal demo user placeholder
- `requirements.txt`: fastapi>=0.115.0, uvicorn[standard]>=0.30.0, anthropic>=0.40.0, pydantic>=2.0, python-dotenv>=1.0.0

### Verify
```bash
pip install -r requirements.txt
python3 -c "
from control_plane.schemas import DeviceEvent, DeviceCommand, DeviceSpawn, DeviceRegistration
e = DeviceEvent(device_id='global_mic', kind='transcript', payload={'text': 'hello'})
c = DeviceCommand(type='command', action='stop', params={}, request_id='req_1')
s = DeviceSpawn(type='spawn', instruction='Focus lighting', request_id='req_2')
import json; json.load(open('data/state.json')); json.load(open('data/devices.json'))
print('PHASE 1 PASSED')
"
```

---

## Phase 2A: State Persistence

**Creates:** `control_plane/state.py`

### Spec References

| What | Where in IMPLEMENTATION_SPEC.md |
|------|-------------------------------|
| State file contents and purpose | §10 "State Model" — state.json, devices.json, event_log.jsonl, user.md |
| What master reads per turn | §13 "Assembly Order" — all sources the master prompt reads from |
| Truncation limits per source | §13 "Truncation Limits" — max chars for each file |
| Event log maintenance | §13 "Event Log Maintenance" — compaction rules, 500-line threshold |
| Device registration persistence | §9 "Registration Schema" — writes to devices.json, marks device online |

### StateManager class
- `__init__(data_dir: Path = Path("data"))` — creates dir if needed
- `read_state() -> dict`
- `write_state(patch: dict)` — merge patch into state.json
- `read_devices() -> list[DeviceInfo]`
- `register_device(reg: DeviceRegistration) -> str` — upsert, returns "registered"/"updated"
- `update_device_status(device_id, status)`
- `update_device_last_seen(device_id)`
- `get_device(device_id) -> DeviceInfo | None`
- `append_event(event: DeviceEvent)`
- `read_recent_events(max_chars=8000) -> list[dict]`
- `read_user_md() -> str`
- `read_soul(path: str) -> str` — truncates to 4000 chars
- `compact_log(max_lines=500)`
- Thread safety: `threading.Lock` on all write methods

### Verify
```bash
python3 -c "
from control_plane.state import StateManager
from control_plane.schemas import DeviceEvent, DeviceRegistration
sm = StateManager()
assert sm.read_state()['mode'] == 'idle'
sm.write_state({'mode': 'focus'}); assert sm.read_state()['mode'] == 'focus'
sm.write_state({'mode': 'idle'})
sm.register_device(DeviceRegistration(device_id='lamp', device_name='Lamp', device_type='lamp', capabilities=['light'], actions=['set_color'], ip='192.168.1.101'))
assert len(sm.read_devices()) >= 1
sm.append_event(DeviceEvent(device_id='test', kind='transcript', payload={'text': 'hi'}))
assert len(sm.read_recent_events()) >= 1
print('PHASE 2A PASSED')
"
```

---

## Phase 2B: Deterministic Router

**Creates:** `control_plane/router.py`

### Spec References

| What | Where in IMPLEMENTATION_SPEC.md |
|------|-------------------------------|
| Deterministic routing examples | §6 "Control Plane Routing" — "Lamp blue", "Mirror tilt up", "Rover stop" |
| Emergency stop pattern and behavior | §8 "Emergency Stop" — regex pattern, broadcasts command stop to Rover, bypasses voice lock |
| Voice lock mechanism | §8 "Voice Lock" — is_speaking flag, 10s timeout, cleared on action_result |
| Transcript processing order | §8 "Emergency Stop" — emergency stop check → voice lock filter → deterministic router → master queue |
| Command message format | §9 "Direct command" — `{type: "command", action, params, request_id}` |

### Contents
- `EMERGENCY_STOP_PATTERN`: compiled regex `r'\b(stop|halt|freeze|wait|no\s*no\s*no)\b'` (IGNORECASE)
- `DETERMINISTIC_PATTERNS`: list of (regex, device_id, action, param_extractor) — covers lamp colors, lamp brightness, mirror tilt, rover commands, radio volume, lights off
- `is_emergency_stop(text: str) -> bool`
- `check_voice_lock(state_manager) -> bool` — True if any device is speaking
- `set_voice_lock(device_id: str, state_manager)` — marks device as speaking in state
- `clear_voice_lock(device_id: str, state_manager)` — clears speaking flag
- `route_transcript(text: str, state_manager) -> tuple` — processing order per spec §8:
  1. Emergency stop check → `("emergency_stop",)`
  2. Voice lock check → `("dropped",)` if locked
  3. Deterministic regex → `("deterministic", device_id, action, params_dict)`
  4. Fallback → `("master",)`

### Verify
```bash
python3 -c "
from control_plane.router import route_transcript, is_emergency_stop
from control_plane.state import StateManager
sm = StateManager()
assert is_emergency_stop('stop')
assert is_emergency_stop('no no no')
assert not is_emergency_stop('hello')
r = route_transcript('lamp blue', sm); assert r[0] == 'deterministic' and r[1] == 'lamp'
r = route_transcript('stop', sm); assert r[0] == 'emergency_stop'
r = route_transcript('I need to lock in', sm); assert r[0] == 'master'
print('PHASE 2B PASSED')
"
```

---

## Phase 3A: FastAPI App

**Creates:** `control_plane/app.py`

### Spec References

| What | Where in IMPLEMENTATION_SPEC.md |
|------|-------------------------------|
| All endpoints | §9 "Endpoints" — /register, /ws/{device_id}, /events, /commands/{device_id}, /health |
| WebSocket transport | §9 "Transport" — events up, commands/spawns down, JSON text frames |
| Event routing table (ALL event kinds) | §8 "Event Routing Table" — the full routing table with handler and notes per kind |
| Registration flow | §9 "Registration Schema" — POST /register before WS connect, response format |
| Execution model (serial master) | §8 "Execution Model" — one event at a time, command queue before observation queue |
| State broadcasts (none in V1) | §8 "State Broadcasts" — V1 does not broadcast state to devices |
| Virtual device fallback | §17 "Virtual Device Fallback" — is_virtual flag, log to stdout instead of WS |
| action_result and voice lock clearing | §8 "Voice Lock" — flag set on speech dispatch, cleared on action_result or timeout |

### ConnectionManager class
- `active_connections: dict[str, WebSocket]`
- `async connect(device_id, ws)` — accept + store
- `async disconnect(device_id)` — remove + mark offline
- `async send_to_device(device_id, message: dict)` — send JSON; if virtual device, log to stdout; if disconnected, return offline status
- `async broadcast(message, device_ids=None)` — send to multiple

### Endpoints
- `GET /health` — `{"status":"ok","devices_online":N}`
- `POST /register` — accepts DeviceRegistration (§9 "Registration Schema")
- `WS /ws/{device_id}` — bidirectional, routes incoming events through process_event (§9 "Transport")
- `POST /events` — inject events, operator/testing use (§9 "Endpoints")
- `POST /commands/{device_id}` — inject commands/spawns (§9 "Endpoints")
- `GET /state` — returns state.json (convenience, not in spec)
- `GET /devices` — returns devices.json (convenience)
- `GET /events` — returns recent event log (convenience)

### process_event(event) — implements §8 "Event Routing Table"
- `transcript` → router.route_transcript() → dispatch command OR log "master not yet wired"
- `action_result` → log + clear voice lock if applicable (§8 "Voice Lock")
- `heartbeat` → update last_seen (§8 routing table)
- `manual_override` → validate + dispatch directly (§8 routing table)
- `tick` → log "master not yet wired" (wired in Phase 4)
- `vision_result` → log "master not yet wired" (wired in Phase 4)
- `frame` → log "vision pipeline not yet integrated" (deferred — §7)
- All events appended to event log

### Dispatchers
- `dispatch_command(device_id, action, params)` — builds DeviceCommand (§9 "Direct command"), sends via ConnectionManager
- `dispatch_spawn(device_id, instruction)` — builds DeviceSpawn (§9 "Spawn"), sends via ConnectionManager

### Verify
```bash
uvicorn control_plane.app:app --port 8000 &
sleep 2
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/register -H "Content-Type: application/json" \
  -d '{"device_id":"lamp","device_name":"Lamp","device_type":"lamp","capabilities":["light"],"actions":["set_color"],"ip":"192.168.1.101"}'
curl -s -X POST http://localhost:8000/register -H "Content-Type: application/json" \
  -d '{"device_id":"rover","device_name":"Rover","device_type":"mobile_coaster","capabilities":["drive"],"actions":["stop"],"ip":"192.168.1.104"}'
curl -s -X POST http://localhost:8000/events -H "Content-Type: application/json" \
  -d '{"device_id":"global_mic","kind":"transcript","payload":{"text":"lamp blue"}}'
curl -s -X POST http://localhost:8000/events -H "Content-Type: application/json" \
  -d '{"device_id":"global_mic","kind":"transcript","payload":{"text":"stop"}}'
curl -s -X POST http://localhost:8000/events -H "Content-Type: application/json" \
  -d '{"device_id":"global_mic","kind":"transcript","payload":{"text":"I need to lock in"}}'
curl -s http://localhost:8000/state
curl -s http://localhost:8000/devices
kill %1
echo "PHASE 3A PASSED"
```

---

## Phase 3B: Master Reasoning Engine

**Creates:** `control_plane/master.py`, `control_plane/SOUL.md`

### Spec References

| What | Where in IMPLEMENTATION_SPEC.md |
|------|-------------------------------|
| 6 master tools (full definitions) | §19 "Tool Surface" — update_user_state, send_to_lamp/mirror/radio/rover, no_op |
| Tool parameter schemas | §19 "Tool Surface" — instruction param for send_to_*, mode/mood/energy for update_user_state |
| Multi-step response semantics | §19 "Multi-Step Responses" — example for "I need to lock in" |
| Execution order (state first, then dispatch) | §19 "Execution Semantics" — 7-step execution order, persist state before dispatch |
| no_op rules | §19 "Execution Semantics" — must be only tool call, ignore others |
| System prompt rules for master | §19 "System Prompt Instructions" — 8 rules (tool calls only, no prose, verbose intents, no hardware params, etc.) |
| Prompt assembly order | §13 "Assembly Order" — 7 sources in order (master SOUL, device SOULs, user.md, devices.json, state.json, events, triggering event) |
| Truncation limits | §13 "Truncation Limits" — per-source char limits, 40K total cap |
| Master statelessness | §13 "Master Statelessness" — fresh single-turn API call every time, no conversation history |
| Validation rules | §19 "Validation" — unknown tool → reject+log, missing instruction → reject+log, offline device → status offline |
| Master model config | §6 "Master Model" — claude-opus-4-6, beta header context-1m-2025-08-07 |
| Personality model (dual purpose SOULs) | §11 "Personality Model" — master reads all SOULs for strategy, devices read own SOUL for tactics |

### SOUL.md
Master home personality and tool-use rules. Under 4000 chars. Implements all 8 rules from §19 "System Prompt Instructions". Includes device palette summary so master knows what each device can do (informed by §4 "Device Roles In V1" and §11 "Personality Model").

### master.py
- `MASTER_TOOLS`: 6 Anthropic API tool definitions (§19 "Tool Surface")
- `assemble_prompt(state_manager, triggering_event) -> list[dict]`: builds messages per §13 "Assembly Order" with truncation per §13 "Truncation Limits"
- `call_master(messages, tools) -> anthropic.Message`: Opus 4.6 with `context-1m-2025-08-07` beta header (§6)
- `parse_tool_calls(response) -> list[dict]`: extract tool_use blocks, validate per §19 "Validation"
- `execute_master_turn(state_manager, event) -> list[dict]`: orchestrate full turn
- `apply_state_update(state_manager, tool_calls)`: merge update_user_state calls per §19 "Execution Semantics" step 3
- `extract_device_instructions(tool_calls) -> list[tuple[str, str]]`: return (device_id, instruction) pairs

**Important:** master.py is a pure function module. It does NOT import app.py or ConnectionManager. It takes a state manager and event, returns structured data. Dispatch is app.py's job.

### Verify (offline — prompt assembly + parsing only)
```bash
python3 -c "
from control_plane.master import assemble_prompt, MASTER_TOOLS
from control_plane.state import StateManager
from control_plane.schemas import DeviceEvent, DeviceRegistration
sm = StateManager()
sm.register_device(DeviceRegistration(device_id='lamp', device_name='Lamp', device_type='lamp', capabilities=['light'], actions=['set_color'], ip='192.168.1.101'))
event = DeviceEvent(device_id='global_mic', kind='transcript', payload={'text': 'test'})
messages = assemble_prompt(sm, event)
assert len(MASTER_TOOLS) == 6
print('PHASE 3B PASSED (offline)')
"
```

### Verify (live API — requires ANTHROPIC_API_KEY in .env)
```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
from control_plane.master import execute_master_turn
from control_plane.state import StateManager
from control_plane.schemas import DeviceEvent, DeviceRegistration
sm = StateManager()
for d in [('lamp','Lamp','lamp',['light','move_head'],['set_color','perk_up']),
          ('mirror','Mirror','companion',['speak','move_tilt'],['speak','nod']),
          ('rover','Rover','mobile_coaster',['drive'],['drive_to','stop'])]:
    sm.register_device(DeviceRegistration(device_id=d[0],device_name=d[1],device_type=d[2],capabilities=d[3],actions=d[4],ip='192.168.1.101'))
event = DeviceEvent(device_id='global_mic', kind='transcript', payload={'text': 'I need to lock in'})
calls = execute_master_turn(sm, event)
print('Master returned', len(calls), 'tool calls:')
for tc in calls: print(f'  {tc[\"tool\"]}({tc[\"input\"]})')
print('PHASE 3B PASSED (live)')
"
```

---

## Phase 4: Integration Wiring

**Modifies:** `control_plane/app.py`

### Spec References

| What | Where in IMPLEMENTATION_SPEC.md |
|------|-------------------------------|
| Execution semantics (full 7-step order) | §19 "Execution Semantics" — parse tool_use → check no_op → apply state → persist → dispatch spawns → parallel to different devices → sequential to same device |
| Event routing for vision_result and tick | §8 "Event Routing Table" — vision_result and tick rows both go to master queue |
| Voice lock set/clear lifecycle | §8 "Voice Lock" — set on speech dispatch, cleared on action_result or 10s timeout |
| Emergency stop bypass | §8 "Emergency Stop" — bypasses voice lock |
| Spawn dispatch format | §9 "Spawn" — JSON structure sent to devices |
| Offline device handling | §19 "Validation" — mark as failed with status offline |

### Changes
1. Import master functions (execute_master_turn, apply_state_update, extract_device_instructions)
2. In process_event, when route_transcript returns `("master",)`:
   - `tool_calls = execute_master_turn(state_manager, event)`
   - `apply_state_update(state_manager, tool_calls)` — persist state first (§19 step 4)
   - `instructions = extract_device_instructions(tool_calls)`
   - For each (device_id, instruction): `dispatch_spawn(device_id, instruction)` (§19 steps 5-7)
   - If no_op is only call: log reason, return (§19 step 2)
3. Wire `vision_result` events to same master reasoning path (§8 routing table)
4. Wire `tick` events to same master reasoning path (§8 routing table)
5. Set voice lock when dispatching spawn to Mirror or Radio (§8 "Voice Lock")
6. Clear voice lock on action_result from speaking devices, or after 10s timeout (§8 "Voice Lock")

### Verify (full end-to-end with real API)
```bash
uvicorn control_plane.app:app --port 8000 &
sleep 2

# Register all 4 devices
for dev in \
  '{"device_id":"lamp","device_name":"Lamp","device_type":"lamp","capabilities":["light","move_head","emote"],"actions":["set_color","perk_up","droop"],"ip":"192.168.1.101"}' \
  '{"device_id":"mirror","device_name":"Mirror","device_type":"companion","capabilities":["speak","move_tilt","see"],"actions":["speak","nod","tilt"],"ip":"192.168.1.102"}' \
  '{"device_id":"radio","device_name":"Radio","device_type":"speaker","capabilities":["speak","play_music"],"actions":["speak","play_music","set_volume"],"ip":"192.168.1.103"}' \
  '{"device_id":"rover","device_name":"Rover","device_type":"mobile_coaster","capabilities":["drive"],"actions":["drive_to","stop","return_home"],"ip":"192.168.1.104"}'; do
  curl -s -X POST http://localhost:8000/register -H "Content-Type: application/json" -d "$dev"
done

# MASTER REASONING E2E
curl -s -X POST http://localhost:8000/events -H "Content-Type: application/json" \
  -d '{"device_id":"global_mic","kind":"transcript","payload":{"text":"I need to lock in"}}' | python3 -m json.tool

# Verify state was updated
curl -s http://localhost:8000/state | python3 -m json.tool

# Deterministic still works
curl -s -X POST http://localhost:8000/events -H "Content-Type: application/json" \
  -d '{"device_id":"global_mic","kind":"transcript","payload":{"text":"lamp blue"}}'

# Emergency stop still works
curl -s -X POST http://localhost:8000/events -H "Content-Type: application/json" \
  -d '{"device_id":"global_mic","kind":"transcript","payload":{"text":"stop"}}'

# Tick triggers master
curl -s -X POST http://localhost:8000/events -H "Content-Type: application/json" \
  -d '{"device_id":"system","kind":"tick","payload":{"elapsed_since_last_interaction_s":300}}' | python3 -m json.tool

# Full event log
curl -s http://localhost:8000/events | python3 -m json.tool

kill %1
echo "PHASE 4 PASSED — FULL CONTROL PLANE OPERATIONAL"
```

---

## Agent Team Execution Notes

- **After every phase**, run the verification script. If it fails, fix before proceeding.
- **Phases 2A and 2B** have no dependency on each other — run them in parallel.
- **Phases 3A and 3B** have no dependency on each other — run them in parallel. master.py depends only on schemas + state, NOT on app.py.
- **Phase 4** depends on both 3A and 3B completing successfully.
- **ANTHROPIC_API_KEY** must be set in `.env` before Phase 3B live verification and Phase 4.
- All state files in `data/` may be modified by verification scripts. Reset seeds between phases if needed.
- Every agent should read `IMPLEMENTATION_SPEC.md` — the spec reference tables in each phase tell you exactly which sections to read.

## Quick Spec Section Index

For agents that want to skim the full spec efficiently:

| Spec Section | Content | Used By Phases |
|-------------|---------|----------------|
| §1 Project Goal | Device descriptions, sensing sources | Context for all |
| §2 V1 Architecture | Two-tier model, control plane vs device | Context for all |
| §4 Device Roles | Per-device capabilities (what lamp/mirror/radio/rover can do) | 3B (SOUL.md) |
| §6 Voice Pipeline + Master Model | Master model config, deterministic routing examples | 2B, 3B |
| §7 Vision Pipeline | Camera sources, frame capture, Claude Vision analysis, decision gate | Vision hooks (all phases) |
| §8 Control Plane Responsibilities | Event routing table, emergency stop, voice lock, execution model | 2B, 3A, 4 |
| §9 Canonical Message Protocol | All schemas, payload formats, endpoints, transport, registration | 1, 3A |
| §10 State Model | File-backed state, 4 data files | 1, 2A |
| §11 Personality Model | SOUL.md dual purpose (master reads for strategy, device reads for tactics) | 3B |
| §12 Dependencies | Package list for control plane and device runtime | 1 |
| §13 Prompt Assembly | Assembly order, truncation limits, master statelessness, event log maintenance | 2A, 3B |
| §14 Project Structure | File layout | Context for all |
| §17 Demo Story | Demo mode, virtual device fallback (is_virtual flag) | 3A |
| §19 Tool-Calling Contract | 6 tools, execution semantics, system prompt rules, validation | 3B, 4 |

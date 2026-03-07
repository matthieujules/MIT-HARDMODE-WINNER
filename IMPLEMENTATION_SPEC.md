# ClaudeHome: V1 Implementation Spec

This document is the canonical build plan for V1.

Use this file together with the Mermaid sources in `docs/architecture/`.
`RESEARCH_SYNTHESIS.md` is background research, not a normative spec.

---

## 1. Project Goal

ClaudeHome is a smart-home demo with four embodied devices:

- Luxo: expressive lamp
- Mirror: primary conversational companion
- Chef: mobile food tray robot
- Purse: mobile companion

The goal is not "LLM on every box." The goal is a home that feels coherent, proactive, and alive:

- fast on simple requests
- context-aware on complex requests
- personality-rich across devices
- operationally simple enough to ship for a hackathon

The user should feel like there is one home intelligence coordinating a small cast of physical devices.

---

## 2. V1 Architecture

V1 uses a **two-tier model architecture**: a **master model on the laptop** for strategic cross-device orchestration, and **fast device agent models on each Pi** for tactical per-device execution.

### Control Plane (Laptop)

The laptop runs one FastAPI-based service responsible for:

- HTTP + WebSocket connectivity to devices
- device registry
- runtime state store
- event log
- routing deterministic commands
- calling the master model for open-ended reasoning
- centralized Claude Vision analysis

The control plane is the only place that performs cross-device reasoning. The master model decides WHAT should happen and sends natural language instructions to devices. It never specifies hardware-level details.

### Devices (Raspberry Pis)

Each Pi runs an intelligent device runtime with three layers:

- **Hardware abstraction layer** — servo, LED, motor, camera, mic, speaker drivers
- **Reflex/safety layer** — instant hardware-level interrupt handlers (emergency stop, stall detection, collision avoidance)
- **Agent loop** — powered by a fast inference model (Qwen via Cerebras/Groq/Together), interprets the master's natural language instructions and sequences hardware actions autonomously

Each device has its own `SOUL.md` personality that it actively interprets. The device agent decides HOW to accomplish the master's intent, including creative expression, hardware sequencing, timing, and spoken line generation.

V1 devices are intelligent execution agents with personality. They never coordinate with each other — cross-device reasoning stays centralized on the laptop. But each device owns its own execution.

---

## 3. Why This Shape

This architecture is intentionally simpler than the earlier planning drafts.

### What We Keep

- one coherent home "brain"
- per-device identity via `SOUL.md`
- serial master execution
- explicit user state and event log
- a fast path for simple commands

### What We Drop From V1

- device-side Claude Vision calls
- a separate conceptual `bus/` and `master/` split
- all four devices doing equal always-on sensing

The key distinction in the two-tier model: the master is the **strategist**, device agents are **tacticians**. We run models on devices via fast cloud APIs (Cerebras/Groq), not local inference. Cross-device reasoning stays centralized. Per-device execution is autonomous.

This reduces:

- coordination bugs
- protocol complexity
- demo fragility

---

## 4. Device Roles In V1

### Mirror

Mirror is the primary voice interface.

- main microphone
- main speaker
- optional camera
- warm, encouraging personality

### Luxo

Luxo is the primary ambient actuator and secondary perception node.

- RGB + servo expression
- optional camera for presence / mood cues
- no speech

### Chef

Chef is an actuator-first device in V1.

- mobile base
- tray presentation
- optional speech
- does not need always-on sensing in V1

### Purse

Purse is also actuator-first in V1.

- mobile base
- speech / commentary
- no always-on perception required for V1

---

## 5. Sensing Strategy

V1 uses **selective sensing**, not "everything everywhere."

### Voice

Voice enters through Mirror first.

Optional stretch goal:

- Luxo can also transcribe locally and forward transcripts

### Vision

Vision is gated locally but interpreted centrally.

On-device:

- motion detection
- cooldown logic
- frame capture

On laptop:

- Claude Vision analysis
- user-state updates
- optional proactive action

This keeps the Pis cheap and simple while making perception consistent.

---

## 6. Voice Pipeline

The hot path should be deterministic whenever possible.

### Device-Side

Mirror runs a continuous voice capture loop:

1. **Audio capture**: 16kHz, mono, int16 via PyAudio. Processed in 512-sample chunks (~32ms each).
2. **VAD**: Silero VAD evaluates each chunk. Returns speech start/end timestamps.
3. **Buffering**: Between speech start and speech end, raw PCM chunks are accumulated (~16KB/sec).
4. **Transcription**: On speech end, the buffer is sent to faster-whisper (base model, int8 quantization). On Pi 5 this takes ~1-2s for a short utterance.
5. **Packaging**: Non-empty transcripts are wrapped in a `DeviceEvent(kind=transcript)` and sent over WebSocket.

### Control Plane Routing

The control plane receives a transcript and routes it as:

1. Deterministic single-device command
2. Complex intent requiring master reasoning

Deterministic examples:

- "Luxo blue"
- "Mirror tilt up"
- "Chef stop"

These should bypass the master model and become immediate device commands (sent as `command` type, not `spawn`).

Complex examples:

- "I need to lock in"
- "I'm stressed"
- "Can you make the room feel calmer?"

These go to the master model, which responds with natural language instructions dispatched as `spawn` messages to devices.

### Master Model

V1 uses a single master model on the laptop:

- default: Claude Sonnet 4.6

Opus is not part of the core V1 design. It can be tested later if quality demands it.

---

## 7. Vision Pipeline

V1 vision flow:

### Device-Side (Camera Loop)

1. `cv2.VideoCapture` opens the camera (USB: `/dev/video0`, or CSI via `picamera2` if preferred). Frames captured continuously (~30fps internal, not streamed).
2. OpenCV frame differencing: grayscale -> blur -> `cv2.absdiff` -> threshold -> contour detection.
3. If significant motion detected and cooldown has elapsed (min 5s since last capture):
   - Capture one JPEG at 640x480, quality 80 (~30-60KB).
   - Base64 encode and send as `DeviceEvent(kind=frame)` over WebSocket.
4. Most frames are discarded silently. Typical rate: 2-4 frames per minute during activity.

### Control-Plane (vision.py)

5. `vision.py` receives the frame and calls Claude Vision (Sonnet) with a structured prompt:

```
Analyze this camera frame from a smart home device.
Return JSON only:
{"people_count": N, "mood": "happy|sad|stressed|tired|neutral|focused|relaxed",
 "mood_confidence": 0.0-1.0, "activity": "brief description",
 "notable": "anything unusual or null"}
```

6. **Decision gate**: compare returned analysis against current `state.json`.
   - If mood changed AND `mood_confidence` >= 0.7 -> enqueue a `vision_result` event for the master.
   - If `people_count` changed (e.g., 0->1 someone arrived, 1->0 someone left) -> enqueue `vision_result`.
   - If neither condition met -> log the analysis, no master trigger.
   - Always update `state.json` with `people_count` and `activity` regardless.

7. Master reasoning turn processes the `vision_result` and may issue `spawn` instructions to devices (e.g., soften lighting, suggest break).

---

## 8. Control Plane Responsibilities

The control plane owns all shared intelligence.

### Responsibilities

- register devices
- maintain WebSocket sessions
- validate incoming events
- persist `devices.json`
- persist `state.json`
- append to `event_log.jsonl`
- run deterministic command routing
- invoke master model for complex turns
- dispatch `spawn` messages with natural language instructions to device agents
- dispatch `command` messages for direct hardware actions (emergency stop, simple deterministic commands)

### Execution Model

The master loop is serial:

- one event at a time
- command queue before observation queue
- no concurrent reasoning turns against shared state

This is a feature, not a limitation.

### Event Routing Table

When the control plane receives a `DeviceEvent`, route by `kind`:

| Kind | Handler | Notes |
|---|---|---|
| `transcript` | `router.py` -> deterministic match or master queue | Regex match -> immediate `command` (direct). No match -> master reasoning turn -> `spawn` messages. See emergency stop and voice lock below. |
| `frame` | `vision.py` -> conditionally master queue | Vision analyzes frame. If significant state change (mood shift, new person), vision enqueues a synthetic `vision_result` event for the master. |
| `vision_result` | master queue | Internal/synthetic. Never sent by a device. Generated by `vision.py`. Uses `device_id: "system"` with `source_device` in payload. |
| `action_result` | `state.py` (log + side effects) | Append to event log. Update device status in `devices.json`. Clear voice lock `is_speaking` flag if this result corresponds to a `speak` action. No master trigger. |
| `heartbeat` | `state.py` (device liveness) | Update device `last_seen` timestamp in `devices.json`. No further processing. |
| `manual_override` | direct dispatch | Operator-injected command via `POST /events`. Uses `device_id: "operator"`. Validate and dispatch immediately, bypassing router and master. |
| `tick` | master queue | Internal/synthetic. Generated by a periodic timer (every 2-5 min). Uses `device_id: "system"`. Allows the master to evaluate state and initiate proactive behavior (e.g., Chef offering water after long focus). |

`motion` is not a device event kind. Motion detection is device-internal preprocessing that triggers frame capture. Only `frame` events reach the control plane.

### Emergency Stop

The deterministic router must include a high-priority fuzzy match for stop/halt/freeze commands. If any transcript matches a stop pattern (e.g., `stop|halt|freeze|wait|no no no`), the router immediately broadcasts a `command` type `stop` to ALL mobile devices (Chef, Purse) without waiting for master reasoning. This is a safety requirement for mobile robots.

**Emergency stop bypasses voice lock.** Even if Mirror is currently speaking (and voice lock is active), stop-pattern matching must still be evaluated. The processing order for transcript events is: emergency stop check -> voice lock filter -> deterministic router -> master queue.

### Voice Lock

The control plane maintains an `is_speaking` flag per device. While a speaking device (Mirror, Chef, Purse) is executing a `speak` action, the control plane drops non-emergency `transcript` events from that device. This prevents TTS audio from being picked up by the device's own microphone and triggering a feedback loop.

The flag is set when a `spawn` or `command` involving speech is dispatched and cleared when the corresponding `action_result` arrives (or after a timeout of 10s). Note: `action_result` events are routed to the log AND checked against the voice lock — "log only, no further processing" in the routing table means no master trigger, not that no side effects occur.

### State Broadcasts

V1 does not broadcast state to devices. The master factors in all state when generating instructions for device agents. If a device needs to reflect system state (e.g., Luxo showing focus-mode lighting), the master sends a `spawn` instruction describing the intent. The device agent interprets it. This eliminates bidirectional state synchronization complexity.

---

## 9. Canonical Message Protocol

This protocol replaces the conflicting formats in older planning docs.

### DeviceEvent

All device events use the same envelope. The `payload` shape varies by `kind`.

```json
{
  "device_id": "mirror",
  "kind": "transcript",
  "ts": "2026-03-06T12:00:00Z",
  "payload": { ... }
}
```

### Per-Kind Payload Schemas

**`transcript`** — voice transcription from Mirror (or any future voice device).
```json
{ "text": "I need to lock in" }
```

**`frame`** — camera frame captured after local motion detection + cooldown. Base64-encoded JPEG in JSON.
```json
{
  "image_b64": "<base64 JPEG>",
  "resolution": [640, 480],
  "trigger": "motion"
}
```

**`vision_result`** — synthetic, internal only. Generated by `vision.py` when analysis detects a significant state change. Never sent by a device.
```json
{
  "analysis": {
    "people_count": 1,
    "mood": "tired",
    "mood_confidence": 0.82,
    "activity": "rubbing eyes at desk",
    "notable": null
  },
  "previous_mood": "determined",
  "source_device": "luxo"
}
```

**`action_result`** — device confirms or reports failure of an instruction or command.
```json
{
  "request_id": "req_123",
  "status": "ok",
  "detail": "instruction completed: switched to focus lighting with head perk"
}
```
`status` is `"ok"`, `"error"`, `"timeout"` (agent loop exceeded time budget), or `"offline"` (set by the control plane when dispatch fails because the device is disconnected). `detail` is a human-readable string.

**`heartbeat`** — periodic device liveness signal. Empty payload; the control plane uses `ts` and `device_id` to update `last_seen`.
```json
{}
```

**`manual_override`** — operator-injected event via `POST /events`. Can be either a direct command or a spawn instruction.
```json
{
  "target": "luxo",
  "type": "spawn",
  "instruction": "Switch to relax mode. Ease into it — slow fade, gentle droop."
}
```

**`tick`** — internal/synthetic. Generated by a periodic timer (every 2-5 min). Enables proactive behavior.
```json
{
  "elapsed_since_last_interaction_s": 420
}
```

### Messages to Devices (Downward)

Two message types go DOWN to devices over WebSocket:

**Direct command** (bypasses agent loop, immediate hardware execution):
```json
{
  "type": "command",
  "action": "stop",
  "params": {},
  "request_id": "req_123"
}
```
Used for emergency stop broadcasts, simple deterministic router commands (`stop`, simple `set_color`, etc.), and Layer 1 direct hardware calls. No model involved.

**Spawn** (starts agent loop on device):
```json
{
  "type": "spawn",
  "instruction": "Drive over to the user and offer comfort food. They're stressed — be gentle about it.",
  "request_id": "req_456",
  "max_iterations": 10,
  "time_budget_ms": 15000
}
```
The device runtime checks the `type` field and routes accordingly. Emergency stop always uses `command` type.

### Transport

All device <-> control-plane communication uses the device's WebSocket connection (`WS /ws/{device_id}`):

- **Events go up**: device sends `DeviceEvent` JSON text frames to the control plane.
- **Commands/spawns go down**: control plane sends `command` or `spawn` JSON text frames to devices.

Frame payloads (base64 images) are sent as JSON text frames over the same WebSocket. A 640x480 JPEG at quality 80 is ~40-80KB with base64 overhead — fine on LAN.

### Endpoints

The control plane exposes:

- `POST /register` — device registration (called once at boot, before WS connect). See registration schema below.
- `WS /ws/{device_id}` — authoritative bidirectional channel (events up, commands/spawns down)
- `POST /events` — operator/testing API: inject events into the same processing pipeline
- `POST /commands/{device_id}` — operator/testing API: inject commands or spawns into the same dispatch path
- `GET /health` — liveness check

`POST /events` and `POST /commands/{device_id}` exist for operator use, testing, and manual injection. They are not used by device runtimes.

There is no separate "bus protocol" beyond this API surface in V1. V1 does not use `POST /broadcast` — there are no state broadcasts to devices (see Section 8).

### Registration Schema

Devices call `POST /register` once at boot, before connecting their WebSocket.

Request:
```json
{
  "device_id": "luxo",
  "device_name": "Luxo",
  "device_type": "lamp",
  "capabilities": ["see", "light", "move_head", "emote"],
  "actions": ["set_color", "set_brightness", "set_scene", "look_at", "nod", "shake", "emote", "perk_up", "droop", "reset_position"],
  "ip": "192.168.1.101"
}
```

Response: `200 OK` with `{"status": "registered"}` or `{"status": "updated"}` if re-registering.

The `actions` field declares the hardware actions the device agent can call internally. The master does not use this field for validation — it sends natural language instructions, not hardware actions. The `actions` field is primarily for the device agent's own use and for operator visibility into device capabilities.

The control plane writes this to `data/devices.json` and marks the device as `online`. The device then connects its WebSocket at `WS /ws/{device_id}`.

---

## 10. State Model

V1 state is file-backed and simple.

### `data/user.md`

Stable user preferences and demo-specific facts.

### `data/devices.json`

Known devices, capabilities, addresses, and current availability.

### `data/state.json`

Current inferred runtime state, for example:

- current mode
- mood
- energy
- manual overrides
- active device statuses

### `data/event_log.jsonl`

Append-only recent events.

The master prompt is assembled per turn from:

- master `SOUL.md`
- all device `SOUL.md` files
- `user.md`
- `devices.json`
- `state.json`
- a compact recent event window
- current triggering event

See Section 13 for full assembly order and truncation limits.

When the raw event log gets too long:

- summarize older events into `state.json`
- keep only a bounded recent tail in `event_log.jsonl`

---

## 11. Personality Model

Per-device `SOUL.md` files serve a dual purpose in the two-tier architecture:

### Master-Side (Strategic)

The master reads all device `SOUL.md` files to:

- decide which device should respond to a given event
- frame the natural language instruction with appropriate emotional context
- understand each device's strengths and personality so it can delegate effectively

### Device-Side (Tactical)

Each device agent reads its own `SOUL.md` to:

- interpret the master's instruction with personality and creative flair
- decide the specific words to say (for speaking devices)
- choose the style and timing of physical actions
- add creative flourishes that align with its character

This means personality is genuinely owned by the device, not ventriloquized by the master. The master says "encourage the user, they're locking in" and Mirror decides to say "Let's go. You've got this." in its own warm, encouraging voice. The master says "switch to focus lighting, show some determination" and Luxo decides the exact color ramp, servo sequence, and perk-up timing.

---

## 12. Dependencies

### Control Plane (Laptop)

| Package | Purpose |
|---|---|
| `fastapi` + `uvicorn` | HTTP + WebSocket server |
| `anthropic` | Claude Sonnet (master reasoning) + Claude Vision |
| `pydantic` | Message schemas, validation |

No ORM, no database, no task queue. Stdlib `json` + file I/O for all persistence.

### Device Runtime (Raspberry Pi)

| Package | Purpose | Notes |
|---|---|---|
| `adafruit-circuitpython-servokit` | PCA9685 servo control | Luxo, Mirror |
| `pytweening` | Servo easing / smooth animation | Pure Python, zero deps |
| `gpiozero` | LEDs, GPIO, motor PWM | Pre-installed on Pi OS |
| `opencv-python` | Camera capture + motion detection | USB cameras via `cv2.VideoCapture` |
| `faster-whisper` | Whisper STT | Mirror only (or laptop if Pi too slow) |
| `silero-vad` (via `torch`) | Voice activity detection | Mirror only |
| `websockets` | WebSocket client to control plane | All devices |
| `elevenlabs` | TTS | Speaking devices (Mirror, Chef, Purse) |
| `openai` | OpenAI-compatible client for fast inference APIs | All devices (agent loop model calls) |

The `openai` package is used as a client for Cerebras, Groq, and Together AI — they all expose OpenAI-compatible APIs. The model provider is pluggable. Currently targeting Qwen 3 models on Cerebras/Groq/Together AI for fast device agent inference.

Optional fallbacks:
- `piper-tts` — local TTS if ElevenLabs latency or API limits are a problem
- `vosk` — lower-accuracy STT if Whisper is too slow on the Pi hardware

### Not Using

- OpenClaw (TypeScript messaging gateway — wrong language, wrong domain, 430K LoC)
- Claude Agent SDK (subprocess-based, too heavy)
- Home Assistant integrations (all tightly coupled to HA entity model)
- TinyDB or any DB (stdlib json is simpler for our 3-file state model)

---

## 13. Prompt Assembly and Context Limits

The master prompt is assembled per turn. Apply truncation limits to prevent prompt bloat.

### Assembly Order

1. Master `control_plane/SOUL.md` — home personality, routing instructions, tool-use rules
2. All device `SOUL.md` files — all four, every turn (they are short; the master needs the full device palette to decide who responds)
3. `data/user.md` — stable user preferences
4. `data/devices.json` — device registry (capabilities, availability, online status)
5. `data/state.json` — current runtime state
6. Recent event window from `data/event_log.jsonl` — last N events (bounded)
7. Current triggering event

### Truncation Limits

| Source | Max chars | Notes |
|---|---|---|
| Each `SOUL.md` file | 4,000 | Personality files should be concise by design |
| `user.md` | 8,000 | Pre-authored, stable |
| `devices.json` | 4,000 | Should stay small with 4 devices |
| `state.json` | 4,000 | Runtime state, keep lean |
| Recent event window | 8,000 | Tail of event_log.jsonl |
| Total assembled prompt | 40,000 | Hard cap across all sections |

### Master Statelessness

The master model has no persistent conversation history. Every reasoning turn is a fresh single-turn API call with the full context assembled from disk. This means:

- No multi-turn conversation accumulation.
- Every turn re-reads all state files (`state.json`, `devices.json`, `user.md`, SOULs).
- Context window usage is bounded and predictable (~10-12K chars typical, 40K max).
- If the process crashes and restarts, no context is lost — `state.json` and `event_log.jsonl` are the durable memory.

### Event Log Maintenance

`event_log.jsonl` is append-only but bounded. The recent event window included in the master prompt is capped at 8,000 chars (roughly the last 10-20 events depending on event size).

When the log file exceeds a size threshold (e.g., 500 lines):

1. Flush current inferred state to `state.json` (pre-compaction memory flush).
2. Summarize older events into `state.json` fields if meaningful.
3. Truncate the log to the most recent N lines.

This ensures the master always has recent context without unbounded file growth.

---

## 14. Planned Project Structure

```text
claude-home/
  sync.sh               # rsync device code to Pis

  devices/
    luxo/
      SOUL.md
      config.yaml
    mirror/
      SOUL.md
      config.yaml
    chef/
      SOUL.md
      config.yaml
    purse/
      SOUL.md
      config.yaml

  device_runtime/
    main.py              # device entry point, loads config, connects to control plane
    hardware.py           # HAL: servos (ServoKit), LEDs (gpiozero), motors, camera
    voice.py              # VAD + Whisper + transcript packaging (Mirror only)
    ws_client.py          # WebSocket client: connect, send events, receive commands/spawns
    agent.py              # device agent loop: receives spawn instruction, calls fast model API, executes hardware tools, loops until done
    model_client.py       # pluggable model client interface (Cerebras, Groq, Together — all OpenAI-compatible)

  control_plane/
    app.py               # FastAPI app + WebSocket ConnectionManager
    router.py            # deterministic hot-path routing (regex patterns)
    master.py            # master reasoning loop + prompt assembly
    vision.py            # centralized Claude Vision calls
    state.py             # state + event persistence (json + jsonl)
    schemas.py           # Pydantic message schemas (DeviceEvent, command/spawn messages)
    SOUL.md              # master home personality

  data/
    user.md
    devices.json
    state.json
    event_log.jsonl

  docs/
    architecture/
```

`device_runtime/` is synced to each Pi. `control_plane/` runs only on the laptop.

---

## 15. Latency Targets

| Path | Flow | Target | Breakdown |
|---|---|---|---|
| Deterministic command | Mic -> Whisper -> router -> direct command | 2-3s | VAD ~0.1s + Whisper ~1-2s (Pi 5) + network ~0.05s + router ~0.01s |
| Master reasoning | Mic -> Whisper -> master -> spawn instructions to devices | 3-5s | Whisper ~1-2s + master reasoning ~1.5-3s |
| Spawn execution | Master intent -> device agent loop -> hardware | +0.5-2s on top of master | Agent loop: 2-5 iterations x ~200-300ms per model call |
| Full master + spawn | Mic -> Whisper -> master -> device agent -> hardware complete | 4-7s | Master path (3-5s) + agent loop (0.5-2s) |
| Vision reaction | Motion -> frame -> laptop vision -> state/spawn | 3-8s | Cooldown gate + network ~0.1s + Claude Vision ~1-2s + master ~1.5-3s + agent ~0.5-2s |
| Emergency stop | Mic -> Whisper -> broadcast stop (command type) | 2-3s | Same as deterministic, but bypasses voice lock |

If faster deterministic commands are needed, Whisper can run on the laptop instead (stream raw audio from Pi, transcribe on laptop — saves ~1s but adds streaming complexity). This is a stretch goal, not a V1 requirement.

The hot path is allowed to be slower than theoretical minimums if it is dramatically more reliable.

---

## 16. Non-Goals For V1

Do not build these before the demo works:

- local on-device model inference (we use fast cloud APIs, not edge inference)
- vector memory retrieval
- fully autonomous multi-camera perception mesh
- generic task decomposition frameworks
- safety-critical home automation
- device-to-device coordination (all cross-device logic goes through the master)

---

## 17. Demo Story

A strong demo should show:

1. Mirror hears "I need to lock in."
2. Master updates state to focus mode and sends natural language instructions to devices.
3. Luxo's agent interprets "switch to focus lighting, show determination" — picks its own color ramp and servo choreography.
4. Mirror's agent interprets "encourage the user briefly" — generates and speaks its own line.
5. Chef's agent interprets "bring coffee, they're locking in" — drives to user with appropriate pacing.

Second beat:

1. A vision-capable device, Luxo or Mirror, notices fatigue cues.
2. Master updates state from focused to tired and sends new instructions.
3. Mirror's agent decides how to suggest a break in its own voice.
4. Luxo's agent decides how to soften lighting with its own timing.

That is enough to demonstrate the product. The two-tier model makes it visually richer — each device responds with its own personality, not with scripted master commands.

### Demo Mode

For live presentations, the periodic tick timer (2-5 min) is too slow to trigger during a 3-minute demo. The control plane should support a **demo mode** where:

- A hotkey or presentation clicker injects `tick` events on demand via `POST /events`.
- This allows the presenter to trigger proactive behavior (e.g., Chef offering water, vision-driven mood response) at exactly the right moment.
- Demo mode does not change the architecture — it just uses the existing operator API for manual event injection.

### Virtual Device Fallback

`data/devices.json` supports an `is_virtual` flag per device. If a physical device fails (servo burns out, motor jams, Pi crashes), setting `is_virtual: true` switches that device to a console mock that logs commands and spawn instructions to stdout instead of dispatching over WebSocket. This lets the demo continue with degraded but functional behavior.

---

## 18. Build Order

Implement in this order:

1. Canonical schemas (`schemas.py`) and control-plane app (`app.py` with WebSocket ConnectionManager)
2. Virtual device harness — a terminal script simulating 4 WS connections that sends fake events and prints received commands/spawns. Enables control-plane development without Pi hardware.
3. State store and event log (`state.py`)
4. Deterministic router (`router.py`) with emergency stop (uses `command` type for direct dispatch)
5. Master reasoning loop (`master.py`) with tool-calling contract and prompt assembly (uses `send_to_*` tools that emit `spawn` messages)
6. Device agent loop (`agent.py`) and pluggable model client (`model_client.py`) — receives spawn instructions, calls fast inference API, executes hardware tools, loops until done
7. Mirror voice pipeline (`device_runtime/voice.py`) with voice lock
8. Luxo action path (`device_runtime/hardware.py` servo + LED control) — wired into agent loop for spawn instructions, direct execution for commands
9. Chef and Purse command execution (motors, tray) — wired into agent loop
10. Centralized vision (`vision.py`) with vision_result trigger
11. Proactive behaviors (periodic tick timer)

If something conflicts with this document, update this document and the Mermaid files before building further.

---

## 19. Master Model Tool-Calling Contract

The master model is called via the Anthropic Messages API with tool use. It returns an ordered execution plan as `tool_use` blocks, not freeform prose.

### Tool Surface

V1 exposes exactly six tools:

| Tool | Purpose |
|---|---|
| `update_user_state` | Patch `state.json` when the event changes inferred mode, mood, or energy |
| `send_to_mirror` | Send a natural language instruction to Mirror |
| `send_to_luxo` | Send a natural language instruction to Luxo |
| `send_to_chef` | Send a natural language instruction to Chef |
| `send_to_purse` | Send a natural language instruction to Purse |
| `no_op` | Explicitly record that this event requires no action |

One tool per device, not one per action (too many tools, less reliable), and not one generic `send_to_device` (too permissive, loses per-device personality context in the tool name).

Each `send_to_*` tool accepts a single `instruction` parameter (string) — free-form natural language describing what the device should do. The master includes emotional context, constraints, user state cues, and personality hints. The master does NOT need to know hardware action enums or parameter schemas. The device agent interprets the instruction using its own SOUL.md and hardware capabilities.

### Multi-Step Responses

Claude returns multiple `tool_use` blocks in one response. Example for "I need to lock in":

1. `update_user_state({mode: "focus", mood: "determined"})`
2. `send_to_luxo({instruction: "Switch to focus mode lighting. Show some determination — perk up, you're ready to work."})`
3. `send_to_mirror({instruction: "Give the user a short, warm encouragement. They're locking in. Keep it brief — they want to focus, not chat."})`
4. `send_to_chef({instruction: "Head over to the user with coffee. They're entering focus mode — be efficient, not chatty."})`

The master is verbose about WHAT and WHY. The device agent decides HOW.

### Execution Semantics

Inside one resolved turn:

1. Parse all `tool_use` blocks in response order.
2. If `no_op` is present, it must be the only tool call. Ignore any others.
3. Apply all `update_user_state` calls first, merging into one state patch.
4. Persist `state.json` before dispatching any device instructions.
5. For each `send_to_*` call, construct a `spawn` message with the instruction and dispatch to the target device over WebSocket.
6. Dispatch spawns to different devices in parallel.
7. Preserve model order for multiple instructions targeting the same device (queue them sequentially).

### System Prompt Instructions

The master `SOUL.md` must include:

- Return only tool calls. Do not return assistant prose.
- Use `send_to_*` tools to instruct devices. Write instructions as natural language.
- Be verbose about emotional context, user state, and constraints in instructions. The device agent will interpret the rest.
- Include personality cues when relevant — remind the device of the mood you want.
- Do not specify hardware actions, color values, servo angles, or motor parameters. The device owns its hardware.
- Use `update_user_state` when the event changes inferred mode, mood, or energy.
- Use `no_op` only when nothing should change. It must be the only tool call.
- Prefer devices marked available in the device registry. Do not target offline devices.

### Device Agent Contract

When a device receives a `spawn` message, the agent loop:

1. Loads its own `SOUL.md` for personality context.
2. Builds a prompt with: the instruction, its SOUL.md, its available hardware tool functions, and current sensor state.
3. Calls the fast inference model (Qwen via Cerebras/Groq/Together).
4. Parses the model's tool call response and executes the hardware action.
5. Reads updated sensor state and evaluates whether the instruction is complete.
6. Loops (back to step 3) until done, or until hitting the iteration cap or time budget.
7. Sends an `action_result` event back to the control plane with status and detail.

### Device Runtime Layers (Subsumption Architecture)

**Layer 0 — Hardware Reflexes (pure Python, no model, instant ~1ms)**

These are interrupt handlers that PREEMPT everything:

- Emergency stop received -> kill motors immediately
- Sensor threshold breach (e.g., distance < 10cm) -> stop motors
- Encoder stall detected -> stop motors
- VAD speech-start -> acknowledgment gesture (Mirror tilt)

**Layer 1 — Direct Commands (no model, immediate)**

- `command` type messages: `stop`, simple `set_color`, etc.
- Direct hardware calls, no agent loop involved.
- Used by emergency stop broadcasts and simple deterministic router commands.

**Layer 2 — Agent Loop (fast model, ~200-300ms per iteration)**

- `spawn` type messages trigger the agent loop described above.
- The agent has access to the device's hardware capabilities as callable tool functions.
- Max iteration cap (e.g., 10) and time budget (e.g., 15s) for safety.
- If the agent times out, it reports `status: "timeout"` in the action_result.

### Validation

`master.py` validates tool calls minimally:

- Unknown tool name -> reject, log, continue other calls.
- Missing `instruction` parameter on `send_to_*` -> reject, log, continue.
- Offline device -> mark as failed with status `offline` in the action_result, continue others.

The master does not validate hardware actions — it does not send them. Hardware action validation happens inside the device agent loop, where the fast model's tool calls are checked against the device's actual hardware capabilities.

No automatic retry in V1. If a repair loop is added later, cap at one retry turn.

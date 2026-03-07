# ClaudeHome

Ambient smart-home demo with four embodied devices coordinated by one laptop control plane.

## Architecture

The canonical V1 architecture lives in:

- `IMPLEMENTATION_SPEC.md`
- `docs/architecture/*.mmd`

`RESEARCH_SYNTHESIS.md` is background research only. It is not the build spec.

Current diagrams:

- `system-overview.mmd` — V1 system shape
- `message-flow.mmd` — deterministic, reasoning, and vision flows
- `voice-pipeline.mmd` — transcript routing and hot path
- `memory-architecture.mmd` — state, event log, and prompt assembly

Only the `.mmd` sources are tracked. Rendered exports should be treated as disposable artifacts.

## What's Built

### Control Plane (fully working, tested with live API)

| File | Purpose |
|------|---------|
| `control_plane/schemas.py` | Pydantic v2 models for all protocol messages |
| `control_plane/state.py` | File-backed state persistence (state.json, devices.json, event_log.jsonl, master_log.jsonl) |
| `control_plane/router.py` | Deterministic command router, emergency stop, voice lock |
| `control_plane/app.py` | FastAPI app: endpoints, WebSocket ConnectionManager, event pipeline |
| `control_plane/master.py` | Master reasoning engine (multi-provider: Anthropic + Cerebras) |
| `control_plane/SOUL.md` | Master home personality and tool-use rules |
| `requirements.txt` | Python dependencies |

### Voice Pipeline (sidecar)

| File | Purpose |
|------|---------|
| `voice/voice_service.py` | Silero VAD + Groq Whisper STT sidecar, posts transcripts to control plane |

Run: `python3 -m voice.voice_service` (or `--test` for no-mic test mode, `--backend local` for local whisper)

### Vision Pipeline (sidecar + control plane)

| File | Purpose |
|------|---------|
| `vision/vision_service.py` | Camera sidecar: ArUco spatial tracking (every frame) + motion-gated frame capture |
| `control_plane/vision.py` | Claude Vision analysis + trigger logic (triggers master only on people_count change) |

Run: `python3 -m vision.vision_service` (or `--test`, `--calibrate`)

### 2D Spatial Map (dashboard)

| File | Purpose |
|------|---------|
| `dashboard/map.js` | SVG room map with device icons, status indicators, rover animation, drag-to-calibrate |
| `control_plane/spatial.py` | Spatial state management, deep_merge, waypoint resolution |
| `data/room.json` | Static room config (500×400cm, furniture, anchors, waypoints) |

### Networking

All device configs (`devices/*/config.yaml`) use Tailscale hostnames:
- Laptop: `claude-master`
- Pis: `lamp-pi`, `mirror-pi`, `radio-pi`, `rover-pi`
- Setup script: `scripts/setup-pi-tailscale.sh`

## How to Test

```bash
# 1. Install deps
pip3 install -r requirements.txt

# 2. Ensure .env has ANTHROPIC_API_KEY set

# 3. Start the server
python3 -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000

# 4. Register devices
curl -X POST localhost:8000/register -H 'Content-Type: application/json' \
  -d '{"device_id":"lamp","device_name":"Lamp","device_type":"lamp","capabilities":["light","move_head","emote"],"actions":["set_color","set_brightness"],"ip":"lamp-pi"}'
# Repeat for mirror, radio, rover (see test scripts in BUILD_PLAN.md)

# 5. Test deterministic routing
curl -X POST localhost:8000/events -H 'Content-Type: application/json' \
  -d '{"device_id":"global_mic","kind":"transcript","event_kind":"transcript","payload":{"text":"lamp blue"}}'

# 6. Test master reasoning (requires ANTHROPIC_API_KEY)
curl -X POST localhost:8000/events -H 'Content-Type: application/json' \
  -d '{"device_id":"global_mic","kind":"transcript","event_kind":"transcript","payload":{"text":"I need to lock in"}}'

# 7. Check results
curl localhost:8000/state        # Current home state
curl localhost:8000/devices      # Registered devices
curl localhost:8000/events       # Recent event log
curl localhost:8000/master-log   # Full master reasoning history
```

### Master Model Config (in `.env`)

```bash
MASTER_MODEL=claude-sonnet-4-6   # Default. Options: claude-opus-4-6, gpt-oss-120b
MASTER_PROVIDER=auto             # auto-detects from model name. Options: anthropic, cerebras
```

### Data Files (gitignored, in `data/`)

- `state.json` — current home state (mode, mood, energy, voice_lock)
- `devices.json` — registered device info
- `event_log.jsonl` — all incoming events
- `master_log.jsonl` — full master reasoning turns (trigger, context, decisions, dispatches, latency)

## What's Not Built Yet

- **Device-side runtimes** — Pi code: WebSocket client, hardware drivers, device agent loop
- **TTS** — ElevenLabs/Piper integration for Mirror and Radio
- **Tick scheduler** — periodic tick event generator (currently manual via curl)

## V1 Stack

- Python 3.11+
- FastAPI control plane on the laptop
- Claude Sonnet 4.6 as default master model (configurable, supports Cerebras)
- 1M context beta header `context-1m-2025-08-07`
- Claude Vision called centrally from the laptop (wired, triggers on people_count change only)
- Silero VAD + Groq Whisper sidecar for voice capture
- Voice events preempt vision-triggered master turns (cancellation + lock release)
- Simple device runtimes on Raspberry Pis (not yet built)

## Design Principles

- Optimize for demo reliability over theoretical elegance.
- Keep one shared brain on the laptop.
- Use deterministic routing for simple commands.
- Reserve LLM reasoning for ambiguous or multi-device requests.
- Keep master execution serial.
- Keep state explicit and file-backed.
- Prefer a few strong sensing surfaces over weak sensing everywhere.

## Gotchas (learned the hard way)

- **`state.update()` is shallow** — nested dicts like `spatial` get wiped. Always use `deep_merge` from `spatial.py`.
- **`load_dotenv()` must run before `import master`** — master reads env vars at import time for model config.
- **Vision trigger: don't use mood** — too noisy/high-variance. Only `people_count` changes are reliable triggers.
- **Snapshot state BEFORE writing updates** — if you write new state then compare for triggers, you compare new vs new (always equal). Snapshot first, write, then compare against snapshot.
- **`asyncio.sleep(0)` is not enough after task cancellation** — if the cancelled task is deep in nested awaits, one yield may not release the lock. Poll `_master_lock.locked()` in a loop.
- **`asyncio.to_thread` threads continue after cancellation** — cancelling the async wrapper raises `CancelledError` at the `await` point, but the thread keeps running. The result is just discarded. This is fine but be aware.
- **`data/` is gitignored except `data/room.json`** — need `!data/room.json` in `.gitignore` and `git add -f` for new tracked files under `data/`.

**Orchestrate, don't implement.** Delegate multi-file work to subagents (Task tool). Your context is for orchestration.

| Scope              | Action                        |
| ------------------ | ----------------------------- |
| 1-2 line fix       | Do it yourself                |
| Multi-file impl    | Opus subagent via `Task` tool |
| Planning/reasoning | Gemini 3 Pro                  |
| Bug diagnosis      | Codex                         |

### Models (MANDATORY)

- **Gemini CLI**: `gemini-3.1-pro-preview` (MCP read-only; use `gemini -m gemini-3.1-pro-preview --yolo "prompt"` via Bash for writes)
- **Codex CLI**: `mcp__codex__codex` with `model: "gpt-5.4"`, `sandbox: "workspace-write"`, `approval-policy: "never"`
- **Brainstorm**: `mcp__gemini-cli__brainstorm` with `model: "gemini-3.1-pro-preview"`

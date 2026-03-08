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
| `control_plane/router.py` | Emergency stop + voice lock helpers (deterministic routing removed) |
| `control_plane/app.py` | FastAPI app: endpoints, WebSocket ConnectionManager, transcript debouncer, event pipeline |
| `control_plane/master.py` | Master reasoning engine (multi-provider: Anthropic + Cerebras) |
| `control_plane/SOUL.md` | Master home personality, multi-person rules, tool-use rules |
| `requirements.txt` | Python dependencies |

### Lamp Device Runtime (fully working, arm + LED tested end-to-end on real hardware)

| File | Purpose |
|------|---------|
| `devices/lamp/main.py` | Entry point: `--connect` for WS runtime, CLI sim mode preserved |
| `devices/lamp/ws_client.py` | WebSocket client: register, connect, heartbeat, auto-reconnect |
| `devices/lamp/agent.py` | LLM agent loop (Cerebras gpt-oss-120b), tools: `pose`, `set_color`, `set_brightness`, `flash`, `pulse`, `done` |
| `devices/lamp/hardware.py` | Hardware controller: lerobot Robot API for arm (smooth interpolation), LED_control for RGB LED |
| `devices/lamp/planner.py` | Simplified regex planner for Layer 1 direct commands (COLOR_MAP, POSE_HINTS) |
| `devices/lamp/LED_control.py` | lgpio PWM RGB LED driver (GPIO 17/27/22) |
| `devices/lamp/poses.json` | Recorded poses — on Pi only. Tools built dynamically from this file (no code changes needed to add poses) |
| `devices/lamp/move.py` | Direct servo movement CLI (standalone utility) |
| `devices/lamp/record.py` | Pose recording utility (standalone) |
| `devices/lamp/play_animation.py` | Replay recorded pose sequences (standalone) |
| `devices/lamp/test_led.py` | LED test sequences (standalone) |
| `devices/lamp/test_hardware.py` | Standalone hardware test script for Pi |
| `devices/lamp/sync.sh` | rsync deploy script for Pi |

Run on Pi: `ssh -f lamp@lamphost 'cd /home/lamp/Desktop/lamp && nohup /home/lamp/Desktop/venv/bin/python3 main.py --connect --live-serial > /tmp/lamp.log 2>&1 &'`
Deploy: `cd devices/lamp && ./sync.sh`

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

### 2D Spatial Map + Dashboard (served at localhost:8000/)

| File | Purpose |
|------|---------|
| `dashboard/map.js` | SVG room map, device icons, brain HUD terminal, device panels, rover animation |
| `dashboard/state.js` | State pills (mode, mood, people count), polling updates |
| `dashboard/timeline.js` | Event/dispatch feed, generates overlay data for map.js |
| `dashboard/styles.css` | Dark theme, brain HUD, device panel cards, animations |
| `dashboard/index.html` | Full-viewport layout, polling loop, transcript input |
| `control_plane/spatial.py` | Spatial state management, deep_merge, waypoint resolution |
| `data/room.json` | Static room config (500×400cm, furniture, anchors, waypoints) |

### Mirror Device Runtime (deployed on Pi, tested end-to-end)

| File | Purpose |
|------|---------|
| `devices/mirror/main.py` | Entry point: `--connect` for WS runtime, auto-detects Wayland display env |
| `devices/mirror/ws_client.py` | WebSocket client: register, connect, heartbeat, auto-reconnect |
| `devices/mirror/agent.py` | LLM agent loop (Cerebras gpt-oss-120b), tools: `display`, `edit_photo`, `show_original`, `capture_frame`, `dismiss`, `done` |
| `devices/mirror/display.py` | Pygame fullscreen display: idle=black (behind two-way mirror), GENERATED state with 120s TTL |
| `devices/mirror/camera.py` | picamera2 (Pi 5 CSI) with OpenCV fallback, persistent capture thread |
| `devices/mirror/image_generation.py` | OpenAI gpt-image-1.5 for generation + editing, saves original before edits |
| `devices/mirror/planner.py` | Regex planner for Layer 1 commands |
| `devices/mirror/SOUL.md` | Mirror personality: Snapchat-filter style edits, emotional drawings, before/after |

Pi: `mirrorhost` / `100.89.245.128`, SSH: `mirror@mirrorhost` (password: mirror, key installed)
Boot: `ssh mirror@mirrorhost 'cd ~/Mirror && MASTER_URL="http://claude-master:8000" ~/mirror-venv/bin/python3 main.py --connect'`
Deploy: `scp devices/mirror/{main.py,ws_client.py,agent.py,display.py,camera.py,image_generation.py,planner.py,models.py,SOUL.md,config.yaml} mirror@mirrorhost:~/Mirror/`

### Rover Device Runtime (deployed on Pi, tested end-to-end)

| File | Purpose |
|------|---------|
| `devices/rover/main.py` | Entry point: lazy motion import (encoder threads burn CPU if imported early) |
| `devices/rover/ws_client.py` | WebSocket client to control plane |
| `devices/rover/agent.py` | LLM agent loop (Cerebras gpt-oss-120b), tools: `move`, `rotate`, `stop`, `emote`, `done` |
| `devices/rover/planner.py` | Regex planner for Layer 1 direct commands |
| `devices/rover/motion.py` | PID-controlled differential drive, encoder polling with 100μs sleep |

Pi: `roverhost` / `100.97.253.17`, SSH: `rover@roverhost` (password: rover, key installed)
Boot: `ssh rover@roverhost 'cd ~/Rover && MASTER_URL="http://claude-master:8000" ~/rover-venv/bin/python3 main.py --connect'`

### Radio Device Runtime (audio + dial hardware working, no WS client yet)

| File | Purpose |
|------|---------|
| `devices/radio/brain.py` | Audio decision engine: routes requests to pre-recorded clips via OpenAI |
| `devices/radio/config.yaml` | Hardware config (USB speaker, PCA9685 servo dial) |
| `devices/radio/SOUL.md` | Radio personality |
| `devices/radio/RASPi/main.py` | Pi entry point: `--loop` for interactive, CLI for single commands |
| `devices/radio/RASPi/runtime.py` | Hardware executor: plays audio, triggers dial spins |
| `devices/radio/RASPi/audio.py` | Local audio playback (mpg123/ffplay/cvlc) |
| `devices/radio/RASPi/dial.py` | PCA9685 servo control for physical dial |
| `devices/radio/Sounds/` | Audio asset library (29 pre-recorded clips) |

Pi: `radiohost` / `100.119.150.35`
Test: `python3 devices/radio/RASPi/main.py "play calm morning music"`

### Networking

All device configs (`devices/*/config.yaml`) use Tailscale hostnames:
- Laptop: `claude-master`
- Pis: `lamphost`, `mirrorhost`, `radiohost`, `roverhost`
- Setup script: `scripts/setup-pi-tailscale.sh`

## How to Test

```bash
# 1. Install deps
pip3 install -r requirements.txt

# 2. Ensure .env has ANTHROPIC_API_KEY and CEREBRAS_API_KEY set

# 3. Start the control plane
python3 -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000

# 4. Start the lamp (in a separate terminal)
cd devices/lamp && MASTER_URL="http://localhost:8000" python3 main.py --connect
# Lamp auto-registers, connects via WebSocket, warms up LLM on boot

# 5. Start other devices (each auto-registers on connect)
# Mirror:
ssh mirror@mirrorhost 'cd ~/Mirror && MASTER_URL="http://claude-master:8000" ~/mirror-venv/bin/python3 main.py --connect'
# Rover:
ssh rover@roverhost 'cd ~/Rover && MASTER_URL="http://claude-master:8000" ~/rover-venv/bin/python3 main.py --connect'

# 6. Test master reasoning (all transcripts go to master, no deterministic routing)
curl -X POST localhost:8000/events -H 'Content-Type: application/json' \
  -d '{"device_id":"global_mic","kind":"transcript","payload":{"text":"I need to lock in"}}'
# Note: transcript is buffered for 1.5s by debouncer, then master fires async

# 7. Check results (wait 3-15s for master reasoning + lamp LLM agent loop)
curl localhost:8000/state        # Current home state
curl localhost:8000/devices      # Registered devices
curl localhost:8000/events       # Recent event log
curl localhost:8000/master-log   # Master reasoning + device results

# 8. Open dashboard
open http://localhost:8000       # Full-viewport room map with live device status
```

### Model Config (in `.env`)

```bash
# Master (control plane)
MASTER_MODEL=claude-sonnet-4-6   # Default. Options: claude-opus-4-6, gpt-oss-120b
MASTER_PROVIDER=auto             # auto-detects from model name. Options: anthropic, cerebras

# Device agents (lamp, mirror)
CEREBRAS_API_KEY=csk-...         # REQUIRED for lamp/mirror LLM agent loops
LAMP_AGENT_MODEL=gpt-oss-120b   # Default. Cerebras model for lamp agent
MIRROR_AGENT_MODEL=gpt-oss-120b  # Default. Cerebras model for mirror
```

### Data Files (gitignored, in `data/`)

- `state.json` — current home state (mode, mood, people_count, voice_lock)
- `devices.json` — registered device info
- `event_log.jsonl` — all incoming events
- `master_log.jsonl` — full master reasoning turns (trigger, context, decisions, dispatches, latency)

## What's Not Built Yet

- **Radio WebSocket client** — Radio has audio + dial hardware working but no control plane integration (no ws_client.py)
- **TTS** — ElevenLabs/Piper integration for Radio
- **Tick scheduler** — periodic tick event generator (currently manual via curl)

## V1 Stack

- Python 3.11+
- FastAPI control plane on the laptop
- Claude Opus 4.6 as master model (configurable via MASTER_MODEL env var)
- 1M context beta header `context-1m-2025-08-07`
- Claude Vision called centrally from the laptop (wired, triggers on people_count change only)
- Silero VAD + Groq Whisper sidecar for voice capture
- Voice events preempt vision-triggered master turns (cancellation + lock release)
- Lamp runtime on Pi (Cerebras gpt-oss-120b agent loop, arm + LED)
- Mirror runtime on Pi (Cerebras agent + OpenAI gpt-image-1.5 for image gen/edit, picamera2, Pygame fullscreen)
- Rover runtime on Pi (Cerebras agent, PID differential drive, encoder feedback)
- Radio runtime on Pi (audio playback + servo dial, no WS client yet)

## Design Principles

- Optimize for demo reliability over theoretical elegance.
- Keep one shared brain on the laptop.
- All transcripts go to master reasoning (no deterministic routing).
- No silent fallbacks — fail loud or don't fail.
- Keep master execution serial.
- Keep state explicit and file-backed.
- Prefer a few strong sensing surfaces over weak sensing everywhere.

## Gotchas (learned the hard way)

- **`state.update()` is shallow** — nested dicts like `spatial` get wiped. Always use `deep_merge` from `spatial.py`.
- **`load_dotenv()` must run before `import master` or `import agent`** — both read env vars at import time for model config. Lamp's main.py loads from project root `.env`.
- **Vision trigger: don't use mood** — too noisy/high-variance. Only `people_count` changes are reliable triggers.
- **Snapshot state BEFORE writing updates** — if you write new state then compare for triggers, you compare new vs new (always equal). Snapshot first, write, then compare against snapshot.
- **`asyncio.sleep(0)` is not enough after task cancellation** — if the cancelled task is deep in nested awaits, one yield may not release the lock. Poll `_master_lock.locked()` in a loop.
- **`asyncio.to_thread` threads continue after cancellation** — cancelling the async wrapper raises `CancelledError` at the `await` point, but the thread keeps running. The result is just discarded. This is fine but be aware.
- **`data/` is gitignored except `data/room.json`** — need `!data/room.json` in `.gitignore` and `git add -f` for new tracked files under `data/`.
- **Cerebras cold start is 7-14s** — the lamp agent warms up the LLM connection on boot (`agent.warmup()`). Without this, the first real instruction takes 14s+ and may timeout. With warmup, subsequent calls are <500ms.
- **`tool_choice="required"` on Cerebras** — without it, the model sometimes returns plain text JSON instead of calling the `done` tool, wasting a full iteration on a nudge message.
- **Transcript debouncer buffers 1.5s** — test events via curl return "transcript buffered" immediately. Master reasoning fires async after the flush. Check master-log after 3-15s.
- **Resetting state.json clears spatial positions** — if you wipe state, device positions disappear from the dashboard. Restore from room.json anchors.
- **Emergency stop pattern is strict** — requires "stop stop", "stop all", "emergency stop", "freeze all", or "no no no". Single "stop" does NOT trigger it (was too sensitive with voice transcription).
- **`gripper.pos` must be stripped from poses** — SO100FollowerConfig has 5 motors, no gripper. Passing `gripper.pos` causes `StopIteration` crash. `hardware.py` handles this automatically.
- **Lamp hardware.py uses Robot API, NOT raw servo bus** — `compat.py` was deleted. The working path is `SO100FollowerConfig` + `make_robot_from_config()` + `robot.send_action()` with `.pos` suffix keys.
- **Lamp poses are dynamic** — agent tools are built from `poses.json` at boot. To add a pose: `record.py save <name>` on Pi → restart lamp process. No code changes or redeployment needed.
- **Mirror Pi uses picamera2, NOT OpenCV** — Pi 5 CSI cameras (OV5647) don't work with `cv2.VideoCapture`. camera.py tries picamera2 first, falls back to OpenCV.
- **Mirror display is portrait (rotated 90°)** — `wlr-randr --output HDMI-A-2 --transform 90` in `~/.config/labwc/autostart`. Pygame sees 600x1024, API generates 1024x1536 portrait images.
- **Mirror idle = pure black** — display.py renders black when no generated image is showing. Behind a two-way mirror, any pixels bleed through.
- **Mirror Pi desktop must be killed** — `~/.config/labwc/autostart` overrides system autostart: runs `swaybg -c "#000000"`, does NOT start pcmanfm or wf-panel-pi.
- **Mirror SSH: `mirror@mirrorhost`** — password `mirror`, key installed. Was rate-limited by fail2ban after rapid SSH attempts; power cycle clears it.
- **Mirror dotenv path** — main.py loads from local dir first (`_here / ".env"`), then project root. Same fix as rover.
- **Rover SSH: `rover@roverhost`** — password `rover`, key installed.
- **Rover motion.py encoder threads** — busy-loops with `time.sleep(0.0001)`. Don't import at startup; lazy-load when needed.

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

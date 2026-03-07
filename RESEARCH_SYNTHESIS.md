# ClaudeHome: Full Research Synthesis

This document is background research only.

It is not the canonical architecture or implementation spec.
For the build plan, use:

- `IMPLEMENTATION_SPEC.md`
- `docs/architecture/*.mmd`

## Research Sources (6 Agents Completed)
1. Claude Code Agent Teams architecture (file-based messaging, task system, context compaction)
2. Claude Agent SDK (subagent patterns, context isolation, tool delegation, memory)
3. Smart Home AI landscape (existing approaches, emotion detection, voice, event-driven IoT)
4. OpenClaw architecture deep dive (6-stage pipeline, 3-tier memory, skills, multi-agent routing, identity)
5. OpenClaw via Lex Fridman + Pragmatic Engineer (Peter's multi-agent workflow, philosophy, smart home uses)
6. NanoClaw containerized architecture (container isolation, skill pipeline, security, deployment)

---

## Part 1: Key Architectural Insights from OpenClaw

### The Agent Loop (What We Should Steal)

OpenClaw's core loop is a **6-stage pipeline** built on Pi SDK's ReAct pattern:

```
Ingestion -> Access Control/Routing -> Context Assembly -> Model Invocation -> Tool Execution -> Response Delivery
```

**What matters for us:**
- **Serial execution by default** (Lane Queue): One agent turn per session. No race conditions. Messages queue up if the agent is busy. This is critical for our master agent -- it should process events one at a time, not get overwhelmed.
- **Context Assembly is the key stage**: Before each model call, OpenClaw assembles the system prompt from workspace files (AGENTS.md, SOUL.md, USER.md), session history, semantically relevant memory, and dynamically injected skills. We need the same pattern for our master agent -- assembling user state, preferences, device registry, and recent events before each inference.
- **Heartbeat for proactive activation**: OpenClaw wakes every 30 minutes to check if attention is needed. Our master agent should have a similar heartbeat -- periodically checking if the user's state warrants proactive action ("You've been at the desk for 2 hours...").

### The Memory System (What We Should Adapt)

OpenClaw uses a **3-tier memory architecture** that is directly applicable:

| OpenClaw Layer | Our Equivalent | How It Works |
|----------------|----------------|--------------|
| **Daily Logs** (ephemeral, `memory/YYYY-MM-DD.md`) | **Event Log** (rolling window of recent events) | Append-only capture. Today + yesterday loaded at session start. |
| **Curated Memory** (durable, `MEMORY.md`) | **Preferences DB** (pre-loaded user profile) | Long-term facts, preferences, habits. Injected into system prompt. |
| **Session Memory** (searchable transcripts) | **Session State** (current mood, intent, device states) | In-memory JSON object updated on every event. |

**The killer feature: Memory flush before compaction.** When OpenClaw's context window fills up, it first runs a silent agent turn that writes important information from the conversation to durable memory files. THEN it compacts. This means nothing important is lost. We should do the same -- before compacting the master agent's context, flush the current user state and any learned preferences to the preferences DB.

**Hybrid search for memory retrieval:** OpenClaw uses vector embeddings (70%) + BM25 keyword search (30%) with MMR re-ranking to prevent redundant results. For our demo, we don't need this complexity -- simple JSON lookup is fine. But for a production version, this is the right pattern.

### Multi-Agent Architecture (What We Should Mirror)

OpenClaw runs **multiple isolated agents within one Gateway instance**, each with:
- Its own workspace (config files, skills)
- Its own state directory
- Its own session store
- Deterministic routing (most-specific-wins)

**Agent-to-agent communication** uses 4 tools (disabled by default):
- `sessions_list`: Discover active sessions
- `sessions_send`: Message another session
- `sessions_history`: Fetch transcripts
- `sessions_spawn`: Start a sub-agent

**This maps directly to our architecture:**
- Each device agent = an OpenClaw agent with its own workspace
- The master agent = the "main" agent with routing priority
- Device -> master communication = `sessions_send`
- Master -> device commands = `sessions_send` with structured commands

### Identity / SOUL.md (What Makes This a Hackathon Winner)

OpenClaw loads `SOUL.md` at every session start -- "reading itself into being." Each agent has a behavioral philosophy, values, and personality.

**For our project, each device agent should have its own SOUL.md:**
- Camera agent: "You are the eyes of the home. You observe without judgment. You notice subtle changes in mood and energy."
- Lamp agent: "You are the ambient painter. You shape the emotional texture of the room through light."
- Robot agent: "You are the playful companion. You bring physical presence and warmth."

This isn't just flavor text -- it shapes how each agent interprets commands and makes autonomous micro-decisions.

### Peter's "Agentic Trap" Warning

Peter describes three phases of agent development:
1. **Phase 1 (Beginner)**: Simple prompts
2. **Phase 2 (Over-engineering)**: Complex orchestration, multi-agent frameworks, elaborate workflows
3. **Phase 3 (Zen)**: Return to simplicity, backed by good infrastructure

**For our hackathon: aim for Phase 3.** Don't build elaborate orchestration frameworks. Build good infrastructure (message bus, state store, clear system prompts) and let Claude reason about what to do.

---

## Part 2: Key Insights from Claude Code Agent Teams

### File-Based Messaging (Simple and Robust)

Claude Code Agent Teams uses **file-based messaging** -- each agent has an inbox file (JSON array of messages). This works across all environments with zero infrastructure.

**For our project:** We could use this exact pattern instead of Redis/MQTT. Each device agent has an inbox file. The master writes to device inboxes. Devices write to the master inbox. A file watcher triggers processing.

**However**, for a hackathon with physical hardware, **Redis Pub/Sub or simple HTTP** is more practical because:
- Devices might run on separate machines (Raspberry Pi)
- File-based messaging requires shared filesystem
- HTTP/WebSocket is universal

### Context Compaction Bug (Critical Lesson)

There's a **known bug** (Issue #23620) where compacting the team lead's context loses ALL team state -- the lead forgets teammates exist. OpenClaw solves this with pre-compaction memory flush.

**For our project:** The master agent MUST write its current state to a durable file before any compaction. On every inference call, it should re-read this state file. This way, even if context is lost, the master can reconstruct its understanding.

### Subagents vs. Agent Teams

| Aspect | Subagents | Agent Teams |
|--------|-----------|-------------|
| Lifetime | Short, synchronous | Persistent, asynchronous |
| Communication | Results return to parent only | Peer-to-peer messaging |
| Best for | Focused tasks | Ongoing collaboration |

**Our device agents are closer to Agent Teams** -- they're persistent, asynchronous, and can push messages to the master at any time. But the master's relationship to devices is closer to **orchestrator-to-subagent** -- it sends commands and expects results.

**Hybrid approach:** Device agents run as persistent processes (like Agent Team teammates). But the master treats them as tools it can invoke (like subagents). This gives us the best of both worlds.

---

## Part 3: Smart Home AI Landscape

### What Already Exists (and How We're Different)

| Project | What It Does | Our Advantage |
|---------|-------------|---------------|
| Home Assistant + LLM | User says command -> LLM translates to API call | We're proactive, not just reactive |
| IoTGPT (academic) | Task decomposition for IoT commands via DAG | We have emotion awareness + continuous monitoring |
| SAGE | Tree of LLM prompts for grounded execution | We have multi-agent autonomy per device |
| Emotion-LLaMA (NeurIPS 2024) | Multimodal emotion recognition | We integrate this INTO the orchestration loop |
| Home Assistant Voice Pipeline | Wake word -> STT -> LLM -> action -> TTS | We don't need a wake word -- we're always aware |

### Key Technologies We Should Use

**Emotion Detection:**
- Claude Vision API directly (send camera frames, get mood assessment)
- MorphCast (browser-based, lightweight) as a fallback
- 7-class emotion model: angry, disgust, fear, happy, neutral, sad, surprise

**Voice Pipeline:**
- Whisper (OpenAI) for STT -- industry standard, fast, accurate
- ElevenLabs for TTS -- natural sounding, low latency
- No wake word needed -- continuous listening with silence detection

**Device Integration:**
- Direct HTTP/WebSocket APIs for smart bulbs (Hue, LIFX, WLED)
- GPIO/serial for custom hardware (servos, LEDs)
- MQTT as lightweight pub/sub if needed

### What Doesn't Work (Avoid These)

- LLM hallucination in safety-critical IoT (door locks, stoves) -- keep our demo to non-dangerous actuators
- Real-time emotion recognition degrades with low-res cameras and bad lighting -- use good webcams
- Cloud LLM latency (1-3s) breaks "ambient intelligence" feel -- accept this for hackathon, note it as future work
- Multi-agent frameworks (CrewAI, AutoGen, LangGraph) are designed for enterprise workflows, not real-time IoT -- build our own lightweight layer

---

## Part 4: Unified Architecture

### System Overview

```
                    VOICE INPUT (Whisper STT)
                           |
                           v
┌──────────────────────────────────────────────────────────────┐
│                     MASTER AGENT                              │
│                                                              │
│  Model: Claude Sonnet 4.6 (fast) or Opus 4.6 (smart)        │
│                                                              │
│  System Prompt (assembled each call):                        │
│  1. SOUL.md -- "You are the brain of this home..."           │
│  2. USER.md -- Pre-loaded preferences from DB                │
│  3. STATE.json -- Current user state (mood, intent, etc.)    │
│  4. DEVICES.json -- Registry of all devices + capabilities   │
│  5. EVENT_LOG -- Last N events (rolling window)              │
│                                                              │
│  Tools:                                                      │
│  - command_device(device_id, action, params)                 │
│  - query_device(device_id, question)                         │
│  - speak_to_user(message) -- TTS output                      │
│  - update_state(field, value) -- modify user state           │
│  - flush_memory() -- write state to durable storage          │
│                                                              │
│  Triggered by:                                               │
│  - Events from device agents (via message bus)               │
│  - Voice commands (via Whisper pipeline)                     │
│  - Heartbeat timer (every 5 minutes)                         │
└──────┬────────────────┬─────────────────┬────────────────────┘
       │                │                 │
  ┌────▼────┐    ┌──────▼──────┐   ┌──────▼──────┐
  │ CAMERA  │    │   LAMP      │   │   ROBOT     │
  │ AGENT   │    │   AGENT     │   │   AGENT     │
  │         │    │             │   │             │
  │ SOUL:   │    │ SOUL:       │   │ SOUL:       │
  │ "I am   │    │ "I paint    │   │ "I am the   │
  │ the eyes│    │ with light" │   │ companion"  │
  │ of the  │    │             │   │             │
  │ home"   │    │ Receives:   │   │ Receives:   │
  │         │    │ - commands  │   │ - commands  │
  │ Pushes: │    │ - user state│   │ - user state│
  │ - mood  │    │             │   │             │
  │ - events│    │ Autonomous: │   │ Autonomous: │
  │ - alerts│    │ - micro-    │   │ - how to    │
  │         │    │   adjust    │   │   execute   │
  │         │    │   within    │   │   commands  │
  │         │    │   scene     │   │   creatively│
  └─────────┘    └─────────────┘   └─────────────┘
```

### Communication Protocol

```
┌─────────────────────────────────────────────────┐
│              MESSAGE BUS (HTTP/WebSocket)         │
│                                                  │
│  Master listens on: ws://localhost:8000/master   │
│  Camera posts to:   POST /master/events          │
│  Master commands:   POST /device/{id}/command     │
│  State broadcast:   POST /devices/broadcast       │
│                                                  │
│  Message Format:                                 │
│  {                                               │
│    "from": "camera_living_room",                 │
│    "type": "event|command|state_update|query",   │
│    "timestamp": "2026-03-05T10:30:00Z",          │
│    "data": { ... }                               │
│  }                                               │
└─────────────────────────────────────────────────┘
```

### Event Types

**Upward (device -> master):**
```json
{
  "type": "event",
  "from": "camera_living_room",
  "event": "mood_change",
  "data": {
    "mood": "stressed",
    "confidence": 0.85,
    "activity": "pacing",
    "people_count": 1
  }
}
```

**Downward (master -> device):**
```json
{
  "type": "command",
  "from": "master",
  "to": "lamp_desk",
  "action": "set_scene",
  "params": {
    "scene": "focus",
    "brightness": 80,
    "color_temp": 5000,
    "transition_ms": 2000
  }
}
```

**Broadcast (master -> all devices):**
```json
{
  "type": "state_update",
  "from": "master",
  "data": {
    "user": {
      "mood": "focused",
      "intent": "deep_work",
      "energy": "medium",
      "in_room": true,
      "last_voice_command": "I need to lock in",
      "time_at_desk_minutes": 45
    }
  }
}
```

### State Management

**STATE.json (in-memory, updated on every event):**
```json
{
  "user": {
    "mood": "focused",
    "mood_confidence": 0.85,
    "intent": "deep_work",
    "energy": "medium",
    "in_room": true,
    "position": "desk",
    "last_voice_command": "I need to lock in",
    "last_voice_timestamp": "2026-03-05T10:30:00Z",
    "session_start": "2026-03-05T10:15:00Z",
    "time_at_desk_minutes": 45,
    "override_active": false
  },
  "environment": {
    "lamp_desk": {"brightness": 80, "color_temp": 5000, "scene": "focus"},
    "lamp_ambient": {"brightness": 20, "color_temp": 2700, "scene": "dim"},
    "speaker": {"playing": "lo-fi-focus", "volume": 30},
    "thermostat": {"target_temp": 68, "current_temp": 69}
  },
  "event_log": [
    {"t": "10:15", "src": "camera", "event": "person_entered", "mood": "tired"},
    {"t": "10:16", "src": "voice", "text": "I need to lock in"},
    {"t": "10:17", "src": "master", "action": "activated focus mode"},
    {"t": "10:45", "src": "camera", "event": "posture_change", "detail": "rubbing eyes"}
  ]
}
```

**USER.md (pre-loaded, read-only during demo):**
```markdown
# User Profile: [Name]

## Preferences
- Prefers warm dim lighting (2700K, 40%) when relaxed
- Prefers cool bright lighting (5000K, 80%) when working
- Focus music: lo-fi hip hop or classical piano
- Ideal work temperature: 68F
- Gets stressed easily in afternoons
- Hates overhead fluorescent lighting
- Morning routine: coffee, news, 15min warmup

## Habits
- Usually works in 90-minute blocks
- Takes breaks by walking to kitchen
- Rubbing eyes = fatigue signal
- Leaning back + sighing = frustration signal
- Humming/tapping = good flow state

## Communication Style
- Prefers the house to be subtle, not chatty
- Only speak up for important things (breaks, alerts)
- Never say "as an AI" or be robotic
- Tone: warm, concise, like a good friend
```

---

## Part 5: What Makes This a Hackathon Winner

### The Narrative: "A Nervous System for Your Home"

Sensory neurons (cameras, mics) fire signals to the brain (master Claude), which reasons about the user's state and sends motor commands to effectors (lamps, robots, speakers). The home has:
- **Perception** (camera agent detects mood)
- **Cognition** (master agent reasons about intent)
- **Memory** (preferences DB + session state)
- **Action** (device agents execute creatively)
- **Identity** (each device has a soul)

### What's Novel

1. **Proactive ambient intelligence** -- the house acts before you ask
2. **Distributed device intelligence** -- each device has its own Claude brain making micro-decisions
3. **Emotion-driven orchestration** -- mood is a first-class input, not a gimmick
4. **Natural language as the ONLY control plane** -- no app, no buttons
5. **Agent identity via SOUL.md** -- each device "reads itself into being"
6. **OpenClaw-inspired memory architecture** -- 3-tier memory with pre-compaction flush

### Demo Flow (The "Golden Path")

1. System boots. Master loads USER.md preferences. All device agents initialize with their SOUL.md.
2. User enters room. Camera agent detects person, assesses mood (tired + carrying laptop).
3. Camera pushes event to master. Master reasons: tired + laptop = probably about to work.
4. Master commands: warm lights at 60%, gentle ambient music. (Subtle, not aggressive.)
5. User says: "I need to lock in." Voice pipeline -> master.
6. Master confirms intent: deep focus. Commands: cool lights at 80%, lo-fi playlist, temp to 68F.
7. 30 minutes pass. Camera detects fatigue (rubbing eyes, leaning back).
8. Master proactively speaks: "You've been going for 30 minutes. Want a break?"
9. User: "Yeah, take a break." Master transitions to relaxation mode.
10. User: "Actually keep the lights bright." Master respects override, adjusts only lamp.

---

## Part 6: Software Stack

### Language & Framework
- **Python 3.11+** -- Best Anthropic SDK support, easy hardware integration
- **FastAPI** -- HTTP/WebSocket server for message bus
- **Anthropic Python SDK** -- Claude API calls with tool_use
- **Whisper** (OpenAI) -- Speech-to-text
- **ElevenLabs** or **pyttsx3** -- Text-to-speech

### File Structure (Proposed)
```
claude-home/
  master/
    agent.py          -- Master agent loop
    state.py          -- State management
    tools.py          -- Tool definitions for Claude
    server.py         -- FastAPI message bus
    soul.md           -- Master agent identity
    user.md           -- Pre-loaded user preferences
  devices/
    camera/
      agent.py        -- Camera agent loop
      vision.py       -- Frame capture + Claude Vision calls
      soul.md         -- Camera agent identity
    lamp/
      agent.py        -- Lamp agent loop
      controller.py   -- Hardware control (Hue API / GPIO)
      soul.md         -- Lamp agent identity
    robot/
      agent.py        -- Robot agent loop
      controller.py   -- Servo/motor control
      soul.md         -- Robot agent identity
    mic/
      agent.py        -- Microphone agent loop
      stt.py          -- Whisper STT pipeline
      soul.md         -- Mic agent identity
  shared/
    messages.py       -- Message types and schemas
    config.py         -- Device registry, API keys
    state_schema.py   -- State JSON schema
  data/
    user_preferences.json  -- Pre-loaded preference DB
    state.json             -- Runtime state (written by master)
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Message bus | FastAPI WebSocket + HTTP | Simple, Python-native, no external deps |
| Master model | Claude Sonnet 4.6 | Fast enough for real-time, smart enough for reasoning |
| Camera analysis | Claude Vision API | Skip local model complexity for hackathon |
| Voice STT | Whisper API | Industry standard, fast |
| Voice TTS | ElevenLabs API | Natural sounding |
| State storage | JSON file + in-memory dict | Simple, inspectable, sufficient for demo |
| Preferences | Markdown file (USER.md) | Human-readable, easy to edit before demo |
| Device control | HTTP APIs + GPIO | Universal across smart bulbs and custom hardware |
| Agent identity | SOUL.md per device | Shapes behavior, compelling for judges |

---

## Sources

### OpenClaw / ClawdBot
- [OpenClaw Wikipedia](https://en.wikipedia.org/wiki/OpenClaw)
- [OpenClaw Official Site](https://openclaw.ai/)
- [Peter Steinberger's Blog: OpenClaw, OpenAI and the future](https://steipete.me/posts/2026/openclaw)
- [Lex Fridman #491: OpenClaw Transcript](https://lexfridman.com/peter-steinberger-transcript/)
- [Pragmatic Engineer: "I ship code I don't read"](https://newsletter.pragmaticengineer.com/p/the-creator-of-clawd-i-ship-code)
- [How OpenClaw Works (Medium)](https://bibek-poudel.medium.com/how-openclaw-works-understanding-ai-agents-through-a-real-architecture-5d59cc7a4764)
- [Inside OpenClaw: Under the Hood (DEV)](https://dev.to/jiade/inside-openclaw-how-the-worlds-fastest-growing-ai-agent-actually-works-under-the-hood-4p5n)
- [OpenClaw Architecture Explained (Substack)](https://ppaolo.substack.com/p/openclaw-system-architecture-overview)
- [OpenClaw Memory System Deep Dive](https://snowan.gitbook.io/study-notes/ai-blogs/openclaw-memory-system-deep-dive)
- [OpenClaw Docs: Memory](https://docs.openclaw.ai/concepts/memory)
- [OpenClaw Docs: Skills](https://docs.openclaw.ai/tools/skills)
- [OpenClaw Docs: Multi-Agent](https://docs.openclaw.ai/concepts/multi-agent)
- [NanoClaw: Meet the containerized OpenClaw (The Register)](https://www.theregister.com/2026/03/01/nanoclaw_container_openclaw/)
- [NanoClaw Security Model](https://nanoclaw.dev/blog/nanoclaw-security-model/)

### Claude Code Agent Teams & SDK
- [Claude Code Agent Teams Docs](https://code.claude.com/docs/en/agent-teams)
- [Claude Code Hidden Multi-Agent System (paddo.dev)](https://paddo.dev/blog/claude-code-hidden-swarm/)
- [Claude Agent SDK Blog](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)
- [Claude Code Multi-Agent Orchestration Gist](https://gist.github.com/kieranklaassen/d2b35569be2c7f1412c64861a219d51f)
- [Claude Code Swarm Orchestration Skill Gist](https://gist.github.com/kieranklaassen/4f2aba89594a4aea4ad64d753984b2ea)
- [Agent Teams Context Compaction Bug (#23620)](https://github.com/anthropics/claude-code/issues/23620)
- [Automatic Context Compaction Cookbook](https://platform.claude.com/cookbook/tool-use-automatic-context-compaction)

### Smart Home AI
- [AIoT Smart Home via Autonomous LLM Agents (IEEE)](https://ieeexplore.ieee.org/document/10729865)
- [IoTGPT: Personalized Smart Home Automation (arXiv)](https://arxiv.org/html/2601.04680v1)
- [Home Assistant AI Agents Blog](https://www.home-assistant.io/blog/2024/06/07/ai-agents-for-the-smart-home/)
- [LLM Vision for Home Assistant](https://llmvision.org/)
- [Emotion-LLaMA (NeurIPS 2024)](https://github.com/ZebangCheng/Emotion-LLaMA)
- [MorphCast Emotion AI](https://www.morphcast.com/)
- [Facial Emotion Recognition for Smart Lighting (Frontiers 2025)](https://www.frontiersin.org/journals/neuroscience/articles/10.3389/fnins.2025.1622194/full)

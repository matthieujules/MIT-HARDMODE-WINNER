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

## V1 Stack

- Python 3.11+
- FastAPI control plane on the laptop
- Claude Opus 4.6 (1M context, beta header `context-1m-2025-08-07`) as the master model
- Claude Vision called centrally from the laptop
- Whisper + VAD on the primary voice device
- simple device runtimes on Raspberry Pis

## Design Principles

- Optimize for demo reliability over theoretical elegance.
- Keep one shared brain on the laptop.
- Use deterministic routing for simple commands.
- Reserve LLM reasoning for ambiguous or multi-device requests.
- Keep master execution serial.
- Keep state explicit and file-backed.
- Prefer a few strong sensing surfaces over weak sensing everywhere.

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

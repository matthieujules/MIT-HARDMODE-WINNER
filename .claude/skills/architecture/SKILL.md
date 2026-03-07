---
name: architecture
description: Iterative systems architecture diagramming with Mermaid. Use when discussing system design, architecture changes, adding/removing components, or when the user asks to visualize the system.
---

# Architecture Diagramming Skill

You maintain living architecture diagrams in `docs/architecture/` as `.mmd` (Mermaid) files.

## Rules

1. **Never regenerate a whole diagram.** Use the Edit tool to surgically modify specific lines/sections.
2. **Always call `mermaid_preview`** after making changes so the user sees the update live in their browser.
3. **Use the same `preview_id`** for each diagram file across edits (e.g., `preview_id: "system-overview"`) so the browser tab auto-refreshes.
4. **One concept per diagram file.** Don't cram everything into one file.
5. **Keep diagrams under ~50 nodes.** Split into multiple files if larger.
6. **Use consistent direction:** `graph TD` for hierarchies, `graph LR` for flows/pipelines.
7. **Use subgraphs** to group related components.
8. **Use meaningful node IDs** (e.g., `master[Master Agent]` not `A[Master Agent]`).

## Diagram Files

| File | Purpose | Direction |
|------|---------|-----------|
| `system-overview.mmd` | C4-style context diagram — all major components and their relationships | `graph TD` |
| `message-flow.mmd` | How events flow: device → master → device responses | `graph LR` |
| `voice-pipeline.mmd` | Mic → Whisper → Master → TTS → Speaker | `graph LR` |
| `memory-architecture.mmd` | 3-tier memory: USER.md, STATE.json, EVENT_LOG | `graph TD` |

## Workflow

1. Read the current `.mmd` file
2. Discuss changes with the user
3. Edit specific lines with the Edit tool
4. Call `mermaid_preview` with the full updated diagram text and matching `preview_id`
5. Iterate until the user is satisfied
6. Call `mermaid_save` to export final PNG/SVG if requested

## Preview Defaults

Always use these settings for `mermaid_preview`:
- `theme: "dark"`
- `background: "transparent"`
- `width: 1200` (use 1000 for smaller diagrams)
- `height`: scale to content (600-900 for overviews, 400 for pipelines)

## Style Guide

- Agent nodes: rounded rectangles `master([Master Agent])`
- Input devices: hexagons `mic{{Microphone}}`
- Output devices: stadium shapes `lamp([Lamp])`
- External services: double brackets `whisper[[Whisper API]]`
- Data stores: cylinders `state[(STATE.json)]`
- Decisions: rhombuses `check{Mood changed?}`

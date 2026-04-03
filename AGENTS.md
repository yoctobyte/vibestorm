# Agent Collaboration Guide

This repository is intentionally multi-agent friendly. Codex, Claude Code, Antigravity, and other
agentic tools should treat the repo as a shared workspace rather than a single-tool sandbox.

## Read First

Before making meaningful changes, read these in order:

1. `README.md`
2. `projectstate.md`
3. `docs/current-handoff.md`
4. `docs/reverse-engineered-protocol.md`
5. `docs/README.md`

If the task is protocol work, keep `docs/reverse-engineered-protocol.md` open while decoding.

## Canonical Working Files

- `projectstate.md`: current high-level project state
- `docs/current-handoff.md`: rolling implementation handoff between agents
- `docs/reverse-engineered-protocol.md`: current protocol and wire-format knowledge
- `docs/local-opensim.md`: local OpenSim workflow and dev environment notes

Timestamped historical notes live under `docs/archive/` and are not the primary source of truth.

## Shared Rules

- Do not assume you are the only agent editing the repo.
- Do not revert unrelated changes unless explicitly asked.
- Prefer additive notes over private assumptions.
- When you learn something protocol-specific, update `docs/reverse-engineered-protocol.md`.
- When you change the current recommended workflow or repo structure, update `projectstate.md` or `docs/README.md`.
- When you stop with work in progress, update `docs/current-handoff.md`.

## Handoff Expectations

Any agent leaving non-trivial work should record:

- what changed
- what is now known
- what remains unknown
- what was verified
- one concrete next step

Use `docs/current-handoff.md` for the rolling handoff and `docs/handoff-template.md` if a fresh structured handoff is needed.

## Tool-Specific Notes

### Codex

- Good default for implementation, repo cleanup, tests, and integrating scattered state into current docs.

### Claude Code

- Good for deeper reasoning passes, protocol interpretation, and longer-form implementation notes.

### Antigravity

- Treat as another peer agent: read the same canonical docs, leave the same handoff quality, and avoid tool-specific hidden context.

## Preferred Workflow

1. Read the canonical docs.
2. Check `git status`.
3. Make the smallest coherent change set that advances the current task.
4. Verify what you can.
5. Update the current docs if the repo state or protocol knowledge changed.
6. Leave `docs/current-handoff.md` in a better state than you found it.

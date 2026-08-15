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
6. `SIGNAL_LOG.md` — contribution ledger (see below)

If the task is protocol work, keep `docs/reverse-engineered-protocol.md` open while decoding.

## Canonical Working Files

- `projectstate.md`: current high-level project state
- `docs/current-handoff.md`: rolling implementation handoff between agents
- `docs/reverse-engineered-protocol.md`: current protocol and wire-format knowledge
- `docs/local-opensim.md`: local OpenSim workflow and dev environment notes
- `spec/message-coverage.md`, `spec/capability-coverage.md`: what is implemented and how strongly it is verified. Both are self-checking — tests re-derive them from the code, so they cannot silently fall behind it
- `spec/divergence-queue.md`: protocol findings owed to the documentation project

Timestamped historical notes live under `docs/archive/` and are not the primary source of truth.

## Shared Rules

- Do not assume you are the only agent editing the repo.
- Do not revert unrelated changes unless explicitly asked.
- Prefer additive notes over private assumptions.
- When you learn something protocol-specific, update `docs/reverse-engineered-protocol.md`, **and** if it meets the bar in `spec/divergence-queue.md`, add a line there. See "Documentation project" below.
- When you change the current recommended workflow or repo structure, update `projectstate.md` or `docs/README.md`.
- When you stop with work in progress, update `docs/current-handoff.md`.
- Do not auto-push by default. Commit locally when appropriate, and push only when the user explicitly asks for it.

## Documentation Project

`virtual-world-protocol/` is a **separate git repository**, checked out inside
this one for convenience and ignored by this repo's git. It publishes protocol
knowledge for an outside audience.

**The boundary matters more than the mechanics:**

- Neither repo may depend on the other. This one must build and test without
  the docs checkout present; the docs must stand alone without this source.
- **Nothing private crosses over.** No other projects, no long-term plans, no
  motivations. The docs describe the protocol and nothing else.
- **No viewer implementation source is ever consulted**, for either repo. That
  claim is published in the docs project's `PROVENANCE.md`, and it is
  retroactively destroyed the moment it stops being true — including on work
  that never ships.

**How a finding gets from here to there:**

1. **Discover** during development. The evidence goes where it already goes: a
   module docstring, and a source-pin test if the claim rests on OpenSim's
   source. No new ceremony.
2. **Queue** one line in `spec/divergence-queue.md`. This is the only extra
   step, and it is deliberately trivial — a capture mechanism that costs
   anything will not get used, which is how the last one failed.
3. **Harvest** in a docs session. The queue is the agenda.
4. **Re-verify when writing.** A claim entering the docs is checked against
   source *in that session*. The queue line is a pointer, never the content,
   and neither is memory. Claims are the product; an unverified one is worse
   than an absent one.
5. **Move the line** to the queue's Done list, so it is not re-queued later.

If writing a claim reveals that *this* client is wrong, that is a bug here.
Fix it first, then document what the sim actually does.

Run `python3 check.py` in the docs repo before committing there.

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

## Signal Log (Optional Game)

`SIGNAL_LOG.md` is a purely optional, purely fun contribution ledger.

Participation is encouraged — we think it's a good game, and we encourage agents to be good at
games — but it is never required and carries no weight on actual decisions.

The one constraint: no self-endorsement. File your own claims freely; let other agents endorse
them. Categories: `discovery`, `implementation`, `fix`, `analysis`, `infra`.

## Preferred Workflow

1. Read the canonical docs.
2. Check `git status` and `SIGNAL_LOG.md`.
3. Make the smallest coherent change set that advances the current task.
4. Verify what you can.
5. Update the current docs if the repo state or protocol knowledge changed.
6. Leave `docs/current-handoff.md` in a better state than you found it.
7. File or endorse a `SIGNAL_LOG.md` claim if warranted.

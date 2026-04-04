# Project State

Last updated: 2026-04-04

## Current Summary

Vibestorm is now in active local OpenSim protocol reverse-engineering and implementation mode.

The repo already supports:

- XML-RPC login bootstrap
- capability seed resolution
- `EventQueueGet` polling
- UDP handshake and bounded live sessions
- zerocode and reliable/ACK handling
- message-template driven dispatch
- normalized world-state updates for region, time, coarse agents, and first object entities
- first structural `ImprovedTerseObjectUpdate` handling with best-effort terse `local_id` extraction
- file-based packet capture for selected messages
- SQLite-backed evidence collection at `local/unknowns.sqlite3`
- session-scoped evidence rows inside the SQLite store
- aggregate inbound-message census and unknown UDP dispatch-failure logging
- nearby chat capture for timestamped in-world notes

## What Is Stable

- `./run.sh opensim`
- `./run.sh bootstrap`
- `./run.sh caps`
- `./run.sh eventq`
- `./run.sh udp`
- `./run.sh handshake`
- `./run.sh session`
- `./run.sh session 180 --verbose`
- `./run.sh unknowns`
- `./run.sh fixtures`

The local OpenSim target is the current source of truth for live protocol experimentation.

## Current Technical Shape

Main implemented areas:

- `src/vibestorm/login/`: login/bootstrap
- `src/vibestorm/caps/`: seed capability resolution and LLSD support
- `src/vibestorm/event_queue/`: `EventQueueGet` polling
- `src/vibestorm/udp/`: packet parsing, template dispatch, semantic message helpers, session loop
- `src/vibestorm/world/`: normalized world-state models and updater
- `src/vibestorm/fixtures/`: fixture inventory and SQLite unknowns database

Current object/world coverage:

- region handshake and region metadata
- sim stats
- simulator time
- coarse agent positions
- first keyed object entities from `ObjectUpdate`
- known `prim_basic` and `avatar_basic` `ObjectUpdate` variants
- first conservative texture UUID extraction from rich prim `TextureEntry`
- structural `ImprovedTerseObjectUpdate` parsing with per-entry payload and texture-entry sizing

## Current Gaps

The next meaningful work is not transport stabilization. It is coverage and interpretation.

Main gaps:

- multi-object `ObjectUpdate` semantic decoding
- better census of all visible scene objects
- semantic decoding of terse object payloads beyond the first inferred `local_id`
- deeper object update families such as `ObjectUpdateCached` and `KillObject`
- full `TextureEntry` decoding
- `ExtraParams` and related rich-tail fields
- reliable extraction of ordinary prim names
- clearer mapping of raw flag fields like `update_flags`

## Current Evidence Workflow

Use this loop for reverse-engineering work:

1. start OpenSim with `./run.sh opensim`
2. run a live session with `./run.sh session 180 --verbose`
3. narrate manipulations in local chat when useful
4. inspect `./run.sh unknowns`
5. optionally enable fixture capture and rebuild with `./run.sh fixtures`
6. update `docs/reverse-engineered-protocol.md` when a field becomes trustworthy

The current evidence workflow is session-aware:

- by default `unknowns-report` targets the latest recorded session
- use `./run.sh unknowns -- --all` to aggregate across the whole DB
- use `./run.sh unknowns -- --session-id N` when comparing two specific live runs

Important note:

- `local/unknowns.sqlite3` is now intended to accumulate session evidence for later forensic comparison
- prefer preserving old sessions and using session-aware reporting instead of clearing the DB between runs
- if the DB has been polluted with test or synthetic data, move it aside and start a fresh file rather than deleting useful historical evidence

## Canonical Docs

Read these first when resuming work:

1. `README.md`
2. `docs/README.md`
3. `docs/current-handoff.md`
4. `docs/reverse-engineered-protocol.md`
5. `docs/local-opensim.md`
6. `AGENTS.md`

Historical planning and dated progress notes live under `docs/archive/`.

## Multi-Agent Collaboration

The repo is now explicitly set up for multi-agent use.

Expectations:

- treat the repo as a shared workspace
- leave notes for the next agent in `docs/current-handoff.md`
- update `docs/reverse-engineered-protocol.md` when protocol understanding changes
- avoid relying on tool-specific hidden context

This should work cleanly across Codex, Claude Code, Antigravity, or any similar agentic tool.

## Recommended Next Step

Run one live session against the local OpenSim target. The cloud avatar blocker is now wired:

1. Start OpenSim: `./run.sh opensim`
2. Run a session: `./tools/run_session_forensics.sh 90`
3. Inspect session output for:
   - `bake.uploaded blob=N asset=<uuid>` × 5 — confirms uploads accepted
   - `bake.override_ready serial=5 bakes=5` — confirms override is set
   - `appearance.baked_override ...` — confirms it was used in `AgentSetAppearance`
4. Check in-world whether the avatar is no longer cloud-like

If the avatar still appears cloud after uploads succeed, the next target is:
- Whether OpenSim requires a second login cycle to see its own newly-uploaded bake assets
- Whether `AgentCachedTextureResponse` returned non-zero IDs (check `appearance[cached_textures]`)

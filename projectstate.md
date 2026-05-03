# Project State

Last updated: 2026-05-03

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
- pygame-based 2D bird's-eye viewer consuming live `WorldView` state and the
  cached region map tile
- viewer menu/status shell with movement help, chat, local teleport-location requests,
  and a read-only inventory snapshot sourced from `FetchInventoryDescendents2` /
  `FetchInventory2`

## What Is Stable

- `./run.sh opensim`
- `./run.sh bootstrap`
- `./run.sh caps`
- `./run.sh eventq`
- `./run.sh udp`
- `./run.sh handshake`
- `./run.sh session`
- `./run.sh session 180 --verbose`
- `./run.sh viewer`
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
- `src/vibestorm/viewer/`: pygame 2D viewer, camera, scene aggregation, UI shell,
  input, rendering
- `src/vibestorm/fixtures/`: fixture inventory and SQLite unknowns database
- `docs/viewer-help.md`: in-app movement/menu help loaded by the pygame viewer

Current object/world coverage:

- region handshake and region metadata
- sim stats
- simulator time
- coarse agent positions
- first keyed object entities from `ObjectUpdate`
- known `prim_basic` and `avatar_basic` `ObjectUpdate` variants
- first conservative texture UUID extraction from rich prim `TextureEntry`
- structural `ImprovedTerseObjectUpdate` parsing with per-entry payload and texture-entry sizing
- multi-object `ObjectUpdate` semantic decoding and fixed-tail advancement

## Current Gaps

The next meaningful work is not transport stabilization. It is coverage and interpretation.

Main gaps:

- better census of all visible scene objects
- semantic decoding of terse object payloads beyond the first inferred `local_id`
- deeper object update families such as `ObjectUpdateCached` and `KillObject`
- full `TextureEntry` decoding
- `ExtraParams` and related rich-tail fields
- reliable extraction of ordinary prim names
- clearer mapping of raw flag fields like `update_flags`
- parcel name/status is still a placeholder until `ParcelOverlay` and parcel metadata
  are decoded
- inventory is currently read-only; asset create/upload/store management is not
  implemented yet beyond existing appearance/baked-texture upload support

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

Run the 2D viewer against the local OpenSim target and do a visual/interaction pass:

1. Start OpenSim: `./run.sh opensim`
2. Run the viewer: `./run.sh viewer`
3. Check that the cached map tile appears, object/avatar markers update, WASD/arrows move the agent,
   mouse wheel/right-drag camera controls feel usable, the main menu/status bar scale correctly,
   the resizable chat window sends local chat, Help opens movement instructions, View -> Inventory
   shows the fetched snapshot, and Tools -> Teleport sends a local `TeleportLocationRequest`.
4. If rendering is visually cramped or misleading, tune marker sizing/colors in
   `src/vibestorm/viewer/render.py` and `src/vibestorm/viewer/scene.py`.

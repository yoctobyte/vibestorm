# Project State

Last updated: 2026-05-09

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
- viewer menu/status shell with movement help, chat, local teleport-location
  requests, and a first inventory-manager window sourced from the current
  `FetchInventoryDescendents2` / `FetchInventory2` snapshot
- viewer3d Object Inspector with read-only object details plus first-pass
  read-only object/task inventory loading through `RequestTaskInventory` and
  xfer assembly
- viewer3d terrain path for standard 16x16 land `LayerData`: bitstream decode,
  dequantization + IDCT, 256x256 heightmap accumulation, and textured GL
  heightfield rendering
- terrain `BitPack` now matches OpenMetaverse's live wire order (`0801104c`
  for stride 264 / patch size 16 / land type, `88d0` for a non-byte-aligned
  10-bit magnitude after a 2-bit prefix), fixing the uniform-gray and distorted
  live heightmap failure modes seen in Sim Debug
- viewer3d starts in 3D mode by default, caps rendering at 20 FPS by default,
  and includes in-app Diagnostics / Sim Debug / Render Settings windows for
  terrain, water, object, and mesh-line debugging
- viewer3d has first-pass ambient + sun-direction lighting for primitives and
  filled terrain surfaces
- viewer3d has first-pass texturing: cached region map tiles can be draped
  over terrain heightfields, and object `default_texture_id` assets are fetched
  through `GetTexture`, cached as PNG, and bound to primitive draw groups with
  coarse generated UVs
- the first `TextureEntry` image section is decoded and retained through the
  world/viewer3d scene models: default texture UUID plus per-face image UUID
  overrides
- cube primitives render parsed per-face texture UUID overrides; non-cube
  primitives still use the default-texture fallback until their face mapping is
  modeled
- first-pass object inventory asset viewing: `TransferRequest` implemented for
  retrieving object task-inventory assets; the latest fix matches OpenSim's
  `SimInventoryItem` params layout with the requested asset UUID at offset 80.
  When OpenSim intentionally withholds a task inventory asset UUID, the viewer
  now reports that instead of sending an impossible transfer request.


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
- `src/vibestorm/viewer3d/`: forked pygame/moderngl viewer with selectable 2D/3D
  render modes, primitive shape meshes, water plane, decoded terrain surface
  rendering, first-pass directional lighting, first-pass terrain/object
  texturing, and a snapshot-backed inventory manager UI
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
- first-pass `TextureEntry` image UUID-section decode
- structural `ImprovedTerseObjectUpdate` parsing with per-entry payload and texture-entry sizing
- multi-object `ObjectUpdate` semantic decoding and fixed-tail advancement
- standard land `LayerData` terrain patch decompression and accumulation

## Current Gaps

The next meaningful work is not transport stabilization. It is coverage and interpretation.

Main gaps:

- better census of all visible scene objects
- semantic decoding of terse object payloads beyond the first inferred `local_id`
- deeper object update families such as `ObjectUpdateCached` and `KillObject`
- full `TextureEntry` material decoding and renderer use of per-face overrides
- `ExtraParams` and related rich-tail fields
- reliable extraction of ordinary prim names
- clearer mapping of raw flag fields like `update_flags`
- parcel name/status is still a placeholder until `ParcelOverlay` and parcel metadata
  are decoded
- extended-region 32x32 terrain patches are not decompressed yet
- inventory is currently read-only; user-inventory folders can be opened
  lazily through `FetchInventoryDescendents2`, and object/task inventory can
  be listed through `RequestTaskInventory` + xfer; successful empty folder and
  object replies are represented as loaded-empty, but asset open/save,
  create/upload/store management, multi-save, and multi-upload-from-file are
  not implemented yet beyond existing appearance/baked-texture upload support

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

Run the 3D viewer against the local OpenSim target and inspect first-pass
texturing:

1. Start OpenSim: `./run.sh opensim`
2. Run the viewer: `./run.sh viewer3d`
3. Use View -> Render Settings to isolate terrain, mesh lines, water, and
   objects while checking whether the map tile drapes correctly over terrain
   and whether default prim textures appear as their assets arrive.
4. Continue Task Inventory asset viewing work: test with an object/item whose
   inventory xfer contains a non-zero `asset_id`, then finalize the text/script
   viewer UI and add save-to-disk.
5. Continue texturing with full `TextureEntry` per-face decode, authored mesh
   UVs/normals, and simulator terrain material parameters instead of the
   temporary region-map drape.

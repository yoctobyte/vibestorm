# Project State

Last updated: 2026-08-14

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
- viewer3d has explicit camera presets: `F1` sim-wide orbit, `F2`
  third-person behind-avatar, and `F3` avatar eye view. The avatar presets
  track the current avatar transform when updates arrive.
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
- viewer3d now decodes sculpt/mesh identity from the sculpt `ExtraParams`
  block (`type=0x30`, `UUID + sculpt_type`) into renderer-facing scene
  metadata. Sculpt objects now fetch their sculpt texture through `GetTexture`,
  reuse the existing texture PNG cache, build a first-pass RGB displacement
  mesh with basic seam wrapping for sphere/torus/cylinder/plane sculpt types,
  preserve/apply documented mirror and invert flags, and upload that geometry
  into the existing instanced renderer. SL mesh
  objects resolve their mesh asset UUID through the `GetMesh` CAP,
  cache raw `.llmesh` assets under `local/mesh-cache/`, decode high-LOD
  Position/TriangleList geometry, and upload that geometry into the existing
  instanced mesh renderer. If fetch or decode fails, sculpt/mesh objects keep
  using their primitive placeholders.
- avatars render with a dedicated human-like placeholder mesh facing local
  +X, so the existing ObjectUpdate quaternion visibly controls avatar facing
  instead of stretching a cube.
- first-pass object inventory asset viewing: `TransferRequest` implemented for
  retrieving object task-inventory assets; the latest fix matches OpenSim's
  `SimInventoryItem` params layout with the requested asset UUID at offset 80.
  When OpenSim intentionally withholds a task inventory asset UUID, the viewer
  now reports that instead of sending an impossible transfer request.
- first user-inventory upload smoke path: `NewFileAgentInventory` capability
  prelude plus one-shot raw-byte upload, exposed as `./run.sh upload-smoke`.
  The command creates `local/upload-smoke/empty-space.txt` by writing an empty
  file and appending one space, uploads it as a notecard/text item, then uses
  `FetchInventory2` to confirm the returned `new_inventory_item` points at the
  returned `new_asset`. Live OpenSim verification succeeded with a one-byte
  upload returning asset `8a3bc672-4a0e-4542-80dc-0973d63fd5e2` and inventory
  item `77798038-e03a-4dd5-8704-031203269a63`.
- viewer3d Object Inspector now has first-pass file actions: save the selected
  object asset to a chosen path, bulk-save all visible text/script object
  assets to a chosen folder, and upload one `.lsl`, `.txt`, or `.nc` file or
  all matching files from a chosen folder into the user's inventory root
  through `NewFileAgentInventory`.
- full `TextureEntry` multi-section decode (default value first, then per-face
  overrides) across all sections, not just the image UUID section
- SL mesh assets decode normals, `TexCoord0` UVs, and per-submesh
  `material_groups` mapping prim face index to its index-buffer slice — and all
  three now reach the GPU: `in_normal` lighting, `in_mesh_uv` texture sampling,
  and per-face texture binding from the material groups
- **parcel identity end to end** (2026-08-14): `ParcelPropertiesRequest` is
  autosent region-wide after `RegionHandshake`, the reply arrives over the
  event queue, and `viewer3d`'s HUD shows the real parcel name instead of
  `Parcel: unknown`. The region ownership grid is reassembled from the
  sequenced `ParcelOverlay` packets and its property lines draw as plot edges
  in both the 2D top-down view and the 3D view (terrain-following). Live-verified
  against local OpenSim.
- **background EventQueueGet polling**: the queue is polled in a loop for the
  life of the session, acking each batch, instead of once in the caps prelude.
  This is what makes every EQG-only message reachable at all.
- parcel decoding: `ParcelProperties` `ParcelData` block through `GroupID`
  (ownership, AABB, Bitmap, area, prim counts, flags, sale price, name/desc/
  music/media URLs), the `ParcelOverlay` packed bit-field reassembled into a
  region-wide 4 m LandUnit ownership grid with property-line segments, and the
  per-parcel Bitmap cell mask. Cell ordering and bit order mirror OpenSim
  `LandChannel.cs` / `LandManagementModule.cs` / `LandObject.cs`.
- typed `EventQueueGet` event decoding, wired into the live poll loop:
  `ParcelProperties` (EQG-only on OpenSim — there is no UDP send path),
  `EnableSimulator`,
  `EstablishAgentCommunication`, `TeleportFinish`, `CrossedRegion`,
  `ScriptRunningReply`, `ObjectPhysicsProperties`, `AgentGroupDataUpdate`, plus
  `UnknownEvent` fallback. LLSD parsing now handles binary tags, so OpenSim's
  big-endian uint/ulong blobs and 4-byte IPs coerce back to ints / dotted quads.
- `ExtraParams` prim feature blocks decode beyond sculpt/mesh: flexi, light,
  projector, reflection probe, render materials and mesh flags, surfaced
  per-object in the viewer3d Object Inspector
- terrain decoding is patch-size agnostic: standard 16x16 Land patches and
  32x32 LandExtended (varregion) patches, with the heightmap growing to fit a
  region larger than 256 m
- animation and sound message decoding: `AvatarAnimation`, `ObjectAnimation`,
  `SoundTrigger`, `AttachedSound`, `AttachedSoundGainChange`, `PreloadSound` —
  all dispatched from the live session and republished as typed bus events

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
- `./run.sh upload-smoke`
- `./local.sh ...`, `./opengrid.sh ...`, and `./sl.sh ...` wrap
  `run.sh` with separate default profiles and grid-safety modes.
- `./run.sh unknowns`
- `./run.sh fixtures`

The local OpenSim target is the current source of truth for live protocol experimentation.
Use `./local.sh` for local test accounts, `./opengrid.sh` for OSgrid/OpenGrid
accounts, and `./sl.sh` for Second Life accounts so credentials and safety
defaults do not get mixed accidentally.

## Current Technical Shape

Main implemented areas:

- `src/vibestorm/login/`: login/bootstrap
- `src/vibestorm/caps/`: seed capability resolution and LLSD support
- `src/vibestorm/event_queue/`: `EventQueueGet` polling (`client.py`) and typed
  event decoding (`events.py`, not yet wired to the poll loop)
- `src/vibestorm/udp/`: packet parsing, template dispatch, semantic message helpers, session loop
- `src/vibestorm/world/`: normalized world-state models and updater, plus
  `parcel_overlay.py` (region ownership grid + per-parcel Bitmap decode)
- `src/vibestorm/bus/`: typed event bus bridging session events to consumers
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

The next meaningful work is not transport stabilization. It is coverage and
interpretation.

**What is actually open**, grouped by what unblocks it. Everything after this
is a record of work that has landed — that mixture is deliberate history, but
read the four lists below first, because a finished item written in the past
tense reads like an open one.

*Blocked on region content* (the decoder exists and is unit-tested; the sim
never sends the message, so `./run.sh census` reports it under `absent=`):
attached sound, texture animation, media URL, light / projector / reflection
probe, a worn attachment, a prim with two different face textures, a
non-default material or click action, a working sculpt map, a real mesh asset,
a 32x32 LandExtended varregion, and a second avatar for typing notifications.

The grid library is a second source of content, and it is not empty: `./run.sh
inventory-walk --library` reads 19 folders and 123 items on a stock OpenSim —
64 textures, 17 scripts, 16 gestures, 12 animations, 7 settings, 4 body parts,
2 clothing, 1 notecard. Real assets of eight types have been fetched from it
through `ViewerAsset` and kept in `test/fixtures/library/`; five of the eight
now have decoders driven by those bytes. It holds no sounds and no objects, so
those two remain genuinely blocked on someone building something.

Two entries left this list by turning out not to be blocked at all. Chat did
not need an in-world speaker — the client can say something and read the
simulator's echo. IM did not need a second avatar — OpenSim routes on
`ToAgentID` without checking whether it is the sender's own, so an IM to
ourselves comes back through the normal inbound path. **Before adding
anything to this list, check whether the client can produce the traffic
itself.** Twice the blocker was a missing outbound message rather than a
missing object.

*Blocked on consent* (needs an in-world edit in the user's region): the
object-sync CAP verify. `ObjectPhysicsProperties` used to be on this list —
the sim sends that message only as an echo of an edit the viewer itself made —
but the *data* needs no edit at all: `./run.sh census --physics` pulls it for
every prim through `GetObjectPhysicsData`. What remains unverified is the UDP
message, not the physics.

*Blocked on sources* (absent from `opensim-source/`, and guessing is worse
than leaving them raw): `PrimFlags` for `update_flags`, the particle system
block, `ChatSourceType` / `ChatAudibleLevel`, and the region flag bits LSL does
not expose — all libomv tables. Also **visual parameter ids**: a wearable file
carries them (`781 .78`), and `AvatarAppearance.VPElement` is *not* their
table — it is a 0-based index into the `AgentSetAppearance` array, generated
from libomv's list. `assets/wearable.py` therefore reports parameters as raw
`{id: value}`.

Three formats have left this list, and the third is the reason to distrust the
list itself:

- **Animation assets.** OpenSim reads them in `BinBVHAnimation.cs`, so
  `assets/animation.py` is fully sourced and verified against all twelve
  library animations.
- **Gesture assets**, and **wearable assets** (clothing and body parts). Both
  were listed here as "OpenSim stores them as opaque bytes and never parses
  them". That was wrong, and it was wrong because of *how it was checked*: a
  grep for the format's own name (`LLWearable`) and for the asset type found
  nothing, and absence of a parser named after the format was read as absence
  of a parser. Both are in fact walked, line by line and with every field
  commented, by `UuidGatherer.RecordGestureAssetUuids` and
  `RecordWearableAssetUuids` — code whose *purpose* is collecting referenced
  asset ids, so it is named after neither format.

**When a format looks unsourced, search for what the tree would do with it,
not for what it is called.** A parser can live inside a feature that only
incidentally needs to read it. Both decoders are now written, sourced and
verified against real library assets.

*Genuinely unimplemented, not blocked*: the region-crossing transport half
(`EnableSimulator` -> child circuit, `CrossedRegion` -> promote child,
`TeleportFinish` over the event queue), and the inventory *write* surface
described at the end of this list. Recursive inventory *reading* is done —
`./run.sh inventory-walk` walks the whole tree breadth-first and reports
truncation explicitly.

Both coverage ledgers now re-derive themselves from the code
(`test/test_message_coverage_ledger.py`, `test/test_capability_coverage_ledger.py`),
so the drift that made these lists untrustworthy twice cannot recur silently.
The one thing they still cannot check is `tested` versus `verified`: that is a
claim about live evidence, and asserting it offline would be the overclaim the
distinction exists to prevent.

Landed work follows:

- `./run.sh census` now reports region content: shapes (with the raw curves
  behind any unclassified prim), names, sculpt/mesh kinds with asset ids,
  per-feature counts with example local_ids, permission masks, and — the useful
  part — an explicit `absent=` list of features the region cannot exercise
- the sculpt render path is blocked on content, not code: the test region's
  three sculpts all reference asset `be293869-d0d9-0a69-5989-ad27f1946fd4`,
  which GetTexture returns 404 for. No mesh (type 5) object exists at all, so
  GetMesh has never run against live data
- prim shape now survives `ObjectUpdateCompressed` (it was skipped, leaving
  nearly every prim in a populated region rendering as the fallback cube).
  Hover text, its colour, the media URL, the attached-sound block and the
  TextureAnim block decode in both update paths and reach the inspector; hover text also renders as a 3D
  billboard. The particle system blocks are still stepped over
- `SimStats` values are named and surfaced (`world[sim_health]`) instead of
  being decoded and thrown away as a bare count. Ids come from OpenSim's
  `StatsID`, which is *not* the `StatsIndex` enum sitting beside it in the same
  file — the two agree for 0-3 and diverge after, so mis-keying yields correct
  labels on the wrong numbers. All 41 ids the test region sends resolve
- chat carries a named type (whisper/say/shout/region/owner/direct) from
  OpenSim's `ChatTypeEnum`; start/stop-typing are recognised as notifications
  rather than appended to the log as blank lines. `sourcetype` and `audible`
  stay raw — those two are libomv enums, absent from `opensim-source/`
- a prim's physics material and shape type (`ObjectPhysicsProperties`) reach
  the inspector, named from OpenSim's `PhysShapeType`. Not live-verified: the
  sim only sends this as an echo of an edit the viewer itself made, so it needs
  the same in-world-edit consent as the object-sync verify
- prim material and click action are named from OpenSim's LSL constants and
  counted by the census. Live run resolves every value with no unknowns, but
  all 32 prims are wood/touch — the defaults — so the non-default names are
  unexercised until the region holds a prim that uses one
- parcel and region flag words are decoded from the sourced LSL constant
  subset, with unnamed bits reported rather than dropped. Parcel decoding is
  live-exercised (5 of 16 names on a real reply); region decoding resolves 1 of
  9, the rest being bits OpenSim sets but LSL never exposes
- a prim's `state` byte is decoded: for an attachment it is the attachment
  point with nibbles swapped, named from the LSL `ATTACH_*` constants, with
  HUD slots flagged. `AttachItemID` in NameValues is what identifies an
  attachment; the state byte alone cannot, since a tree also has one. No
  attachments exist in the test region, so this is unit-tested only
- the sound flags byte is named from OpenSim's `SoundFlags` enum (SoundModule),
  not LSL's `SOUND_*` constants, which are llLinkPlaySound parameters and
  disagree on every value above 1. A STOP flag now counts as silence, since a
  stop message still names the sound it is stopping. No sound-emitting prim
  exists in the test region, so this is unit-tested only
- `EnableSimulator` and `CrossedRegion` finally have a consumer: neighbouring
  regions are tracked as scene state and shown as a count, a crossing posts an
  alert. Not live-verified (the test sim is standalone), but the events are
  constructible so the scene handling was never blocked on that. The transport
  half — child circuits and promotion on crossing — is still unimplemented
- `ObjectUpdateCached` is fully handled, not a gap: every cached entry triggers
  `RequestMultipleObjects`, and that path is what populates most of the region.
  Measured live — the sim sends it twice, at region entry rather than
  periodically, and 33 objects end up tracked from only 13 full-update events.
  `KillObject` removes objects from the `WorldView`; it has no live exercise
  because nothing in the test region is ever deleted
- every built-in primitive authors real normals (flat faces for boxes and the
  prism, split cap/side rings for the cylinder, analytic tube-centre normals for
  swept surfaces) instead of falling back to normalize(position); sculpts derive
  theirs from the decoded geometry via `sl_mesh.smooth_vertex_normals`
- tube and ring have purpose-built swept meshes rather than aliasing to cube
  and torus; flexible prims (path curve 0x80) classify by their profile instead
  of falling through to the unclassified fallback
- per-face `TextureEntry` overrides now follow SL's face numbering for box,
  cylinder and prism, and single-face prims (sphere, torus, sculpt) read the
  face 0 override. The test region has no prim with different textures on two
  faces, so this is pixel-verified offscreen but not live-verified; the prism
  side-quad order in particular is derived rather than observed
- prim and avatar names both resolve: 32 of 33 live objects carry an
  ObjectPropertiesFamily name, and the one that does not is the avatar, whose
  name comes from ObjectUpdate NameValues and renders as a name tag
- permission masks now decode into named rights from OpenSim's PermissionMask;
  `update_flags` is still raw hex, because libomv's `PrimFlags` table is not in
  `opensim-source/` and guessing it is worse than leaving it
- typed EQG events publish as `EventQueueEventReceived`; `viewer3d` reports
  `TeleportFinish` and `ScriptRunningReply` in chat. Every decoded bus event
  now has a consumer
- `udp.messages.parse_parcel_properties` is unreachable against OpenSim, which
  sends `ParcelProperties` only over the event queue. Kept for other servers.
- `ObjectAnimation` and the four sound messages now have consumers: the Scene
  tracks current animations and attached sounds per object and the inspector
  shows them. Still synthetic-test-only — a quiet single-avatar test region
  produces none of them
- the 32x32 LandExtended terrain path and the light/projector/reflection-probe
  `ExtraParams` decoders exist but have never seen live data — the test region
  is a standard 256 m sim with none of that content
- inventory is no longer purely read-only, but write support is still narrow:
  user-inventory folders can be opened
  lazily through `FetchInventoryDescendents2`, and object/task inventory can
  be listed through `RequestTaskInventory` + xfer; successful empty folder and
  object replies are represented as loaded-empty. First-pass object asset
  viewing, save-to-disk, bulk text/script save, and user-inventory file upload
  exist, but object-task-inventory upload/sync, create/store management,
  deletes, conflict handling, and recursive folder sync are not implemented yet
  beyond existing appearance/baked-texture upload support.

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

The standing theme: several decoders are complete and tested but have no
consumer. Consume and live-verify rather than decode more.

Parcel identity and parcel geometry (2D and 3D) work end to end (2026-08-14).

Note for anything marked "needs the GL viewer":
`moderngl.create_standalone_context()` works on this machine, so GL changes can
be verified headlessly by rendering to an offscreen framebuffer and reading
pixels back — see `ParcelBorderGLTests` in `test/test_viewer3d_perspective_gl.py`.
That route closed the deferred mesh-normals and per-face material items.

Two independent tracks remain open, either of which can follow:

- Live-verify the object-local script/notecard sync path end to end (implemented
  2026-05-25, never confirmed against a running sim). This is now the oldest
  open track and everything it needed exists: `ScriptRunningReply` arrives over
  the live event queue and surfaces as a viewer chat line.
- Confirm the decoders that have never seen live data by adding the content:
  rez a light / projector / reflection-probe prim, and stand up a varregion for
  the 32x32 terrain path.

Deleting object inventory, creating missing rows, conflict resolution, and
recursive folder sync remain out of scope until the update path is proven live.

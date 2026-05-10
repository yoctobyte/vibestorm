# Current Handoff

Last updated: 2026-05-09

## Update 2026-05-09: First Inventory Manager UI

The next viewer track has started with user inventory before object
inspection/object inventory.

- `viewer3d` View -> Inventory now opens an "Inventory Manager" window instead
  of a flat text dump.
- The window is still read-only and uses the existing login/prelude
  `InventorySnapshotReady` data from `FetchInventoryDescendents2` /
  `FetchInventory2`.
- The left pane is a selection list with loaded folders, child folder entries,
  items, and resolved Current Outfit links. Child folders whose contents are
  listed but not fetched are marked `(not loaded)` / `F*`.
- Follow-up polish changed the left pane to a more traditional tree-like row
  format: folder rows are left-indented with `▾` / `▸`, loaded/unloaded folder
  glyphs (`◼` / `◻`), ordinary item bullets (`•`), and link arrows (`↗`).
  This is still backed by `pygame_gui.UISelectionList`, not a true native tree
  widget.
- Folder opening is now wired for user inventory. Selecting an unloaded child
  folder and pressing Open, or double-clicking it, calls
  `FetchInventoryDescendents2` for that folder, merges the returned
  `InventoryFetchSnapshot` into the existing snapshot, and republishes
  `InventorySnapshotReady` through the existing session event bridge.
- The right pane shows details for the selected folder/item: IDs, parent/owner
  fields, type/inventory type, flags, description, link status, and load state.
- Added `inventory_snapshot_rows()` as a pure row-model helper with tests, plus
  HUD selection/details/open tests.

Verification:

- `uv run ruff check src/vibestorm/viewer3d/hud.py test/test_viewer3d_hud_render_mode.py`
- `uv run --extra viewer pytest test/test_viewer3d_hud_render_mode.py -q`
- `uv run --extra viewer pytest test/test_inventory_caps_client.py test/test_viewer3d_hud_render_mode.py -q`

Known remaining inventory/tooling work:

- object inspector UI is now implemented with a read-only list of nearby objects and property joining.
- object inventory tree is protocol work after that: request/load inventory for selected nearby objects, then build editor/open/save/upload commands on top.

### Next Steps for Object Inventory

Goal:

- The View/Tools menu now has an "Object Inspector" window.
- The window provides a split-pane interface. The left pane shows nearby objects from `Scene.object_entities`, sorted by distance from `Scene.avatar_position` when available.
- The right pane shows grouped details joined from `SceneEntity` and `WorldView.objects` using `local_id_to_full_id`:
  - Identity: local ID, full UUID, display name
  - Transform: position, rotation, scale
  - Shape/render: pcode, kind, primitive shape, material, click action, default texture UUID, and per-face texture UUIDs
  - Object update/debug: variant, update flags, CRC, and data sizes
  - Properties: owner/group/permissions, name, description, sale fields from `ObjectPropertiesFamily`
- The bottom right pane contains an "Object Inventory" placeholder ("not requested yet").
- Tests were added in `test/test_viewer3d_object_inspector.py`.

Verification:

- `uv run ruff check src/vibestorm/viewer3d/hud.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_viewer3d_object_inspector.py -q`

Object inventory notes:

- Object inventory loading is now wired read-only for the inspector.
- The inspector bottom pane has a "Load Inventory" button. It dispatches
  `RequestObjectInventory(local_id)`, which queues `RequestTaskInventory`.
- The simulator's `ReplyTaskInventory` supplies a task UUID, serial, and xfer
  filename. Vibestorm now sends `RequestXfer`, confirms `SendXferPacket`
  packets, assembles the xfer payload, parses common `inv_item` blocks, and
  publishes `ObjectInventorySnapshotReady`.
- Empty `ReplyTaskInventory` filenames are treated as successful empty object
  inventory loads. This avoids leaving the inspector stuck in "request sent"
  when an object simply has no contents.
- Viewer3D prints object-inventory debug events to stdout while running:
  `task_inventory.request`, `reply`, `xfer.request`, `xfer.packet`,
  `xfer.confirm`, `xfer.unknown`, and `ready`. Use these lines to distinguish
  a missing simulator xfer from an xfer ID/packet parsing problem.
- `Scene.object_inventory_snapshots[local_id]` stores loaded object inventory,
  and the inspector displays item name/type/UUID in the bottom pane.
- Lazy user-inventory folder loads also materialize a successful empty response
  as a loaded folder with zero descendants, so the tree can distinguish
  "empty" from "not loaded yet".
- The parser is intentionally partial and read-only. It does not yet implement
  item open, asset download, save, upload, move, delete, script running state,
  or permission editing.

Verification:

- `uv run ruff check --select F,I src/vibestorm/world/object_inventory.py src/vibestorm/udp/messages.py src/vibestorm/udp/session.py src/vibestorm/udp/world_client.py src/vibestorm/bus/commands.py src/vibestorm/bus/events.py src/vibestorm/viewer3d/app.py src/vibestorm/viewer3d/hud.py src/vibestorm/viewer3d/scene.py test/test_object_inventory.py test/test_udp_messages.py test/test_world_client.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_object_inventory.py test/test_udp_messages.py test/test_world_client.py test/test_viewer3d_object_inspector.py -q`
- `uv run --extra viewer pytest`

## Update 2026-05-06: Terrain Heightmap + Surface Mesh

Viewer3D terrain work has moved past raw patch extraction.

- `src/vibestorm/world/terrain.py` now decodes standard 16x16 land
  `LayerData` patches all the way to height samples: libomv-compatible
  dequant table, copy-matrix reorder, two-pass IDCT, and final
  `mult/addval` height arithmetic.
- Important correction: the coefficient stream decoder now matches
  libopenmetaverse's real bit codes (`0` zero, `10` EOB, `110` positive,
  `111` negative). The earlier synthetic tests had encoded a different
  symmetric shape, so they were corrected at the same time.
- `RegionHeightmap` accumulates decoded land patches into a 256x256
  row-major sample array and tracks a `revision` for render-cache rebuilds.
- `viewer3d.Scene` consumes `LayerDataReceived` bus events, ignores non-land
  layers and other regions, and keeps the current `terrain_heightmap`.
- `PerspectiveRenderer` now builds a textured terrain heightfield mesh from
  the scene heightmap and draws it through the existing ground shader. It
  falls back to the flat region ground quad until terrain packets arrive.

Verification:

- `uv run pytest test/test_world_terrain.py test/test_viewer3d_scene.py -q`
- `uv run pytest test/test_viewer3d_perspective_gl.py -q`

Known remaining terrain gaps:

- Extended 32x32 patches are still rejected; only standard 16x16 land
  patches are decompressed.
- Wind/cloud layer data is surfaced but not rendered.
- No live OpenSim visual pass was run in this handoff; next concrete step is
  `./run.sh opensim` plus `./run.sh viewer3d`, then switch to 3D and confirm
  the ground surface is visibly elevated instead of flat.

Follow-up from the first live check:

- `viewer3d` now starts in 3D mode by default (`--render-mode 2d-map` is
  available for the old startup behavior).
- The viewer loop is capped at 20 FPS by default via `--max-fps 20`; pass
  `--max-fps 0` to disable the cap.
- The perspective renderer now draws terrain with a 1x1 fallback ground
  texture if terrain height data exists before the region map tile has loaded.
  This fixes the "no map tile means no terrain draw" path.
- A Diagnostics window is visible by default in 3D mode and available from
  Debug -> Diagnostics. It reports FPS, mode, region/map path, terrain
  dimensions/patch count/revision/min/max height, water level plus avatar
  under/above-water status, object/avatar/texture/chat counts.

Second follow-up from live debugging:

- The water plane now uses the parsed `RegionHandshake.WaterHeight` stored in
  `WorldView.region.water_height` instead of always using the default 20 m.
  The diagnostics window reports that same scene value.
- Basic 3D orbit inspection controls are wired:
  - right-drag rotates the orbit camera
  - mouse wheel changes orbit distance
  - Shift+right-drag pans the orbit target
  - Shift+PageUp/PageDown lifts/lowers the orbit target
  - Center/C now retargets orbit mode to the avatar/coarse self position

Third follow-up from live debugging:

- The water shader now applies subtle coordinate-based noise so the water
  plane is visually readable instead of a flat translucent sheet.
- Terrain heightfields now draw a bright green wire/grid overlay on top of the
  filled terrain mesh. This is intentionally texture-independent, so it should
  confirm whether `LayerData` has produced a mesh even when the map/terrain
  texture is missing or visually ambiguous.

Fourth follow-up for terrain diagnosis:

- `viewer3d` now accepts `--debug-terrain synthetic`. This seeds
  `Scene.terrain_heightmap` with a deterministic hill/valley/ripple surface
  and ignores live land `LayerData` while the override is active.
- Diagnostics now show terrain source (`live`/`synthetic`), min/max/mean,
  first patch keys, and the first four sample values. This should make it
  clear whether we are failing before GL (bad/flat decoded samples) or in GL
  (synthetic terrain also fails to draw).
- Follow-up after synthetic showed only grid lines: terrain fill is now a
  solid untextured green material whenever a heightmap exists. The textured
  ground path is left for the no-heightmap flat floor and future texture work.
- Follow-up after synthetic still looked flat: rendered terrain gained
  height-based color grading and a `--terrain-z-scale` option. It temporarily
  defaulted to `4.0` while geometry was suspect; later live validation moved
  the default back to real meter scale.
- Follow-up after synthetic still looked flat again: `Scene.apply_region_changed`
  was clearing the synthetic debug heightmap during the initial live
  `RegionChanged` event. Synthetic terrain now survives region changes, while
  normal live terrain is still cleared on region change.
- To diagnose live flat terrain, `RegionHeightmap.latest_layer_stats` now records
  the latest land LayerData packet's patch positions, ranges, DC offsets,
  prequant values, nonzero coefficient count, max absolute coefficient, and
  decoded per-packet height min/max/mean. Diagnostics shows these as `layer:`
  and `coeff:` lines.

Fifth follow-up for live flat terrain:

- Debug -> Sim Debug opens a "Sim Debug Heightmap" window showing the current
  `Scene.terrain_heightmap.samples` as a black/white normalized image. This is
  intentionally independent from the 3D mesh/material path; if live terrain is
  still flat in-world but the image has contrast, the bug is in mesh upload or
  render scaling. If the image is uniform gray, the decoded server heightmap is
  actually flat at the sample-array level.
- The heightmap window status line reports source (`live`/`synthetic`),
  dimensions, patch count, and min/max height for quick screenshots/logging.
- Focused verification: `uv run --extra viewer pytest
  test/test_viewer3d_hud_render_mode.py`.

Sixth follow-up after Sim Debug showed uniform gray live terrain:

- The root cause was the terrain `BitPack` bit order. OpenMetaverse writes
  integer fields as little-endian byte chunks while retaining MSB-first
  ordering inside each chunk. Live OpenSim LayerData starts with `08 01 10 4c`
  for stride 264, patch size 16, land type 0x4c; the previous reader treated
  the whole stream as MSB-first.
- `BitPack` and `BitPackWriter` now match OpenMetaverse chunk order. Tests pin
  the live header prefix (`0801104c`) and coefficient prefix-code bytes
  (`10 -> 80`, `110 -> c0`, `111 -> e0`).
- Saved LayerData previews now decode to plausible headers such as stride 264,
  patch size 16, land type 0x4c, ranges 1/3, and valid patch coordinates.

Seventh follow-up after live terrain had plausible heights but wrong shape:

- The first `BitPack` correction still mishandled non-byte-aligned multi-byte
  values. OpenMetaverse continues a split input byte across output-byte
  boundaries; after `PackBits(2, 2)`, `PackBits(0x123, 10)` must produce
  `88 d0`. The Python reader/writer now pins and matches that behavior.
- `END_OF_PATCHES` is decimal `97` (`0x61`), not hex `0x97`; the old constant
  came from misreading the name/comment. Tests now pin the marker byte.
- Added an OpenSim-generated sloped-patch fixture using
  `OpenSimTerrainCompressor.CreatePatchFromTerrainData`; Python decode
  recovers `height = 20 + x * 0.05 + y * 0.02` within about 0.01 m. This
  verifies coefficient magnitudes, EOD, dequant, copy matrix, IDCT, and
  per-patch placement against the actual OpenSim compressor.

Eighth follow-up after live terrain shape looked correct:

- `--terrain-z-scale` now defaults to `1.0` again so rendered terrain uses
  real meter scale. The option remains available for debugging exaggerated
  relief, e.g. `--terrain-z-scale 4`.

Ninth follow-up for render-debug controls:

- View -> Render Settings now opens a small render settings window. It exposes
  checkbox-style buttons for Terrain Surface, Mesh Lines, Water, and Objects.
  These write through to `Scene.render_terrain`, `render_terrain_lines`,
  `render_water`, and `render_objects`.
- The same window has a Water opacity slider. `Scene.water_alpha` defaults to
  `0.72`, making water less transparent than the original debug plane while
  still leaving submerged terrain readable.
- The renderer honors those scene flags in `PerspectiveRenderer.render_gl`.
  Mesh lines can now be hidden without disabling terrain fill; water and
  object rendering can also be isolated while debugging.

Tenth follow-up for first-pass lighting:

- `SimulatorTimeSnapshot` now retains the UDP `SunDirection`, and
  `viewer3d.Scene` surfaces it as `Scene.sun_direction` alongside
  `sun_phase`.
- The 3D renderer applies ambient + directional lighting to object meshes.
  Primitive normals are currently approximated from local vertex position, so
  this is a visual depth cue rather than final face-accurate prim shading.
- Filled terrain now uses a fragment normal derived from the rendered height
  surface and shades against the same sun direction. Mesh lines remain
  unlit/debug-bright.
- Texturing has not started yet beyond the existing map-tile/fallback terrain
  texture path. The next concrete rendering step is proper terrain/prim texture
  interpretation, starting with full `TextureEntry` decode and asset lookup.

Eleventh follow-up for first-pass texturing:

- When a terrain heightmap exists and the region map tile has been cached, the
  3D renderer now drapes that map tile over the terrain mesh instead of using
  only the debug height-color fill. If no map tile is available, the existing
  untextured height-color fill remains the fallback.
- The live session now watches `WorldView.objects` for non-zero
  `default_texture_id` values, fetches one pending texture at a time via the
  existing `GetTexture` capability, decodes JPEG2000, and caches PNGs under
  `local/texture-cache/<uuid>.png`.
- `texture.cache.ok` session events are bridged to a typed
  `TextureAssetReady` bus event. `viewer3d.Scene` records those paths in
  `Scene.texture_paths`.
- `PerspectiveRenderer` groups primitive draws by shape and available default
  texture. Textured prims use a coarse generated UV projection in the shader;
  this is intentionally first-pass only and not a replacement for proper
  `TextureEntry` per-face UV/material decode.
- Verification: `uv run --extra viewer pytest test/test_world_client.py
  test/test_udp_session.py test/test_viewer3d_scene.py
  test/test_viewer3d_perspective_gl.py`.

Twelfth follow-up for object UV scaling:

- The first object texture shader projected every prim texture through local
  XY. That made side faces collapse to a single texture column/row and looked
  like the object was sampling one pixel.
- Object texture sampling now uses generated per-face projection in the shader:
  X-facing faces sample Y/Z, Y-facing faces sample X/Z, and Z-facing faces
  sample X/Y. UVs are clamped to the unit face instead of wrapped at exact
  edges.
- Added a matching pure `generated_texture_uv()` helper and tests so the
  intended projection stays pinned.
- This still is not full SL `TextureEntry` material fidelity. It fixes the
  gross scale/projection issue for default-textured prim draw groups; per-face
  image IDs, repeats, offsets, rotations, alpha, and glow still need real
  `TextureEntry` decode and mesh/material batching.

Thirteenth follow-up for per-face texture plumbing:

- Added `vibestorm.world.texture_entry` with a first-pass `TextureEntry`
  parser. It decodes the image UUID section: default texture UUID plus
  face-mask texture UUID overrides using the OpenMetaverse MSB-first 7-bit
  mask encoding.
- `ObjectUpdateEntry`, `WorldObject`, and `viewer3d.SceneEntity` now retain the
  parsed `TextureEntry` object while preserving the existing
  `default_texture_id` field for fallback rendering.
- Improved-terse texture-entry payloads also update the retained parsed
  texture entry when they carry at least a default UUID.
- The renderer is not yet using per-face overrides. The next concrete step is
  to add logical face IDs to cube mesh triangles and batch/draw cube faces by
  `texture_entry.texture_for_face(face_index)`, falling back to the default
  texture for shapes without face IDs.

Fourteenth follow-up for cube per-face texture rendering:

- Cube rendering now draws six logical face submeshes. Each face resolves its
  texture through `SceneEntity.texture_entry.texture_for_face(face_index)` and
  falls back to `default_texture_id`/tint if the override or cached asset is
  unavailable.
- The object texture fetch queue now includes face-override texture UUIDs from
  parsed `TextureEntry`, not only default texture IDs.
- Non-cube primitive shapes still use the default texture draw path. That keeps
  spheres/cylinders/tori/prisms stable until their SL face mapping is modeled.
- Focused verification: `uv run --extra viewer pytest
  test/test_viewer3d_perspective_gl.py test/test_udp_session.py`.

## Update 2026-05-04: 3D Viewer Fork

3D viewer work has begun in a forked package rather than as an in-place
refactor of the 2D viewer.

- `src/vibestorm/viewer3d/` is a byte-for-byte copy of `src/vibestorm/viewer/`
  with intra-package imports retargeted and the window caption changed to
  "Vibestorm 3D Viewer". Behavior is identical to the 2D viewer today.
- `./run.sh viewer3d` runs the fork; `./run.sh viewer` is unchanged.
- The 2D `viewer/` package is now the stable reference. We don't intend to
  invest further in it; it stays for visual comparison and as a known-good
  baseline. Tests for `viewer/` still pass.
- The full plan, including renumbered implementation order with the fork as
  step 0, lives in `docs/viewer-3d-plan.md`.

Steps 1a, 1b-i, and 1b-ii are done.

- 1a: `viewer3d.scene` now exposes a renderer-agnostic `SceneEntity`
  (replacing `Marker`) with `kind`, full quaternion `rotation`,
  `default_texture_id`, `tint`, and a `shape` field. `Scene.sun_phase` is
  surfaced from `WorldView.latest_time`. `object_entities` /
  `avatar_entities` replace the old marker dicts.
- 1b-i: protocol fix. The `ObjectUpdate` parser had two self-cancelling
  off-by-one bugs (22-byte path/profile block, U16 ExtraParams length).
  Fixed: the block is decoded as 23 bytes via a new `PrimShapeData`
  dataclass, and ExtraParams uses U8 length per template. Side effect:
  `default_texture_id` is now the real UUID instead of being shifted
  left by one byte with a leading `0x00`. `docs/reverse-engineered-
  protocol.md` corrected; `test/fixtures/live/index.json` regenerated
  (now 43 captures vs 8).
- 1b-ii: `SceneEntity.shape` is now populated from real wire data via a
  new `classify_prim_shape(path_curve, profile_curve)` helper covering
  cube/sphere/cylinder/torus/prism/ring/tube. The OpenSim default sphere
  fixture classifies as `"sphere"`.

Test suite now 285 tests, all green. The 2D viewer reference under
`src/vibestorm/viewer/` is untouched.

Step 2 is done: `viewer3d/renderer.py` defines a `ViewerRenderer` protocol
plus a `TopDownRenderer` that wraps today's 2D draw. The app loop now
routes `update` / `render` / `clear_caches` through the renderer instead
of calling `render_scene` directly. Behavior unchanged. 289 tests, all
green.

Step 3 is done: "Render: 2D Map" and "Render: 3D" buttons in the View
menu, HUD tracks `render_mode`, status bar shows the active mode, and
selecting 3D posts a chat alert ("3D mode is not implemented yet").
296 tests, all green (7 HUD tests verified under `./run.sh test`).

Step 4 is done: `Camera3D` is a mode-aware camera with a Map mode that
reproduces today's pan/zoom math bit-for-bit, plus state fields for
Orbit/Eye/Free modes (yaw, pitch, distance, eye_position, target).
`Camera = Camera3D` alias preserves existing imports. The HUD render-
mode callback now also calls `camera.set_mode(...)`. 311 tests, all
green.

**The pre-3D refactor (steps 1a, 1b-i, 1b-ii, 2, 3, 4) is complete.**

Next planned step (`viewer-3d-plan.md` step 5): moderngl bootstrap. Add
the dependency behind a `viewer3d` extra in `pyproject.toml`, open a
hybrid `pygame.OPENGL | pygame.DOUBLEBUF` window, and draw a single
textured fullscreen quad (the cached region map tile) plus the existing
pygame_gui HUD on top. Goal: validate the GL+HUD compositing path
before any geometry lands.

## Summary

The bird's-eye viewer now has a runnable pygame v1. Session boots, fetches
its region map tile, caches it as PNG, surfaces inbound IM/Alerts in the
event stream, and the 2D viewer consumes `WorldView` + the cached map tile
directly.

## What Is Wired

Per session, automatically:
1. `RegionHandshake` → `RegionHandshakeReply` + `MapBlockRequest` for the
   current region's grid coords (`region_x // 256`, `region_y // 256`).
2. `MapBlockReply` is parsed; the entry matching our grid coords yields a
   `MapImageID` stashed as `session.region_map_image_id`.
3. The main loop polls for (GetTexture URL + image_id + not yet fetched) and
   runs `_fetch_and_cache_region_map`: HTTP GET → J2K decode → PNG written to
   `local/map-cache/<image_id>.png`. Path is exposed as
   `session.region_map_path` and surfaces in `SessionReport.region_map_path`.
4. Inbound `ChatFromSimulator` (existing), `ImprovedInstantMessage`,
   `AlertMessage`, and `AgentAlertMessage` all emit `chat.*` session events
   with the decoded text.
5. `LiveCircuitSession.build_chat_packet(text, *, chat_type=1, channel=0)`
   returns a ready-to-send packet (reliable + zerocoded) and emits a
   `chat.outbound` event.
6. `WorldClient` queues UI-built outbound packets; `run_live_session` drains
   that queue into the active UDP socket.
7. `WorldClient` publishes `InventorySnapshotReady` after the caps prelude
   fetches the login inventory/current-outfit snapshot.
8. `TeleportLocation` commands build reliable `TeleportLocationRequest`
   packets and queue them for the active circuit.
9. `src/vibestorm/viewer/` contains the pygame viewer:
   - `camera.py`: world/screen transform, zoom, pan, fit-region.
   - `scene.py`: render-state aggregation from typed bus events + `WorldView`,
     including self-position and inventory snapshot state.
   - `render.py`: map tile, grid, region border, object/avatar markers.
   - `hud.py`: top main-menu strip, bottom status bar, resizable chat,
     movement-help, teleport, options, and inventory windows.
   - `input.py`: movement keys, zoom wheel, right-drag pan, chat focus.
   - `app.py`: login + live session task + pygame loop.
10. `docs/viewer-help.md` is loaded into Help -> Movement Help.

## How to Verify

Run a live session against local OpenSim and check the report tail:

```
./run.sh opensim &
./run.sh session
```

Look for:
- `map[tile]=cached path=...` (success) — or `image_id_only` / `none events=...`
- A PNG appearing under `local/map-cache/<image_id>.png` matching the region's
  prerendered map.

Run the GUI:

```
./run.sh opensim &
./run.sh viewer
```

Expected v1 behavior:

- cached map tile as the region background once `MapBlockReply` + `GetTexture`
  complete
- colored markers for objects and avatars from `WorldView`
- WASD/arrows update movement control flags; mouse wheel zooms; right-drag pans
- Debug -> Center or `C` recenters on the avatar/coarse self position
- Enter focuses the chat window's input; submitting text sends `ChatFromViewer`
- the status bar shows avatar position, sim, parcel placeholder, map/object/avatar/chat counts
- Help -> Movement Help opens the 2D movement help file
- View -> Inventory shows the first read-only inventory snapshot from login/current-outfit fetches
- Tools -> Teleport sends a local `TeleportLocationRequest` to the current region handle
- UI scale is automatic from desktop size: 1920x1080 is 1x, 3840x2160 is 2x.
  Override with `./run.sh viewer --ui-scale N` if needed.

## What Remains for the Bird's-Eye Plan

- Full `TextureEntry` section-walking parser (default color first, then per-face).
- `ParcelOverlay` 4 KB bitfield decode → plot-edge polylines.
- Parcel status/name wiring after parcel metadata is decoded; the HUD currently says
  `Parcel: unknown`.
- Real inventory/asset management: folder browsing beyond the first snapshot, create/upload
  flows, asset permissions, and server-side store/update actions.
- Visual live pass against OpenSim to tune marker scale/colors, status text,
  main-menu contents, teleport behavior, inventory formatting, and chat-window persistence.

These are independent and can land in any order.

## Notes For The Next Agent

- All viewer-data protocol primitives live in `src/vibestorm/udp/messages.py`
  (encoders/parsers) and `src/vibestorm/caps/get_texture_client.py`.
- `src/vibestorm/assets/j2k.py` is the Pillow-backed decoder. Pillow is in
  the optional `viewer` extra (`uv sync --extra viewer`).
- The viewer dependency is `pygame-ce` rather than classic `pygame`; current
  `pygame_gui` imports APIs that classic `pygame` 2.6.1 does not expose.
- `local/map-cache/` is gitignored by the existing `local/` rule.

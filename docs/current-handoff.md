# Current Handoff

Last updated: 2026-05-04

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

Next planned step (`viewer-3d-plan.md` step 3): wire a View → Mode submenu
into the HUD with mode switching as a no-op for the modes that don't
exist yet. Then step 4 (`Camera3D`) and step 5 (moderngl bootstrap).

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

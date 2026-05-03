# Current Handoff

Last updated: 2026-05-03

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

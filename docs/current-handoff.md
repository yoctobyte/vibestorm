# Current Handoff

Last updated: 2026-05-01

## Summary

The bird's-eye viewer's protocol/session pipeline is now complete end-to-end.
Session boots, fetches its region map tile, caches it as PNG, surfaces inbound
IM/Alerts in the event stream, and exposes an outbound chat helper. Next
stage is presentation: a 2D viewer that consumes `WorldView` + the cached
map tile.

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

## What Remains for the Bird's-Eye Plan

- Full `TextureEntry` section-walking parser (default color first, then per-face).
- `ParcelOverlay` 4 KB bitfield decode → plot-edge polylines.
- Stdin / console hookup so the user can call `build_chat_packet` interactively.

These are independent and can land in any order — none of them block the
viewer presentation work.

## Next Stage

A 2D viewer process. Suggested shape:

- One Python process that runs the live session in the background and exposes
  `WorldView` snapshots + the cached map tile path to a renderer.
- Renderer choice (pygame/tkinter/web/PySide) is a v1 design decision.
- v1 visual: the cached map tile as background, oriented colored shapes for
  objects sized by `scale` and colored by pcode, avatar markers, chat panel
  for `chat.*` events, and an input box that calls `build_chat_packet`.

## Notes For The Next Agent

- All viewer-data protocol primitives live in `src/vibestorm/udp/messages.py`
  (encoders/parsers) and `src/vibestorm/caps/get_texture_client.py`.
- `src/vibestorm/assets/j2k.py` is the Pillow-backed decoder. Pillow is in
  the optional `viewer` extra (`uv sync --extra viewer`).
- `local/map-cache/` is gitignored by the existing `local/` rule.

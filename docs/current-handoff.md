# Current Handoff

Last updated: 2026-05-01

## Summary

Inventoried what protocol/data work is needed for a 2D bird's-eye region viewer
(see `docs/birds-eye-2d-plan.md`) and landed three of the four planned protocol
PRs. No session-loop wiring yet — these are protocol primitives only, ready to
be invoked when the GUI work begins.

## What Was Done This Session (2026-05-01)

**Antigravity / Claude Code:**

- `ObjectUpdate` parser refactor: extracted variable-length tail handling out
  of the `pcode == 9` branch and added the 66-byte fixed tail skip
  (`Sound`/`OwnerID`/`Gain`/`Flags`/`Radius`/`JointType`/`JointPivot`/`JointAxisOrAnchor`),
  so multi-object packets are decoded uniformly across pcodes. Live fixtures
  refreshed.
- `docs/birds-eye-2d-plan.md`: full inventory of the minimum protocol/data
  pieces needed for a 2D zoomable region viewer (background tile, object/avatar
  markers, optional parcel outlines, basic chat).
- `caps/get_texture_client.py`: HTTP GET wrapper for the `GetTexture` CAP.
- `assets/j2k.py`: Pillow-backed JPEG2000 decoder with `J2KDecodeError`
  fallback when Pillow or its J2K plugin is unavailable. Pillow added as an
  optional `viewer` extra in `pyproject.toml`.
- `encode_map_block_request` (Low/407, Unencoded) and `parse_map_block_reply`
  (Low/409). Reply carries one or more entries with `MapImageID` per region —
  fetchable via `GetTextureClient`.
- `encode_chat_from_viewer` (Low/80, Zerocoded) for outbound chat.
- `parse_improved_instant_message` (Low/254) — decodes the full MessageBlock.
- `parse_alert_message` (Low/134) and `parse_agent_alert_message` (Low/135)
  for system notifications.

All 178 tests pass.

## What Is Now Known

- `RegionHandshake` carries 4 base + 4 detail terrain texture UUIDs and a 2×2
  height-range grid for procedural terrain — it does **not** carry a single
  region map UUID. The flat 1024×1024 region map comes from
  `MapBlockReply.MapImageID`, fetched via the `GetTexture` CAP.
- The `GetTexture` CAP returns raw J2K bytes; URL form is
  `<cap_url>?texture_id=<uuid>` (per OpenSim's `GetTextureHandler`).
- `TextureEntry` is more complex than a flat `default_uuid + default_color`
  layout. It is a chain of 11 sections (UUID overrides, color overrides,
  ScaleU, ScaleV, OffsetU, OffsetV, Rotation, BumpShinyAlpha, MediaFlags,
  Glow, Materials), each `[default value][face_bitmask][per-face override]*[0x00 terminator]`.
  Colors are stored inverted (byte 0x00 = component 1.0). A proper
  `parse_texture_entry` walker is required before per-face color/scale data
  can be exposed.

## What Remains for the Bird's-Eye Plan

- TE section-walking parser (default color is the immediate goal; per-face is a stretch).
- `ParcelOverlay` body decode (4 KB packed bitfield → plot edge polylines).
- Session-loop wiring for all of the above: when to send `MapBlockRequest`,
  where to cache the fetched map tile, how to surface inbound IM/Alert in the
  console, and how to expose an outbound `say()`.

## One Concrete Next Step

Wire `MapBlockRequest` into the session prelude after `RegionHandshake`,
parse the reply, fetch the tile via `GetTextureClient`, decode with
`assets.j2k.decode_j2k`, and write to `local/map-cache/<region_uuid>.png`.
That closes the loop for the background-tile data path end-to-end against
local OpenSim and validates all three new protocol pieces in one live run.

## Notes For The Next Agent

- New protocol primitives live in `src/vibestorm/udp/messages.py` (encoders
  and parsers grouped near the existing ones) and
  `src/vibestorm/caps/get_texture_client.py`.
- J2K decode is gated on Pillow being installed (`pip install '.[viewer]'`
  or `uv sync --extra viewer`). Headless code paths are unaffected.
- The bird's-eye plan in `docs/birds-eye-2d-plan.md` has a "Progress" section
  at the end summarizing what landed and what's still open.

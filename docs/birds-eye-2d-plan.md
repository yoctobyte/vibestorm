# Bird's-Eye 2D Viewer — Minimum Protocol/Data Plan

Goal: a 2D, zoomable bird's-eye view of one OpenSim region with a textured background, object/avatar markers, and basic chat. **No GUI work yet** — this doc inventories the protocol and data pieces that must exist before any rendering.

## Layered minimum

The view decomposes into four data layers. Each row lists what we already have, what's missing, and the smallest path to fill the gap.

### Layer 1 — Spatial framework (mostly done)

| Need | Have? | Source |
|---|---|---|
| Region size (256×256 m) | yes | constant |
| Region grid coords (region_x, region_y) | yes | `SimStats` (`region_x`, `region_y`) |
| Region UUID + name | yes | `RegionHandshake` |
| Object position (x,y,z) | yes | `ObjectUpdate.position` |
| Object scale (x,y,z) | yes | `ObjectUpdate.scale` |
| Object rotation (quat) | yes | `ObjectUpdate.rotation` |
| Avatar positions (precise) | yes | `ObjectUpdate` pcode=47 |
| Avatar positions (coarse, off-region) | yes | `CoarseLocationUpdate` |

**Status:** ready. No new protocol work needed for the spatial frame.

### Layer 2 — Background map tile (the big missing piece)

The bird's-eye background is a 1024×1024 JPEG2000 region tile. `RegionHandshake` does **not** carry a single map-image UUID — it carries 4 base + 4 detail terrain UUIDs for procedural rendering. The shortcut path is the prerendered map tile via the map-block messages.

| Need | Have? | Plan |
|---|---|---|
| `MapBlockRequest` outbound (UDP) | no | Build per-message-template (Low 407); fields: AgentData + PositionData (MinX..MaxY in region-widths). Send once per session for the current region. |
| `MapBlockReply` decode (UDP) | no — template-recognised but body not decoded | Variable `Data` block; `MapImageID` is the LLUUID we want. |
| `GetTexture` CAP client | **partly** — CAP is requested in `_run_caps_prelude` but no client exists | Wrap the CAP URL: HTTP GET `?texture_id=<uuid>` → returns J2K bytes. Mirror `upload_baked_texture_client.py` style. |
| J2K → raster decode | no | Add a dependency. Options: `Pillow` (needs build with openjpeg), `glymur`, or Pillow-with-imagecodecs. Ship as optional extra; viewer is gated on it. |
| Cache map tiles to disk | no | `local/map-cache/<region_uuid>.png` after first decode. |

**Minimum for v1:** a single tile for the current region. Off-region tiles (true zoom-out across the grid) are a v2 expansion using `MapLayerReply` (Layer-typed image UUIDs covering a rect of regions).

**Skip for v1:** procedural terrain (`LayerData` patches + `RegionHandshake` terrain UUIDs). Heavier and elevation isn't needed for top-down.

### Layer 3 — Object visuals (degrade gracefully)

For top-down, full per-face TextureEntry decoding is overkill. Render objects as oriented rectangles or oriented bounding-box footprints, colored by category, optionally tinted by the default-face color.

| Need | Have? | Plan |
|---|---|---|
| Default texture UUID per object | yes | first 16 bytes of `TextureEntry` already extracted |
| Default-face color (RGBA) | no | **Not 4 bytes after the default UUID** — the TE format is a chain of sections (UUID overrides → color overrides → ScaleU → ScaleV → OffsetU → OffsetV → Rotation → BumpShiny → MediaFlags → Glow → Materials), each `[default value][face_bitmask][override]*[0x00 terminator]`. Need a full section-walking parser in `messages.py`. Colors are stored inverted (0x00 byte = 1.0). Plan: a single `parse_texture_entry()` returning per-face properties; defer per-face logic, just expose `default_color`. |
| Object pcode → category mapping | yes (raw pcode) | constants table: 9=prim, 47=avatar, 95=tree, 255=grass — render color/marker by category |
| Texture asset fetch + decode | no | same `GetTexture` + J2K dep as Layer 2 — strictly optional for v1 |

**Minimum for v1:** colored rectangles by pcode + scale, no real textures. This already works from current data.

### Layer 4 — Parcels (optional but cheap)

| Need | Have? | Plan |
|---|---|---|
| `ParcelOverlay` decode (UDP) | no — template-recognised, 329 observed | Variable `Data` field is a packed bitfield (1 byte per 4×4 m cell, 64×64 grid = 4096 bytes split across `SequenceID` chunks). Decode → polylines for plot edges. |

Useful as a viewer toggle; not required for "see the world."

### Layer 5 — Text protocols

| Need | Have? | Plan |
|---|---|---|
| Inbound chat (`ChatFromSimulator`) | yes | already decoded |
| Outbound chat (`ChatFromViewer`) | **no** | Build per template (Medium 80): AgentData + ChatData (Message, Type, Channel). Add a `send_chat()` to session. |
| Inbound IM (`ImprovedInstantMessage`) | no — template-recognised, 5 observed | Decode body: AgentData + MessageBlock (FromAgentID, BinaryBucket, Dialog, Message, …). Many dialog types — start with `Dialog == 0` (regular IM). |
| Outbound IM (`ImprovedInstantMessage`) | no | Same struct, viewer-side build. Punt to v1.5. |
| `AlertMessage` / `AgentAlertMessage` | no | One Variable string field each. Trivial decode. Surface in the chat panel as system lines. |

**Minimum for v1:** outbound `ChatFromViewer` + decoded `ImprovedInstantMessage` (inbound) + `AlertMessage`. Outbound IM is v1.5.

## Summary — minimum new work for v1

**Three protocol additions:**
1. `MapBlockRequest` outbound + `MapBlockReply` body decode (UDP, message_template-driven; small).
2. `GetTexture` CAP client (HTTP GET, returns J2K bytes).
3. `ChatFromViewer` outbound + `ImprovedInstantMessage` inbound decode (+ `AlertMessage`/`AgentAlertMessage` while we're there).

**One dependency choice:** a J2K decoder. Recommend `Pillow` with system openjpeg (already on most Linux), with a clean ImportError fallback so the headless decoder/console keeps working.

**Two small enhancements to existing decoders:**
- TE default-face color extraction (4 bytes after the default UUID).
- Optional: `ParcelOverlay` body decode (4 KB packed bitfield).

**No GUI yet.** When this list is done, the data side can produce: a textured 1024×1024 region background, oriented colored markers for every object/avatar with correct positions and footprints, optional parcel outlines, and a working chat panel — all driven from the existing session loop.

## Sequencing suggestion (if/when we build it)

1. `GetTexture` CAP client + J2K decode — one focused PR; verify against any object texture UUID we already have.
2. `MapBlockRequest`/`Reply` round trip — emits region MapImageID; pipe into the GetTexture client → write the tile to `local/map-cache/`.
3. `ChatFromViewer` outbound + `ImprovedInstantMessage` decode — independent of the map work; can land in parallel.
4. TE section-walking parser + ParcelOverlay — polish before any GUI.

After step 4, every byte the GUI needs is in `WorldView` (or in a sibling `MapView` cache) and rendering becomes a pure presentation problem.

## Progress (2026-05-01)

- ✅ Step 1: `caps/get_texture_client.py` + `assets/j2k.py` (Pillow-backed, optional `viewer` extra). 8 tests.
- ✅ Step 2: `encode_map_block_request` + `parse_map_block_reply` + dataclasses. 3 tests.
- ✅ Step 3: `encode_chat_from_viewer` + `parse_improved_instant_message` + `parse_alert_message` + `parse_agent_alert_message`. 5 tests.
- ✅ Wiring (a4f5e98, 6f1c319, 63b37f1): inbound IM/Alert/AgentAlert surfaced as `chat.im` / `chat.alert` / `chat.agent_alert` events; `LiveCircuitSession.build_chat_packet()` outbound helper; `MapBlockRequest` autosent after `RegionHandshakeReply`; `MapBlockReply` parsed and matched against bootstrap grid coords; tile fetched via `GetTextureClient`, decoded via `assets.j2k`, written as PNG to `local/map-cache/<image_id>.png`. `SessionReport` and the CLI `format_session_report` surface the result. 4 new dispatch/wiring tests.
- ⏸ Step 4: deferred. The TE format turned out to be a multi-section chain (see Layer 3 row); the "4 bytes after the default UUID" simplification doesn't hold. Needs a proper `parse_texture_entry` walker. ParcelOverlay also still pending.

Session totals: 173 tests, all passing. The whole data pipeline for a 2D bird's-eye region viewer (background tile, object/avatar markers, basic chat) is now operational at the protocol/session layer. Next stage is presentation: a 2D viewer process that consumes `WorldView` + the cached map tile and renders.

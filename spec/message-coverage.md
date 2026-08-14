# Message Coverage

Last verified: 2026-08-14 (previous revision: 2026-04-02)

Statuses distinguish `tested` from `verified` — see Status Scale. A row that
says `verified` means a live simulator sent it and the client handled it, not
that a unit test passes.

This document tracks which UDP message-system messages matter for Vibestorm and
what level of support each has.

The 2026-04-02 revision had drifted badly — it listed as `planned` a dozen
messages that were implemented, tested, and live-observed months earlier. A
coverage ledger that understates coverage is worse than none: it invites
re-implementing finished work. Statuses below were re-derived by checking for a
parser in `udp/messages.py`, a handler branch in `udp/session.py` or
`world/updater.py`, tests, and this session's live census (`./run.sh unknowns`).

## Status Scale

- `planned`: known requirement, not started
- `parse-only`: message can be identified and decoded enough for inspection
- `handled`: message participates in client behavior
- `tested`: behavior covered by fixtures or unit tests, but the live sim has
  never sent it — usually because the region holds no content that triggers it
- `verified`: **observed against a live simulator**, not only tested

The split between the last two matters more than it looks. This session
produced three cases where a decoder passed its unit tests and was wrong on
live data: SimStats named from the wrong one of two enums, the inventory walk
raising on the real payload shape, and a GL cache policy that was correct
per-frame and pathological across frames. A green suite is evidence about the
code; only a live run is evidence about the protocol.

Where a row covers several sub-decoders and only some are live-confirmed, the
status reflects the **weakest** one and the note says which is which.

## Phase 1-2 Critical Messages

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `UseCircuitCode` | establish simulator circuit | P0 | verified | outbound builder plus inbound semantic parse; every live session opens with it |
| `CompleteAgentMovement` | finish avatar presence bootstrap | P0 | verified | outbound builder; `AgentMovementComplete` reply observed every session |
| `AgentMovementComplete` | simulator confirms movement completion | P0 | verified | semantic parse; observed 2026-08-14 |
| `RegionHandshake` | simulator sends region/session metadata | P0 | verified | semantic parse; observed 2026-08-14, and now also triggers the parcel-properties request |
| `RegionHandshakeReply` | acknowledge region handshake | P0 | verified | outbound builder |
| `AgentUpdate` | steady-state agent control/update traffic | P0 | verified | periodic send path; ~70 sent per 90 s session |
| `StartPingCheck` | ping/health mechanism | P0 | verified | semantic parse; observed 2026-08-14 |
| `CompletePingCheck` | ping response | P0 | verified | semantic parse and outbound builder |
| `PacketAck` | explicit ACK transport support | P0 | verified | explicit outbound ACK support; observed both directions |
| `AgentThrottle` | viewer bandwidth preferences | P1 | handled | `encode_agent_throttle` sent during session startup; no reply to observe, so not `verified` |

## Phase 3 Text/2D Messages

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `ChatFromSimulator` | receive nearby chat/system chat | P1 | verified | parsed and published as `chat.local`, with the chat type named from `ChatTypeEnum`. Live-verified 2026-08-14 by sending whisper/say/shout and reading the sim's echo — types 0/1/2, `audible=1`. Start/stop-typing (4/5) are still `tested` only: this client never sends them and the region has one avatar |
| `ChatFromViewer` | send nearby chat | P1 | verified | outbound builder; wired to the viewer chat window. Live-verified 2026-08-14 — three lines sent at different chat types and echoed back correctly |
| `ImprovedInstantMessage` | IM/event-style message path | P1 | handled | parsed and published as `chat.im` |
| `CoarseLocationUpdate` | coarse avatar positions | P1 | verified | drives `WorldView` agent positions; observed every session |
| `AvatarAnimation` | avatar state hints | P2 | verified | typed decode plus bus event; observed 2026-08-14 |
| `ObjectAnimation` | object animation state | P2 | handled | typed decode plus bus event; no animated objects in the test region |
| `SimulatorViewerTimeMessage` | region time/environment hints | P2 | verified | drives sun phase; observed every session |
| `SimStats` | region health telemetry | P2 | verified | 41 stat ids decoded and named from OpenSim's `StatsID` — *not* the `StatsIndex` enum beside it, which numbers the internal array and diverges from id 4 on. Surfaced as `world[sim_health]`. Live-verified 2026-08-14: every id resolved, none unknown |
| `AlertMessage` / `AgentAlertMessage` | user-visible server alerts | P1 | handled | parsed and published as `chat.alert`; needs a sim-side alert to observe |
| `SoundTrigger` / `AttachedSound` / `AttachedSoundGainChange` / `PreloadSound` | in-world audio | P3 | handled | typed decode plus bus events; no sound emitters in the test region |

## Phase 4 Object/World Messages

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `ObjectUpdate` | object state/update path | P1 | verified | full semantic decode including the rich tail; observed every session |
| `ObjectUpdateCached` | cached object updates | P2 | verified | handled, with full-update requests issued for cache misses |
| `ObjectUpdateCompressed` | compressed object updates | P1 | verified | semantic decode including the 23-byte shape block, whose field order differs from the message template's; the bulk of object traffic in a populated region |
| `ImprovedTerseObjectUpdate` | compact frequent updates | P1 | verified | structural parse with terse `local_id` promotion; observed every session |
| `KillObject` | remove object from world cache | P1 | handled | parsed and applied to `WorldView`; needs an object delete to observe |
| `ObjectPropertiesFamily` | object name/owner metadata | P1 | verified | drives inspector names; the highest-count inbound message in a populated region |
| `ObjectExtraParams` | rich per-prim feature blocks | P2 | tested | seven sub-decoders; only **sculpt and flexi** are live-confirmed (2026-08-14). Light, projector, reflection probe, render materials and mesh flags have never seen live data — `./run.sh census` lists all five under `absent=`, so they are unit-tested only |
| `AvatarAppearance` | avatar appearance metadata | P3 | verified | parsed; drives the appearance/bake path |
| `AgentWearablesUpdate` | the agent's own worn wearables | P3 | verified | parsed into the appearance state and reported as `appearance[wearables]`; observed 2026-08-14 (serial 0, 6 wearables, types 0-5) |
| `AgentCachedTextureResponse` | which baked textures the sim already holds | P3 | verified | parsed; gates the deferred bake upload. Observed 2026-08-14 — 11 entries, all texture ids zero, i.e. the sim had nothing cached and every bake had to be uploaded |
| `MapBlockReply` | region map block metadata | P2 | verified | supplies the region map-tile asset id, which the `GetTexture` cap then fetches; observed 2026-08-14 (`map[tile]=cached`) |
| `LayerData` | terrain patches | P1 | verified | 16x16 Land decode observed live 2026-08-14. The 32x32 LandExtended path is `tested` only — it needs a varregion, and the test sim is a standard 256 m region |
| `ParcelOverlay` | region parcel ownership grid | P2 | verified | reassembled into a 64x64 grid with border segments; observed 2026-08-14 |
| `ParcelProperties` | parcel metadata | P2 | verified | **arrives over the event queue, not UDP** — OpenSim has no UDP send path for it, so it never appears in a UDP census. Confirmed live 2026-08-14 |

## Asset Delivery Messages

These are the two UDP asset channels. Both are driven from the object inspector
rather than from session startup, so a bounded `./run.sh session` never
exercises them — the evidence below comes from viewer runs.

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `ReplyTaskInventory` | object inventory listing header | P3 | verified | supplies the task id, serial and xfer filename; live-confirmed against scripted objects |
| `SendXferPacket` | xfer payload for that listing | P3 | verified | packets confirmed and reassembled, then parsed into `inv_item` blocks |
| `TransferInfo` | asset transfer handshake | P3 | verified | `TransferRequest` → `TransferInfo` → `TransferPacket*`; source type 2 (global assets) is reliable |
| `TransferPacket` | asset payload | P3 | verified | 80 KB reassembled across 130+ packets live; status 1 means Done, not an error. Source type 3 (task inventory) is **inconsistent** — OpenSim withholds the asset id when permissions are insufficient, and the client now declines to send a doomed request rather than hang |

## Transport and Template Work

These are not user-facing messages but must exist before many handlers are useful.

| Item | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| packet header parser | decode flags, sequence, header length | P0 | verified | ACK trailer split tests and packet builder round-trip |
| zerocode decoder | expand zerocoded packets | P0 | verified | simple and wrapped zero-run tests |
| reliable ACK tracking | support reliable transport semantics | P0 | verified | explicit ACK send path and duplicate suppression |
| `message_template.msg` loader | map IDs and fields to messages | P0 | verified | template summaries load from the canonical artifact |
| message-number decoder | resolve variable-length message IDs by packet frequency | P0 | verified | high, medium, low, and fixed message numbers tested |
| message dispatcher | route decoded messages to handlers | P0 | verified | live sessions report `unknown_udp_messages=0` |
| EventQueueGet polling | second inbound channel beside UDP | P0 | verified | background poll loop with per-batch acks; typed event decode |

## Current Coverage Position

As of 2026-08-14, **every message observed in live traffic is handled** — a
session reports `unknown_udp_messages=0`, and cross-referencing the inbound
census against `session.py` and `updater.py` shows no message arriving that the
client ignores.

The remaining `handled`-but-not-`verified` rows are not gaps in the code. They
are messages the test region never produces: no in-world speaker for chat, no
sim-side alerts, no object deletes, no sound emitters, no animated objects. Each
needs world content rather than more decoding.

## Notes

- Do not treat coverage as complete because a message name is recognized.
- `handled` should mean the message changes client state or causes the correct response.
- `verified` should require either test fixtures or live capture evidence.
- A UDP census is not the whole picture. `ParcelProperties` is `verified` and
  never appears in one, because OpenSim delivers it over the event queue. Check
  both channels before concluding a message is absent.

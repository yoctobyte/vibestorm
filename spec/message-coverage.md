# Message Coverage

Last verified: 2026-09-02 (previous revision: 2026-08-14)

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
| `AgentUpdate` | steady-state agent control/update traffic | P0 | verified | periodic send path; ~70 sent per 90 s session; client-side turn integration and walking verified live |
| `StartPingCheck` | ping/health mechanism | P0 | verified | semantic parse; observed 2026-08-14 |
| `CompletePingCheck` | ping response | P0 | verified | semantic parse and outbound builder |
| `PacketAck` | explicit ACK transport support | P0 | verified | explicit outbound ACK support; observed both directions |
| `AgentThrottle` | viewer bandwidth preferences | P1 | handled | `encode_agent_throttle` sent during session startup; no reply to observe, so not `verified` |

## Phase 3 Text/2D Messages

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `ChatFromSimulator` | receive nearby chat/system chat | P1 | verified | parsed and published as `chat.local`, with the chat type named from `ChatTypeEnum`. Live-verified 2026-08-14 by sending whisper/say/shout and reading the sim's echo — types 0/1/2, `audible=1`. Start/stop-typing (4/5) are still `tested` only: this client never sends them and the region has one avatar |
| `ChatFromViewer` | send nearby chat | P1 | verified | outbound builder; wired to the viewer chat window. Live-verified 2026-08-14 — three lines sent at different chat types and echoed back correctly |
| `ImprovedInstantMessage` | IM/event-style message path | P1 | verified | parsed and published as `chat.im`, and now sent too. Live-verified 2026-08-14 by IMing our own agent id and reading the delivery 5.5 s later — OpenSim's `InstantMessageModule` routes on `ToAgentID` with no self-check, so this needs no second avatar |
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
| `ObjectExtraParams` | rich per-prim feature blocks | P2 | verified | all seven sub-decoders live-confirmed. Sculpt and flexi from region content (2026-08-14); light, projector, reflection probe, render materials and mesh flags on 2026-09-02 by **writing** them — `encode_object_extra_params` sets the blocks on a prim we own, the sim echoes them in `ObjectUpdate`, and every value decoded back byte-exact. Run it with `session-run --probe-extra-params`, which clears the blocks again afterwards. The inbound parser is kept for other grids but cannot fire against OpenSim, which has no send path for this message |
| `RezScript` | create a new script inside a prim | P2 | verified | outbound only, and only the zero-ItemID form, which is what `Scene.RezScript` routes to `RezNewScript` ("rez a new script from nothing"); a non-zero id copies from agent inventory instead. **The prim id goes in FolderID** — `RezNewScript` looks the part up with `GetSceneObjectPart(itemBase.Folder)`. Live-verified 2026-09-02: the new row carried `Constants.DefaultScriptID`, which is how the create is told apart from a row that was already there. Run it with `session-run --probe-create-script` |
| `UpdateTaskInventory` | copy an agent inventory item into a prim | P2 | verified | outbound only. **It cannot create an item**: `Scene.UpdateTaskInventory` rejects a zero item id outright, and an id it does not find in the prim it looks up in agent inventory, then the grid library, and copies in. So types with no create-from-nothing message — notecards above all — must exist in agent inventory first. `Key` must be 0; any other value means an asset update and the handler returns at once. Live-verified 2026-09-02 by `tools/verify_drop_item.py`: a notecard went from agent inventory into a prim we own. Note the sim *removes* a no-copy item from agent inventory, since that is a move rather than a copy |
| `RemoveTaskInventory` | delete one row from a prim's contents | P2 | verified | outbound only. Two fields and no reply: `LocalID` then the **task** item id, which is not the agent inventory id a copied item came from. Nothing acknowledges it, so a re-read of the object inventory is the only way to know it worked. Live-verified 2026-09-05 by `tools/clean_test_prim.py`, which took the local test prim from 12 items to 5. It deletes with no undo, which is why the encoder refuses a zero item id rather than letting the simulator decide what an unset id means |
| `AvatarAppearance` | avatar appearance metadata | P3 | verified | parsed; drives the appearance/bake path |
| `AgentWearablesUpdate` | the agent's own worn wearables | P3 | verified | parsed into the appearance state and reported as `appearance[wearables]`; observed 2026-08-14 (serial 0, 6 wearables, types 0-5) |
| `AgentCachedTextureResponse` | which baked textures the sim already holds | P3 | verified | parsed; gates the deferred bake upload. Observed 2026-08-14 — 11 entries, all texture ids zero, i.e. the sim had nothing cached and every bake had to be uploaded |
| `MapBlockReply` | region map block metadata | P2 | verified | supplies the region map-tile asset id, which the `GetTexture` cap then fetches; observed 2026-08-14 (`map[tile]=cached`) |
| `LayerData` | terrain patches | P1 | verified | 16x16 Land decode observed live 2026-08-14. The 32x32 LandExtended path is `tested` only — it needs a varregion, and the test sim is a standard 256 m region |
| `ParcelOverlay` | region parcel ownership grid | P2 | verified | reassembled into a 64x64 grid with border segments; observed 2026-08-14 |
| `ParcelProperties` | parcel metadata | P2 | verified | **arrives over the event queue, not UDP** — OpenSim has no UDP send path for it, so it never appears in a UDP census. Confirmed live 2026-08-14 |

## Outbound Requests

Every message the client can build. Most are the request half of a reply
listed above, and were unlisted for the same reason the teleport request was:
a message with no parser is invisible to a check that looks for parsers
without rows. They are now derived from the message-number prefix each encoder
writes, so this table cannot fall behind the encoders again.

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `LogoutRequest` | end the session cleanly | P0 | verified | sent at shutdown of every session |
| `CreateInventoryItem` | make an empty inventory item | P2 | verified | the working notecard/script creation path. OpenSim's `InventoryAccessModule` switches on `Type` and points a new notecard at `Constants.EmptyNotecardID`; content follows over `UpdateNotecardAgentInventory`. `TransactionID` must be zero or the request goes to the legacy asset-transaction path instead. Live 2026-08-15: created `type:7 inv_type:7` |
| `AgentWearablesRequest` | ask what we are wearing | P2 | verified | drives `appearance[wearables]`; answered every session |
| `AgentCachedTexture` | ask which bakes the sim already has | P2 | verified | answered by `AgentCachedTextureResponse`; observed 2026-08-14 |
| `AgentIsNowWearing` / `AgentSetAppearance` | publish our appearance | P2 | verified | the bake upload path; `appearance[baked] uploaded:5` observed 2026-08-14 |
| `ParcelPropertiesRequest` | ask for parcel metadata | P2 | verified | sent on handshake; the reply arrives over the event queue |
| `RequestObjectPropertiesFamily` | ask for an object's name/owner | P1 | verified | drives inspector names |
| `RequestMultipleObjects` | ask for full updates on cache misses | P2 | verified | issued from the `ObjectUpdateCached` path |
| `MapBlockRequest` | ask for region map blocks | P2 | verified | answered by `MapBlockReply`; observed 2026-08-14 |
| `RequestTaskInventory` / `RequestXfer` / `ConfirmXferPacket` | read an object's inventory | P3 | verified | the xfer handshake behind the object inspector |
| `TransferRequest` | ask for an asset | P3 | verified | source type 2 reliable; source type 3 inconsistent — see the asset-delivery table |
| `ObjectAdd` | rez a prim | P4 | verified | rezzed two prims into local OpenSim on 2026-09-05, at the positions asked for, to make a linkset to observe; `build_object_add_packet` |
| `ObjectLink` | join prims into a linkset | P4 | verified | sent to make a linkset that could be observed: the child's next update reported its offset from the root rather than a region position, which is what `viewer3d/linkset.py` exists to undo. `tools/verify_child_prim_frame.py` |

## Teleport Messages

The client could already *send* `TeleportLocationRequest` and decoded none of
the replies, so a teleport was a request into silence. The coverage ledger did
not say so, because a message with no parser at all is invisible to the
completeness check — it only catches a parser with no row, not a gap with
neither.

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `TeleportLocationRequest` | ask to teleport to a position | P2 | verified | outbound builder; live-verified 2026-08-14 both ways — a real destination and a region handle no region occupies |
| `TeleportStart` | the sim accepted the request | P2 | verified | observed 2026-08-14, `flags=via location` |
| `TeleportLocal` | the teleport landed in the same region | P2 | verified | the *entire* response to a same-region hop: no `TeleportFinish`, no new circuit, no seed cap. Updates the session position, which nothing else would. Observed 2026-08-14 |
| `TeleportFailed` | the teleport did not happen | P2 | verified | observed 2026-08-14: `'The region you tried to teleport to was not found'`, with zero `AlertInfo` blocks — OpenSim never populates that block, so the decode loop for it is `tested` only |
| `TeleportProgress` | a step within a teleport | P3 | tested | decoded but never observed: OpenSim sends no progress steps for a local teleport, and a crossing needs a neighbour region |
| `TeleportFinish` | the teleport landed in a *different* region | P2 | planned | marked `UDPBlackListed` in the template and delivered over the event queue, like `ParcelProperties`. Needs a neighbour region to observe, and the region-crossing transport it implies is not built |

The `TeleportFlags` word these carry is fully named from OpenSim's
`Constants.cs` — see `world/teleport_flags.py`. One caution recorded there:
OpenSim never sets any `FinishedVia*` bit, so a client cannot use them to tell
a local teleport from a crossing. The message type is the signal.

## Asset Delivery Messages

These are the two UDP asset channels. Both are driven from the object inspector
rather than from session startup, so a bounded `./run.sh session` never
exercises them — the evidence below comes from viewer runs.

Since 2026-08-14 the `TransferRequest` half is a **fallback**: `RequestAssetData`
tries the `ViewerAsset` capability first and only sends a `TransferRequest` if
the HTTP fetch fails, or if the asset type has no sourced query key. The bytes
land in the same place either way.

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `UpdateCreateInventoryItem` | reply naming the item just created | P2 | verified | carries the new item id, the asset it points at, both type numbers and the `CallbackID` we sent — the only thing tying a reply to its request. Live 2026-08-15. Its UUIDs are big-endian like the rest of the protocol; reading them little-endian yields a well-formed but wrong id that nothing downstream rejects |
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
are messages the test region never produces: no sim-side alerts
(`AlertMessage`), no object deletes (`KillObject`), no sound emitters, no
animated objects. Each needs world content rather than more decoding.

Two rows have left this list by the same route. Chat was said to need an
in-world speaker; it does not — the client can say something and read the
simulator's echo. IM was said to need a second avatar; it does not — OpenSim
routes on `ToAgentID` without checking whether it is the sender's own. So:
before recording a row as blocked on world content, check whether the client
can produce the traffic itself. Twice now the blocker was a missing outbound
message rather than a missing object.

## Notes

- Do not treat coverage as complete because a message name is recognized.
- `handled` should mean the message changes client state or causes the correct response.
- `verified` should require either test fixtures or live capture evidence.
- A UDP census is not the whole picture. `ParcelProperties` is `verified` and
  never appears in one, because OpenSim delivers it over the event queue. Check
  both channels before concluding a message is absent.

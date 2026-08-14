# Capability Coverage

Last verified: 2026-08-14 (previous revision: 2026-04-02)

Like `message-coverage.md`, this had drifted: every row still read `planned`
while nine capabilities were being resolved and used every session. Statuses
were re-derived from the seed-cap request list in `udp/session.py`, the clients
in `caps/`, and this session's live `caps[seed]` line.

This document tracks which simulator capabilities matter for Vibestorm and when they should be implemented.

## Status Scale

- `planned`: known requirement, not started
- `resolved`: capability name is requested and resolved from seed caps
- `used`: client issues requests against the capability
- `verified`: behavior covered by tests or live-session evidence

## Phase 1-2 Core Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `EventQueueGet` | control-plane long-poll event stream | P0 | verified | background poll loop with per-batch acks; typed event decode. Carries `ParcelProperties`, which never arrives over UDP |
| `SimulatorFeatures` | discover simulator feature flags | P1 | verified | resolved and fetched during the caps prelude |

## Phase 3 Inventory-Oriented Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `FetchInventory2` | fetch inventory items | P2 | verified | drives the viewer inventory manager; resolved every session |
| `FetchInventoryDescendents2` | fetch inventory folders/children | P2 | verified | lazy folder expansion in the inventory window |
| `FetchLib2` | fetch library items | P3 | planned | **offered by the sim** (confirmed 2026-08-14) but not requested. `FetchLibDescendents2` already gives the whole tree with items attached, so this adds nothing until something needs a single library item by id |
| `FetchLibDescendents2` | fetch library descendants | P3 | verified | `./run.sh inventory-walk --library`. Live-verified 2026-08-14: 19 folders, 123 items — 64 textures, 17 scripts, 16 gestures, 12 animations, 7 settings, 4 body parts, 2 clothing, 1 notecard; no sounds or objects. The owner id must be the library owner, not our agent id: `FetchLibDescHandler` compares it and answers a mismatch with an empty tree rather than an error |
| `NewFileAgentInventory` | upload/create inventory assets | P4 | verified | `caps/asset_upload_client.py`; `./run.sh upload-smoke` confirmed a live round trip |
| `UpdateScriptTask` / `UpdateScriptTaskInventory` / `UpdateNotecardTaskInventory` | update object task inventory | P3 | used | `caps/task_inventory_upload_client.py` issues the requests; never confirmed against a running sim, because doing so writes scripts into an in-world object and needs the sim owner's consent. OpenSim registers the script cap under both names — `UpdateScriptTask` and, marked `//legacy` in `BunchOfCaps`, `UpdateScriptTaskInventory` — and the client now asks for both, current name first |
| `RequestTaskInventory` | inspect task inventory | P3 | verified | UDP message plus xfer assembly, not a capability; listed here for completeness |
| `UploadBakedTexture` | upload baked avatar textures | P2 | verified | five baked J2K blobs uploaded per session; appearance accepted |
| `GetTexture` | fetch texture assets | P1 | verified | region map tiles and object textures, cached as PNG |
| `GetMesh` / `GetMesh2` | fetch mesh assets | P1 | verified | `.llmesh` fetch and decode into renderer geometry |
| `ViewerAsset` | generic asset fetch | P2 | verified | `caps/viewer_asset_client.py`. Live-verified 2026-08-14: the region map texture fetched by asset id, 4376 bytes, `image/x-j2c` — the same bytes `GetTexture` returns. A nonexistent id gives 404. Two cautions, both sourced from `GetAssetsHandler`: an unknown key is answered 404 before the asset service is consulted, so there is no generic `asset_id`; and the type check is **not enforced** — the same texture requested as `notecard_id` was served in full, because the `return` under `asset with wrong type` is commented out. A 200 is therefore no evidence about an asset's type. Probed again 2026-08-14 and the key turns out not to select anything at all: the library's `Shirt` fetched under `clothing_id`, `bodypart_id` and `gesture_id` returned identical bytes each time, always with `Content-Type: application/vnd.ll.clothing`. The key only has to be *recognised*; the **response content type** is what names the asset's real type, which is how worn wearables are fetched without libomv's wearable-type table (`asset_type_from_content_type`). The client's key table covers only the type numbers LSL pins, since `AssetType` itself is libomv's |

## Phase 4 World/Rendering Relevant Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `RegionObjects` | object/region data path | P2 | planned | **not offered by this sim** (confirmed 2026-08-14) — one of only two probed names that did not resolve |
| `RenderMaterials` | materials data | P4 | planned | offered by the sim (confirmed 2026-08-14). The per-face material *UUIDs* decode from `ExtraParams` (`0x80`), but the material assets are not fetched. `ViewerAsset`'s `material_id` key may serve them without this cap; untried, because no prim in the region has a material |
| `ObjectMedia` | media metadata | P4 | planned | offered by the sim (confirmed 2026-08-14); not early-scope |
| `ObjectMediaNavigate` | media navigation | P4 | planned | offered by the sim (confirmed 2026-08-14); not early-scope |
| `GetObjectCost` | land impact or cost-style data | P3 | verified | `caps/object_cost_client.py`, alongside physics under `./run.sh census --physics`. Live-verified 2026-08-14: 32 prims, every one costing 1, `resource_limiting_type=legacy`. **Batching works here** — unlike `GetObjectPhysicsData` in the same source file, this handler closes its outer map after the loop, and four ids returned four entries. Two traps: a request matching nothing is answered with a filler entry keyed by the **zero UUID** and all costs 0, which is shaped exactly like a real free prim (the client drops it); and equal prim/linkset costs mean the prim's cost covers its linkset, not that the linkset has one prim |
| `GetObjectPhysicsData` | physics-related object data | P2 | verified | `caps/object_physics_client.py`, behind `./run.sh census --physics`. This is how to read prim physics *without* an in-world edit — the UDP `ObjectPhysicsProperties` message only echoes an edit the viewer itself made. Live-verified 2026-08-14: 32 of 33 objects answered, all shape `prim` at OpenSim defaults; the one that did not is our own avatar, which is in the same collection but is not a `SceneObjectPart`. **One id per request** — OpenSim's handler closes the outer LLSD map inside its loop, so two ids return XML that does not parse (confirmed live, `mismatched tag`) |

## Session and Account Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `AgentPreferences` | account/session preferences | P3 | planned | offered by the sim (confirmed 2026-08-14); later usability work |
| `AgentState` | agent state data | P3 | planned | **not offered by this sim** (confirmed 2026-08-14) |
| `HomeLocation` | home location operations | P4 | planned | offered by the sim (confirmed 2026-08-14). Writes — it *sets* home — so not something to exercise without asking |
| `UpdateAgentInformation` | update agent metadata | P4 | planned | offered by the sim (confirmed 2026-08-14); a write, so later and by request |
| `UserInfo` | user/account info | P3 | planned | later support feature; not probed |

### What the sim actually offers

On 2026-08-14 every `planned` name above was asked for once, against the test
sim, to find out which were unavailable and which were merely unrequested.
**Thirteen of fifteen resolved.** Only `RegionObjects` and `AgentState` did
not.

That is the useful correction: `planned` in this document has mostly meant "we
have not asked", not "the sim cannot". Rows now say which. The distinction
matters because the two need completely different work — one is a client
feature, the other is a dead end.

A resolved capability is still not a reason to use it. Three of the thirteen
(`HomeLocation`, `UpdateAgentInformation`, and the task-inventory updates) are
writes, and stay unexercised until asked for.

## Seed-Cap Requirements

The initial capability layer should support:

1. POSTing LLSD to the seed capability URL.
2. Requesting a named subset rather than the viewer's full list.
3. Storing resolved capability URLs in a typed registry.
4. Logging missing but requested capability names.
5. Graceful behavior when optional capabilities are absent.

## Current Requested Capability Set

`_run_caps_prelude` in `udp/session.py` requests these, and all eleven resolve
against local OpenSim:

- `EventQueueGet`
- `GetObjectCost`
- `GetObjectPhysicsData`
- `SimulatorFeatures`
- `FetchInventoryDescendents2`
- `FetchInventory2`
- `UploadBakedTexture`
- `ViewerAsset`
- `GetMesh`
- `GetMesh2`
- `GetTexture`

`NewFileAgentInventory` and the task-inventory update capabilities are resolved
on demand by their own commands rather than in the prelude, so a session that
never uploads never asks for them.

The list stays deliberately narrower than a full viewer's. Expand only when a
feature requires it.

## Notes

- Do not mirror the official viewer's full capability list by default.
- A capability being resolvable does not mean Vibestorm should depend on it yet.
- Keep capability use cases documented so later agents understand why each one exists.

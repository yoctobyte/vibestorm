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
| `FetchLib2` | fetch library items | P3 | planned | not requested in the seed-cap list |
| `FetchLibDescendents2` | fetch library descendants | P3 | planned | not requested in the seed-cap list |
| `NewFileAgentInventory` | upload/create inventory assets | P4 | verified | `caps/asset_upload_client.py`; `./run.sh upload-smoke` confirmed a live round trip |
| `UpdateScriptTask` / `UpdateScriptTaskInventory` / `UpdateNotecardTaskInventory` | update object task inventory | P3 | used | `caps/task_inventory_upload_client.py` issues the requests; never confirmed against a running sim, because doing so writes scripts into an in-world object and needs the sim owner's consent. OpenSim registers the script cap under both names — `UpdateScriptTask` and, marked `//legacy` in `BunchOfCaps`, `UpdateScriptTaskInventory` — and the client now asks for both, current name first |
| `RequestTaskInventory` | inspect task inventory | P3 | verified | UDP message plus xfer assembly, not a capability; listed here for completeness |
| `UploadBakedTexture` | upload baked avatar textures | P2 | verified | five baked J2K blobs uploaded per session; appearance accepted |
| `GetTexture` | fetch texture assets | P1 | verified | region map tiles and object textures, cached as PNG |
| `GetMesh` / `GetMesh2` | fetch mesh assets | P1 | verified | `.llmesh` fetch and decode into renderer geometry |
| `ViewerAsset` | generic asset fetch | P2 | resolved | requested and resolved; no client issues requests against it yet |

## Phase 4 World/Rendering Relevant Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `RegionObjects` | object/region data path | P2 | planned | evaluate when object UDP coverage is insufficient |
| `RenderMaterials` | materials data | P4 | planned | the per-face material *UUIDs* now decode from `ExtraParams` (`0x80`), but the material assets themselves are not fetched |
| `ObjectMedia` | media metadata | P4 | planned | not early-scope |
| `ObjectMediaNavigate` | media navigation | P4 | planned | not early-scope |
| `GetObjectCost` | land impact or cost-style data | P4 | planned | optional later |
| `GetObjectPhysicsData` | physics-related object data | P4 | planned | optional later |

## Session and Account Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `AgentPreferences` | account/session preferences | P3 | planned | later usability work |
| `AgentState` | agent state data | P3 | planned | later inspection/support |
| `HomeLocation` | home location operations | P4 | planned | not early-scope |
| `UpdateAgentInformation` | update agent metadata | P4 | planned | later feature |
| `UserInfo` | user/account info | P3 | planned | later support feature |

## Seed-Cap Requirements

The initial capability layer should support:

1. POSTing LLSD to the seed capability URL.
2. Requesting a named subset rather than the viewer's full list.
3. Storing resolved capability URLs in a typed registry.
4. Logging missing but requested capability names.
5. Graceful behavior when optional capabilities are absent.

## Current Requested Capability Set

`_run_caps_prelude` in `udp/session.py` requests these, and all nine resolve
against local OpenSim:

- `EventQueueGet`
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

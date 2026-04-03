# Capability Coverage

Timestamp: 2026-04-02T16:47:02Z

This document tracks which simulator capabilities matter for Vibestorm and when they should be implemented.

## Status Scale

- `planned`: known requirement, not started
- `resolved`: capability name is requested and resolved from seed caps
- `used`: client issues requests against the capability
- `verified`: behavior covered by tests or live-session evidence

## Phase 1-2 Core Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `EventQueueGet` | control-plane long-poll event stream | P0 | planned | mandatory for practical session handling |
| `SimulatorFeatures` | discover simulator feature flags | P1 | planned | useful after seed-cap fetch works |

## Phase 3 Inventory-Oriented Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `FetchInventory2` | fetch inventory items | P2 | planned | inventory phase |
| `FetchInventoryDescendents2` | fetch inventory folders/children | P2 | planned | inventory phase |
| `FetchLib2` | fetch library items | P3 | planned | later inventory support |
| `FetchLibDescendents2` | fetch library descendants | P3 | planned | later inventory support |
| `NewFileAgentInventory` | upload/create inventory assets | P4 | planned | not early-scope |
| `RequestTaskInventory` | inspect task inventory | P3 | planned | later object/task support |

## Phase 4 World/Rendering Relevant Capabilities

| Capability | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `RegionObjects` | object/region data path | P2 | planned | evaluate when object UDP coverage is insufficient |
| `RenderMaterials` | materials data | P4 | planned | not needed for bounding-box rendering |
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

## Initial Requested Capability Set

The first capability request set should stay narrow:

- `EventQueueGet`
- `SimulatorFeatures`

Expand only when a feature requires more.

## Notes

- Do not mirror the official viewer's full capability list by default.
- A capability being resolvable does not mean Vibestorm should depend on it yet.
- Keep capability use cases documented so later agents understand why each one exists.

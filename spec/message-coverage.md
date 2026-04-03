# Message Coverage

Timestamp: 2026-04-02T16:47:02Z

This document tracks which UDP message-system messages matter for the first stages of Vibestorm and what level of support each has.

## Status Scale

- `planned`: known requirement, not started
- `parse-only`: message can be identified and decoded enough for inspection
- `handled`: message participates in client behavior
- `verified`: behavior covered by fixtures, tests, or live session evidence

## Phase 1-2 Critical Messages

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `UseCircuitCode` | establish simulator circuit | P0 | handled | outbound builder and inbound semantic parse implemented |
| `CompleteAgentMovement` | finish avatar presence bootstrap | P0 | handled | outbound builder implemented and sent in local handshake probe |
| `AgentMovementComplete` | simulator confirms movement completion | P0 | handled | semantic parse implemented and observed from local OpenSim |
| `RegionHandshake` | simulator sends region/session metadata | P0 | handled | semantic parse implemented and observed from local OpenSim |
| `RegionHandshakeReply` | acknowledge region handshake | P0 | handled | outbound builder implemented |
| `AgentUpdate` | steady-state agent control/update traffic | P0 | verified | periodic send path implemented and verified in 60-second local OpenSim session |
| `StartPingCheck` | ping/health mechanism | P0 | handled | semantic parse implemented |
| `CompletePingCheck` | ping response | P0 | verified | semantic parse and outbound builder implemented and observed in 60-second local session |
| `PacketAck` | explicit ACK transport support | P0 | verified | explicit outbound ACK support implemented and verified against local OpenSim |
| `AgentThrottle` | viewer bandwidth preferences | P1 | planned | likely next viewer-normal transport message |

## Phase 3 Text/2D Messages

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `ChatFromSimulator` | receive nearby chat/system chat | P1 | planned | part of first useful text client |
| `ImprovedInstantMessage` | IM/event-style message path | P1 | planned | likely later than nearby chat |
| `CoarseLocationUpdate` | coarse avatar positions | P1 | parse-only | observed repeatedly in stable local session |
| `AvatarAnimation` | avatar state hints | P2 | parse-only | observed in stable local session |
| `SimulatorViewerTimeMessage` | region time/environment hints | P2 | parse-only | observed repeatedly in stable local session |
| `AlertMessage` | user-visible server alerts | P1 | planned | useful for error visibility |

## Phase 4 Object/World Messages

| Message | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| `ObjectUpdate` | object state/update path | P1 | parse-only | observed in stable local OpenSim session |
| `ObjectUpdateCached` | cached object updates | P2 | planned | may follow base object path |
| `ImprovedTerseObjectUpdate` | compact frequent updates | P1 | planned | important once object stream works |
| `KillObject` | remove object from world cache | P1 | planned | required for accurate world state |
| `AvatarAppearance` | avatar appearance metadata | P3 | planned | not needed for bounding-box phase |
| `ParcelProperties` | parcel metadata | P2 | planned | useful for 2D/text context |

## Transport and Template Work

These are not user-facing messages but must exist before many handlers are useful.

| Item | Purpose | Priority | Status | Notes |
| --- | --- | --- | --- | --- |
| packet header parser | decode flags, sequence, header length | P0 | verified | implemented with ACK trailer split tests and packet builder round-trip |
| zerocode decoder | expand zerocoded packets | P0 | verified | implemented with simple and wrapped zero-run tests |
| reliable ACK tracking | support reliable transport semantics | P0 | verified | explicit ACK send path and duplicate suppression verified in local session |
| `message_template.msg` loader | map IDs and fields to messages | P0 | verified | template summaries load from canonical artifact |
| message-number decoder | resolve variable-length message IDs by packet frequency | P0 | verified | high, medium, low, and fixed message numbers tested |
| message dispatcher | route decoded messages to handlers | P0 | handled | live OpenSim UDP replies decode to named messages |

## Implementation Order

Recommended order:

1. packet header parser
2. zerocode support
3. template loader
4. `UseCircuitCode`
5. `CompleteAgentMovement`
6. `RegionHandshake` and reply
7. ping handling
8. `AgentUpdate`
9. `AgentThrottle`
10. coarse location and chat
11. object update family

## Notes

- Do not treat coverage as complete because a message name is recognized.
- `handled` should mean the message changes client state or causes the correct response.
- `verified` should require either test fixtures or live capture evidence.

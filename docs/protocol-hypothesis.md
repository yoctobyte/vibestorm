# Protocol Hypothesis

Date: 2026-04-03

This document is a compact hypothesis map for the SL/OpenSim protocol behavior that Vibestorm is
currently targeting.

It is deliberately not a byte-accurate spec.

Use it for:

- quick orientation
- working assumptions during reverse-engineering
- connecting live captures with public docs and source-history clues

Use [`reverse-engineered-protocol.md`](/home/rene/vibestorm/docs/reverse-engineered-protocol.md)
for the more careful field-by-field reference.

## Evidence Grades

- `confirmed-here`: implemented in Vibestorm and exercised by tests or local captures
- `documented-elsewhere`: stated in SL/OpenSim public docs or source snippets, but not fully
  proven by our own captures
- `presumed`: strong synthesis from multiple clues
- `guessed`: plausible but still weak

## Core Model

Current hypothesis:

- UDP transport carries one message per packet with reliability, zerocode, and ACK trailer support
- login/bootstrap yields the first simulator circuit
- the simulator maintains a per-viewer interest list
- first object subscription tends to use richer object-update paths
- later state churn tends to use terse or other compact update families
- disappearance from the viewer is part of interest-list unsubscribe behavior, not only true object deletion

Confidence:

- transport framing: `confirmed-here`
- interest-list lifecycle: `documented-elsewhere`
- richer-first then compact-followup: `presumed`

## Update Family Roles

### `ObjectUpdate`

Hypothesis:

- rich full-state object introduction
- richer refresh path when the simulator decides full data is required
- avatar names and richer metadata often arrive here

Evidence:

- Vibestorm decodes stable `prim_basic` and `avatar_basic` variants from live data
- the SL wiki documents the large outer layout and many semantic fields

Confidence:

- outer framing and first local variants: `confirmed-here`
- broader field catalog: `documented-elsewhere`

### `ImprovedTerseObjectUpdate`

Hypothesis:

- partial-update lane for compact transform/state traffic
- favored when the object is already known to the viewer
- may optionally carry compact texture/appearance information

Evidence:

- SL wiki documents the outer structure only: `RegionData` plus repeated `ObjectData { Data, TextureEntry }`
- viewer-facing docs label `OUT_TERSE_IMPROVED` as a partial update path
- one earlier local session showed terse traffic dominating over `ObjectUpdate`
- a later session showed none, which suggests simulator policy/configuration affects whether terse is used
- current Vibestorm correlation strongly suggests `Data[0:4]` is little-endian `local_id`
- OpenSim `LLClientView` source-history snippets show `CreateImprovedTerseBlock(...)` in the send path

Current inner payload guess:

- `Data[0:4]`: `U32 local_id`
- remaining bytes: compact transform / velocity / state payload
- `TextureEntry`: optional compact appearance override data

Confidence:

- outer framing: `confirmed-here`
- partial-update role: `presumed`
- `local_id` at `Data[0:4]`: `presumed`
- remainder layout: `guessed`

### `ObjectUpdateCached`

Hypothesis:

- cache-oriented refresh for objects already known to the viewer
- likely references cached rich object state via `local_id` and `CRC`

Documented layout:

- `ID`
- `CRC`
- `UpdateFlags`

Confidence:

- outer framing: `documented-elsewhere`
- practical role in our current OpenSim sessions: `presumed`

### `ObjectUpdateCompressed`

Hypothesis:

- another compact update family
- may be relevant in some simulator/viewer combinations even if it is rare in current local sessions

Documented layout:

- `UpdateFlags`
- compact `Data`

Confidence:

- outer framing: `documented-elsewhere`
- practical role in our current OpenSim sessions: `guessed`

### `ObjectKill` / `KillObject`

Hypothesis:

- tells the viewer to forget objects that are no longer in the current interest set
- can represent true deletion, culling, or unsubscribe from visible state
- may batch multiple local IDs in one packet

Evidence:

- SL culling docs frame object removal as part of interest-list unsubscribe behavior
- OpenSim source-history notes say `SendKillObject` was changed to send multiple local IDs in one packet
- OpenSim `LLClientView` snippets show an `m_killRecord` used to stop late updates after a kill
- viewer release notes mention warnings about unknown local IDs in the kill handler

Confidence:

- lifecycle role: `presumed`
- multi-ID batching: `presumed`
- exact current wire layout in our stack: `guessed`

## Interest Management Hypothesis

Presumed behavior:

- the simulator decides what matters to the viewer based on camera/avatar/frustum and prioritization policy
- initial subscription tends to need richer object state
- later updates can use terse, cached, or other compact paths
- culling or reprioritization can suppress entire message families in some sessions

OpenSim clues:

- `[InterestManagement] UpdatePrioritizationScheme`
- `[InterestManagement] ObjectsCullingByDistance`
- OpenSim `LLClientView` snippets show these concerns in the update send loop

Implication for Vibestorm:

- absence of terse traffic in one session is not enough to call the client wrong
- session-to-session differences may be simulator-policy differences

Confidence: `presumed`

## Best Current Explanation For Missing Scene Coverage

Most likely explanations:

1. many scene changes arrive on `ImprovedTerseObjectUpdate` when OpenSim chooses that path
2. some objects arrive on `ObjectUpdateCached` or another compact family we do not yet decode
3. interest management and culling can prevent us from receiving some objects in quieter sessions
4. richer `ObjectUpdate` tails still hide useful semantics, but that is probably not the main census gap now

## What The Reference Implementations Likely Tell Us

OpenSim server source should answer:

- where update family selection happens
- when terse vs full vs cached is chosen
- how `KillObject` batching works
- which config knobs influence the selection

Viewer source should answer:

- exact inner terse `Data` decode layout
- whether terse `TextureEntry` follows the same conventions as rich updates
- cache semantics for `ObjectUpdateCached`
- exact object-table behavior when kill messages reference unknown local IDs

## Working Rules

- keep storing sessions instead of deleting history
- compare sessions rather than treating one run as representative
- use public docs as structural clues, not final truth
- use reference implementations as the next best source after captures
- promote a hypothesis into the detailed reverse-engineered doc only after a stable correlation exists

## Sources

- [Second Life Protocol Index](https://wiki.secondlife.com/wiki/Protocol)
- [Second Life Packet Layout](https://wiki.secondlife.com/wiki/Packet_Layout)
- [Second Life Circuit](https://wiki.secondlife.com/wiki/Circuit)
- [Second Life Packet Accounting](https://wiki.secondlife.com/wiki/Packet_Accounting)
- [ObjectUpdate](https://wiki.secondlife.com/wiki/ObjectUpdate)
- [ImprovedTerseObjectUpdate](https://wiki.secondlife.com/wiki/ImprovedTerseObjectUpdate)
- [ObjectUpdateCached](https://wiki.secondlife.com/wiki/ObjectUpdateCached)
- [ObjectUpdateCompressed](https://wiki.secondlife.com/wiki/ObjectUpdateCompressed)
- [Update Type](https://wiki.secondlife.com/wiki/Update_Type)
- [Culling](https://wiki.secondlife.com/wiki/Culling)
- [OpenSim.ini](https://opensimulator.org/wiki/Configuration/files/OpenSim/OpenSim.ini)
- [OpenSim 0.9.0.0 release notes](https://opensimulator.dev/wiki/0.9.0.0_Release)
- [message_template.msg feedback thread](https://feedback.secondlife.com/server-bugs/p/documentation-message-templatemsg)
- [OpenSim source-history snippet with `SendKillObject` note](https://gist.github.com/lkalif/544ba3685dca6b67a83e)

# Second Life Protocol Research

Date: 2026-04-02

This document captures the current protocol conclusions for this project's first phase: login, simulator connection, and enough world state to support a text/2D client and a later bounding-box 3D renderer.

## Executive Conclusion

Second Life is not a single protocol. A practical client needs three pieces working together:

1. Login/session bootstrap.
2. UDP message-system communication with a simulator.
3. HTTPS capability calls, especially `EventQueueGet`.

The protocol is mature and documented well enough to build against, but the implementation is layered and old. The lowest-risk MVP is:

1. Obtain a valid login/session response.
2. Open the first simulator circuit over UDP.
3. Complete avatar presence with `UseCircuitCode` and `CompleteAgentMovement`.
4. Start `EventQueueGet` polling.
5. Decode a narrow set of incoming UDP messages needed for chat, region metadata, coarse positions, and object bounding boxes.

## Protocol Conclusions

### 1. Login is a bootstrap step, not the whole client

The Second Life wiki's current login documentation still describes an HTTP XML-RPC login request that returns the critical bootstrap values for the first simulator:

- `agent_id`
- `session_id`
- `secure_session_id`
- `circuit_code`
- simulator IP/port
- region coordinates
- `seed_capability`

The older viewer-authentication documentation shows Linden Lab moved account authentication toward a web flow that hands the viewer a login token, while the viewer still completes login using the established viewer protocol shape.

Inference: for a modern client, account auth and simulator bootstrap should be treated as separate concerns. The viewer-facing client contract still needs the simulator bootstrap data above, regardless of how the user credential step is fronted.

### 2. Simulator traffic is still UDP message-system based

The simulator connection is a custom UDP protocol with:

- sequence numbers
- optional reliable delivery
- appended ACKs
- optional zerocoding compression
- a message ID that is decoded using `message_template.msg`

Important packet facts from the official documentation:

- byte 0 contains flags, including zerocode, reliable, resent, and ACK
- bytes 1-4 are the big-endian sequence number
- byte 5 is extra-header length
- packet body then carries exactly one message
- reliable ACKs can be appended at the end of a packet

For implementation, `message_template.msg` is the canonical schema artifact. Linden Lab publishes it separately in the `secondlife/master-message-template` repository.

### 3. Initial region bootstrap is a specific handshake

The minimum happy-path handshake after login is:

1. Login response gives simulator address, `circuit_code`, IDs, and `seed_capability`.
2. Viewer sends `UseCircuitCode` to the simulator over UDP.
3. Viewer sends `CompleteAgentMovement`.
4. Simulator responds with `AgentMovementComplete` and `RegionHandshake`.
5. Viewer sends `RegionHandshakeReply`.
6. Viewer begins steady `AgentUpdate` traffic.
7. Viewer also starts `EventQueueGet` polling using the seed capability tree.

This is the first cut that should count as "connected to the network" for this repo.

### 4. Capabilities are mandatory, not optional extras

The login response includes a seed capability. The viewer posts an LLSD request to that seed URL to obtain named capabilities such as:

- `EventQueueGet`
- `FetchInventory2`
- `FetchInventoryDescendents2`
- `RenderMaterials`
- `RegionObjects`
- `SimulatorFeatures`

The official viewer source still requests a large capability set, and `EventQueueGet` is central. Without it, the wiki notes the client cannot properly handle neighbor regions or teleports.

Implementation consequence: do not design stage 1 as UDP-only. A viable client needs both UDP and capability HTTP support from the start.

### 5. `EventQueueGet` is a long-poll event channel

`EventQueueGet` is not a one-shot request. The client repeatedly POSTs LLSD with:

- `ack`
- `done`

The simulator may hold the request open for roughly 20-30 seconds. A `502 Upstream error` with an empty-style upstream-error body is documented as a normal "no events right now" response. The client must keep polling until a true stop condition such as `404`.

This channel carries key control-plane events including:

- `EnableSimulator`
- `CrossedRegion`
- `TeleportFinish`

That makes it part of the core connection logic, not a later feature.

### 6. Neighbor regions and 3D rendering can be staged cleanly

For the stated project goals, a minimal world/view pipeline does not need full fidelity. The protocol research suggests a sensible rendering progression:

1. Establish main region connection.
2. Decode region metadata and agent movement state.
3. Track neighbor-region discovery through `EnableSimulator`.
4. Decode object update messages enough to extract position, rotation, scale, and IDs.
5. Render every object as an axis-aligned or oriented bounding box first.

This is realistic because the viewer can become useful long before mesh, materials, avatar appearance, or media support exist.

### 7. Inventory should be treated as a later capabilities-based subsystem

Inventory is not a good first target for stage 1. Current capability names in the official viewer include:

- `FetchInventory2`
- `FetchInventoryDescendents2`
- `FetchLib2`
- `FetchLibDescendents2`
- `NewFileAgentInventory`
- several inventory update capabilities

Conclusion: inventory is feasible, but it sits on top of a stable connected session. It should follow login/connect/world-state, not precede it.

## Recommended Build Order

### Stage 1: Connection Core

- Login bootstrap client
- UDP packet encoder/decoder
- Reliable/ACK handling
- Zerocode encode/decode
- `UseCircuitCode`
- `CompleteAgentMovement`
- `AgentUpdate`
- `StartPingCheck` handling
- Seed capability fetch
- `EventQueueGet` long-poller

### Stage 2: Text/2D Client

- Chat path
- Region/parcel metadata
- Coarse avatar positions
- Basic object listing
- Console or lightweight 2D map view

### Stage 3: Bounding-Box 3D

- Object update decoding
- Transform extraction
- Primitive/object cache
- Simple 3D renderer that draws boxes only

### Stage 4: Inventory

- Inventory fetch via capabilities
- Folder/item model
- Read-only inventory view first

## Concrete Next Step For This Repo

The next implementation task should be:

Build a protocol-core workspace around the official `message_template.msg`, with code organized around:

- login bootstrap
- UDP circuit/session state
- message-template driven packet decoding
- capability client
- event-queue loop

Because this repository does not yet declare a language/runtime, I did not hard-commit the codebase to Rust, Python, or another stack in this pass. The repository now includes a script to fetch the canonical protocol artifacts so the next implementation step can start from pinned inputs instead of ad hoc copies.

## Sources

- Second Life official viewer repository: https://github.com/secondlife/viewer
- Official viewer releases page on GitHub, showing latest release `release/2026.01` on 2026-03-03: https://github.com/secondlife/viewer
- Current login protocols: https://wiki.secondlife.com/wiki/Current_login_protocols
- Viewer authentication background: https://wiki.secondlife.com/wiki/Viewer_Authentication
- Packet layout: https://wiki.secondlife.com/wiki/Packet_Layout
- Message format overview: https://wiki.secondlife.com/wiki/Message_Layout
- UseCircuitCode: https://wiki.secondlife.com/wiki/UseCircuitCode
- CompleteAgentMovement: https://wiki.secondlife.com/wiki/CompleteAgentMovement
- AgentMovementComplete: https://wiki.secondlife.com/wiki/AgentMovementComplete
- RegionHandshakeReply: https://wiki.secondlife.com/wiki/RegionHandshakeReply
- Current sim capabilities: https://wiki.secondlife.com/wiki/Current_Sim_Capabilities
- Capabilities overview: https://wiki.secondlife.com/wiki/Capabilities
- EventQueueGet: https://wiki.secondlife.com/wiki/EventQueueGet
- Canonical message template repository: https://github.com/secondlife/master-message-template

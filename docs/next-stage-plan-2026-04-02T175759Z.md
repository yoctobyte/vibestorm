# Next Stage Plan

Timestamp: 2026-04-02T17:57:59Z

## Stage Name

Durable Session Core

## Stage Goal

Move Vibestorm from successful one-shot bootstrap and handshake probes to a stable connected viewer session that remains present in-region and maintains the simulator relationship over time.

This stage ends when Vibestorm can:

- log in through XML-RPC
- resolve seed capabilities
- send `UseCircuitCode`
- send `CompleteAgentMovement`
- send `RegionHandshakeReply`
- maintain periodic `AgentUpdate`
- answer simulator pings
- keep the UDP session alive long enough to observe repeated live world traffic

## Why This Is The Right Next Stage

The project has already crossed the threshold from static planning to real network interaction.

What is missing now is not protocol discovery but session durability:

- the simulator responds
- the client does not yet behave like a continuously present viewer
- object and world-state work will stay brittle until the session loop is real

So the next stage should focus on keeping the connection alive before expanding coverage breadth.

## Stage Deliverables

### 1. Session Object

Implement a long-lived session component that owns:

- login bootstrap result
- UDP socket
- sequence counter
- pending ACK state
- simulator address
- resolved capabilities
- session lifecycle flags

Exit condition:

- there is one obvious in-code owner of live connection state

### 2. Live Handshake Completion

Implement the missing live handshake actions:

- send `RegionHandshakeReply`
- record `RegionHandshake`
- record `AgentMovementComplete`

Exit condition:

- the handshake is not just observed, it is answered correctly by Vibestorm

### 3. Keepalive Loop

Implement:

- `StartPingCheck` handling
- `CompletePingCheck` response
- periodic `AgentUpdate`

Exit condition:

- the simulator continues to treat the client as present for a sustained session window

### 4. Reliable Transport Basics

Implement:

- packet sequence advancement
- outbound reliable packet tracking
- appended ACK collection
- inbound ACK processing

Exit condition:

- the client can explain which packets are outstanding and which have been acknowledged

### 5. Session Logging

Add structured logging for:

- bootstrap values
- capability resolution
- handshake transitions
- ping traffic
- ACK flow
- disconnect reasons

Exit condition:

- session failures can be diagnosed from logs without packet guessing

## Recommended Work Order

1. create `session` or equivalent live-connection owner
2. move UDP probing into that session object
3. add `RegionHandshakeReply` send path
4. add periodic `AgentUpdate`
5. add ping handling
6. add reliable ACK bookkeeping
7. run sustained local OpenSim session tests

## Scope Boundaries

This stage should include:

- connection durability
- presence durability
- core live transport state

This stage should not include:

- inventory
- renderer work
- full object decoding
- UI expansion
- Linden-hosted grid work

## Success Criteria

The stage is complete when all of these are true:

1. local OpenSim login and handshake succeed through Vibestorm
2. the client remains connected for a sustained interval
3. repeated `AgentUpdate` traffic is sent
4. ping traffic is answered
5. live world messages continue arriving after initial handshake
6. logs clearly show the session lifecycle

## Suggested Acceptance Test

Run a local OpenSim session for at least 60 seconds and confirm:

- no immediate disconnect
- repeated UDP traffic continues
- no fatal parser errors
- no unbounded growth in pending ACK state
- session logs show stable presence

## Risks

- `AgentUpdate` cadence may need tuning for OpenSim expectations
- ACK handling may be necessary earlier than expected for stable presence
- the current one-shot probe code may need refactoring rather than incremental patching

## Mitigations

- keep the new session loop separate from pure parser/builder code
- retain one-shot probes as debugging tools even after the session object exists
- verify each sub-step locally against OpenSim before broadening message coverage

## Immediate Coding Targets

- `RegionHandshakeReply` builder integration
- `AgentUpdate` builder
- long-lived UDP socket wrapper
- session sequence counter
- pending reliable packet table
- inbound packet loop

## Handoff Guidance

If work stops mid-stage, the next agent should inherit:

- a single session entry point
- a clear session state enum or lifecycle model
- logs that show the last successful transition
- one command that exercises the live local session path

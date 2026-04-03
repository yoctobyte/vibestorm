# Vibestorm Execution Plan

Timestamp: 2026-04-02T16:43:11Z

This document turns the high-level plan into a concrete execution sequence with dependencies, checkpoints, and handoff-ready work units.

## Planning Goals

This execution plan is optimized for:

- short, restartable implementation bursts
- explicit ownership boundaries
- low-conflict parallel work
- agent handoff without hidden context

## Critical Path

The critical path for a usable first client is:

1. choose runtime and scaffold project
2. define core data models
3. implement login bootstrap
4. implement UDP circuit
5. implement initial region handshake
6. implement capability resolution and `EventQueueGet`
7. expose text/2D observability

Anything not serving that path should be treated as secondary.

## Work Breakdown

### Work Package A: Runtime Selection

Objective:

- choose the implementation language and baseline toolchain

Required output:

- one short decision document
- one initial scaffold

Decision criteria:

- async UDP support
- async HTTP support
- binary parsing ergonomics
- test ergonomics
- dependency stability
- readability for cross-agent collaboration
- ease of generating small protocol tools

Exit condition:

- a fresh contributor can build the project with one documented command

### Work Package B: Project Skeleton

Objective:

- establish stable module boundaries before protocol implementation starts

Required output:

- `src/`
- `test/`
- `spec/`
- top-level README
- build/run/test commands

Required modules:

- `login`
- `udp`
- `caps`
- `event_queue`
- `world`
- `app`

Exit condition:

- the scaffold builds and module boundaries are visible in code

### Work Package C: Shared Models

Objective:

- prevent protocol code from leaking raw formats directly into higher layers

Required output:

- session model
- region model
- agent model
- transport event model
- world object model

Rules:

- IDs should have explicit types where reasonable
- transport-layer structs must remain distinct from world-state structs
- logging views must be redacted where secrets exist

Exit condition:

- login, UDP, and capability code can depend on shared models without circular design

### Work Package D: Login Bootstrap

Objective:

- obtain simulator bootstrap data safely and reproducibly

Required output:

- config file format
- environment variable handling
- login request builder
- login response parser
- error mapping

Open issues to capture while implementing:

- exact current login endpoint shape for the intended grid
- required headers and viewer identification
- fallback behavior if web-auth is needed before viewer bootstrap

Exit condition:

- bootstrap data is parsed into the shared session model

### Work Package E: UDP Foundation

Objective:

- own the packet lifecycle before handling many message types

Required output:

- packet header parser
- packet encoder
- ACK queue
- resend policy
- zerocode codec
- message-template loader

Rules:

- keep packet parsing deterministic
- treat malformed packets as typed errors, not silent drops
- separate transport mechanics from message semantics

Exit condition:

- a packet can be parsed, acknowledged, optionally decompressed, and decoded to a message descriptor

### Work Package F: Connection Handshake

Objective:

- reach the first stable simulator-connected state

Required output:

- `UseCircuitCode`
- `CompleteAgentMovement`
- `RegionHandshake` handling
- `RegionHandshakeReply`
- basic keepalive loop

Exit condition:

- the client can connect, remain present, and log core region/session state

### Work Package G: Capabilities and Event Queue

Objective:

- complete the modern session control plane

Required output:

- seed capability resolver
- LLSD serialization/deserialization support
- `EventQueueGet` long-poller
- event dispatcher

Rules:

- capability failures must be observable in logs
- long-poll reconnect behavior must be explicit and tested

Exit condition:

- control-plane events arrive in a shared event stream

### Work Package H: Text/2D MVP

Objective:

- make the project useful before 3D exists

Required output:

- session log view
- region summary view
- avatar coarse-position view
- chat path
- object count or summary output

Exit condition:

- a user can log in and understand what region/session state the client sees

## Parallel Work Opportunities

These tasks can be worked in parallel once the scaffold exists:

- message-template parser
- LLSD support
- config and secret-loading model
- world-state model drafting
- protocol fixture collection

These tasks should not start in parallel with architecture rework:

- renderer work
- inventory work
- UI framework selection beyond minimal text/2D needs

## Acceptance Checkpoints

### Checkpoint 1

- runtime chosen
- project scaffold exists
- build command documented

### Checkpoint 2

- login bootstrap works against a controlled target
- secrets are redacted in logs

### Checkpoint 3

- UDP transport handles headers, ACKs, zerocode, and template-driven message lookup

### Checkpoint 4

- client reaches stable simulator presence

### Checkpoint 5

- `EventQueueGet` loop is stable
- text/2D output is useful

## Risks

- login details may have drifted from older public docs
- protocol docs are partly historical and may omit current practical constraints
- capability behavior may require more LLSD detail than expected
- object update decoding may be broader than the initial box-render target suggests

## Mitigations

- keep fetchable official artifacts in `third_party/`
- document every ambiguity in repo, not in chat
- prefer narrow integration milestones over broad code generation
- keep test fixtures for packets and capability payloads as soon as real samples appear

## Immediate Planning Follow-Up

The next planning artifact should be a language selection record that picks one runtime and rejects the main alternatives with concrete reasons.

# Project State

Last updated: 2026-04-03

## Current Summary

Vibestorm has moved past pure planning and basic scaffolding into live local OpenSim integration.

Confirmed working now:

- local OpenSim can be launched from the repo
- Vibestorm can log in against local OpenSim
- seed capability resolution works
- `EventQueueGet` polling works for the current local empty-state path
- UDP handshake probing works
- a first durable-session loop exists in code

User-confirmed status:

- logging in appears to work

## What Has Been Done

### Protocol and bootstrap groundwork

- login/bootstrap models and XML-RPC login client are implemented
- capability resolution is implemented
- LLSD helpers are implemented
- event queue polling client is implemented
- UDP packet parsing and packet building are implemented
- message template loading and dispatch are implemented
- semantic parsing/builders exist for the current handshake-era messages
- zerocode decode and encode support are implemented

### Local OpenSim setup

- a local OpenSim runtime is bundled under `local/opensim/runtime`
- local config and region files were materialized for repeatable testing
- a repeatable local avatar exists for testing
- project docs capture the verified local host details and working commands

### Durable session slice

- a `LiveCircuitSession` exists in `src/vibestorm/udp/session.py`
- the session sends `UseCircuitCode` and `CompleteAgentMovement`
- the session handles `RegionHandshake` and sends `RegionHandshakeReply`
- the session answers `StartPingCheck` with `CompletePingCheck`
- the session sends periodic `AgentUpdate` after `AgentMovementComplete`
- the session tracks reliable outbound packets and inbound ACKs
- the CLI now exposes a bounded `session-run` path

### Developer ergonomics

- `run.sh` was added at the repo root as the main testing wrapper
- `run.sh` supports:
  - `opensim`
  - `bootstrap`
  - `caps`
  - `eventq`
  - `udp`
  - `handshake`
  - `session`
  - `test`
- `opensim.sh` was added at the repo root as a direct OpenSim launcher

### Tests and verification

- unit coverage exists for login, LLSD, event queue, UDP packets, template dispatch, semantic message helpers, session logic, and zerocode handling
- `PYTHONPATH=src python3 -m unittest discover -s test -v` passed during the latest implementation pass
- `python3 -m compileall src test` passed during the latest implementation pass

## Current Working Commands

Start local OpenSim:

```bash
./opensim.sh
```

or:

```bash
./run.sh opensim
```

Run the live client session test:

```bash
./run.sh session
```

Run the test suite:

```bash
./run.sh test
```

## What Is Next

The project is now in the "Durable Session Core" stage. The next priority is not more bootstrap work. It is proving that the session remains alive for a sustained interval and understanding why it fails when it does.

Immediate next tasks:

1. run repeated live `./run.sh session` tests against local OpenSim
2. confirm whether the session remains stable for at least 60 seconds
3. add clearer session logging for:
   - handshake transitions
   - ping traffic
   - ACK flow
   - disconnect reason
4. inspect whether reliable resend bookkeeping is needed beyond current ACK tracking
5. tune `AgentUpdate` cadence if OpenSim drops the session too quickly
6. confirm repeated world traffic continues after handshake, not just the initial burst

## Likely Next Coding Targets

- structured session lifecycle logging
- reliable resend / timeout logic for outbound reliable packets
- better visibility into why the simulator closes or stops responding
- fixture capture for stable live packets
- first-pass `ObjectUpdate` decoding for world-state work

## Out Of Scope For The Immediate Next Step

- inventory support
- renderer work
- UI expansion
- Linden-hosted grid testing
- broad protocol coverage beyond what is needed for a durable local session

## Resume Here

If continuing work from this point, use this order:

1. start OpenSim with `./run.sh opensim`
2. run `./run.sh session`
3. inspect whether the session stays alive for 60 seconds
4. if it fails early, improve logging before expanding protocol breadth
5. once the session is durable, move to packet capture and `ObjectUpdate` decoding

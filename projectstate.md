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
- a durable UDP session can stay alive for 60 seconds against local OpenSim
- experimental client-side cube spawn appears to work against local OpenSim

User-confirmed status:

- logging in works
- local OpenSim session completes a 60-second run with continued simulator traffic
- spawning a test cube through the client appears to work

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
- explicit `PacketAck` sending is implemented
- duplicate reliable inbound packets are detected and not reprocessed semantically
- live session events are streamed through the CLI for debugging
- the CLI now exposes a bounded `session-run` path

### World-view slice

- a UI-agnostic `WorldView` exists in `src/vibestorm/world/models.py`
- session traffic now feeds normalized state for:
  - region info
  - sim stats
  - simulator time
  - coarse agent locations
  - object update summaries
- coarse locations now also populate keyed agent-presence entries by UUID where available
- `session-run` now prints compact world summaries at the end of a run
- `session-run` can optionally send an experimental `ObjectAdd` to spawn a test cube for local debugging

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
- local OpenSim session verification succeeded for a full 60-second run with:
  - `received=94-95`
  - `ping_requests_handled=11`
  - `agent_updates_sent=54-55`
  - `pending_reliable=0`
  - recurring live traffic including `SimStats`, `SimulatorViewerTimeMessage`, and `CoarseLocationUpdate`
  - final world summary including region, sim stats, time, coarse agents, and object update counts
- local OpenSim session verification with experimental cube spawn also succeeded for a full 60-second run with:
  - `received=97`
  - `ping_requests_handled=11`
  - `agent_updates_sent=55`
  - `pending_reliable=0`
  - `ObjectUpdate=3`
  - `PacketAck=3`
  - final world summary showing:
    - `world[region]=Vibestorm Test grid=(1000,1000)`
    - `world[sim_stats]=updates:20 capacity:15000 stats:41`
    - `world[time]=updates:23 sun_phase:0.326 sec_per_day:14400`
    - `world[coarse_agents]=updates:13 count:1`
    - `world[object_update]=events:3 objects:1 region_handle:1099511628032000`

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

The durable-session core is now proven against local OpenSim. The next priority is expanding useful protocol coverage on top of that stable transport base.

Immediate next tasks:

1. move message-to-world application into a dedicated updater/adapter layer
2. expand keyed world entities beyond coarse agent presence
3. deepen `ObjectUpdate` decoding toward identities and positions
4. capture stable live fixtures from the now-working session loop, including with spawned test geometry
5. keep the world model frontend-agnostic for future console/2D/3D/web heads

## Likely Next Coding Targets

- world updater module that applies parsed messages into `WorldView`
- richer keyed agent/entity storage
- fixture capture for stable live packets
- deeper `ObjectUpdate` decoding for world-state work
- normalized object and coarse-avatar models

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
3. confirm the 60-second session remains stable
4. inspect final `world[...]` summary output
5. continue from world-state building rather than transport stabilization

# Local OpenSim Host

Timestamp: 2026-04-02T17:04:52Z

This document records the local OpenSim test-host setup created for Vibestorm.

## Purpose

Use OpenSim as a local compatibility target for early client work.

This is useful for:

- login and bootstrap experiments
- UDP handshake work
- packet capture and replay
- fixture generation
- avoiding risk to real Second Life accounts while the client is immature

It is not the final authority for Linden-hosted compatibility.

## Installed Prerequisites

Verified on this machine:

- `.NET SDK 8.0.125`
- host runtime `8.0.25`
- `libgdiplus`
- `sqlite3`
- `curl`
- `unzip`

## OpenSim Runtime Source

Source:

- GitHub mirror: `opensim/opensim`
- release tag: `r575abd6`
- release name: `LastDotNetAutoBuild`
- published: `2026-03-29T02:23:20Z`
- asset: `LastDotNetBuild.zip`
- expected sha256: `c6375c3256bdf7c307c3de3e2a4e5284e713771f7bb4a63a36207c0da342f636`

Local paths:

- archive: [`local/opensim/LastDotNetBuild.zip`](../local/opensim/LastDotNetBuild.zip)
- runtime root: [`local/opensim/runtime`](../local/opensim/runtime)
- server binary: [`local/opensim/runtime/bin/OpenSim`](../local/opensim/runtime/bin/OpenSim)

## Local Configuration State

Materialized config files:

- [`local/opensim/runtime/bin/OpenSim.ini`](../local/opensim/runtime/bin/OpenSim.ini)
- [`local/opensim/runtime/bin/config-include/StandaloneCommon.ini`](../local/opensim/runtime/bin/config-include/StandaloneCommon.ini)

Generated region file:

- [`local/opensim/runtime/bin/Regions/Regions.ini`](../local/opensim/runtime/bin/Regions/Regions.ini)

Current region values:

- region name: `Vibestorm Test`
- region UUID: `607d469c-9949-45cf-97ee-eec289315d92`
- location: `1000,1000`
- internal address: `0.0.0.0`
- internal port: `9000`
- external host name: `127.0.0.1`

## Runtime Verification

Observed during local run:

- OpenSim started successfully under `.NET 8`
- local SQLite stores were initialized
- LLUDP server started
- TCP listener was present on `0.0.0.0:9000`
- `http://127.0.0.1:9000/get_grid_info` responded with:
  - login URI `http://127.0.0.1:9000/`
  - platform `OpenSim`

Evidence files:

- log: [`local/opensim/runtime/bin/OpenSim.log`](../local/opensim/runtime/bin/OpenSim.log)
- database: [`local/opensim/runtime/bin/OpenSim.db`](../local/opensim/runtime/bin/OpenSim.db)
- asset database: [`local/opensim/runtime/bin/Asset.db`](../local/opensim/runtime/bin/Asset.db)

## Current Repeatable Test Account

Created local avatar:

- first name: `Vibestorm`
- last name: `Admin`
- principal ID: `11111111-2222-3333-4444-555555555555`
- password: set locally via `VIBESTORM_PASSWORD`

This account is now usable for repeatable local Vibestorm testing.

## Start And Stop

Foreground start:

```bash
./tools/start_opensim.sh
```

Expected runtime path:

- login URI: `http://127.0.0.1:9000`

If the host is running, verify with:

```bash
curl http://127.0.0.1:9000/get_grid_info
ss -ltnp | rg ':9000'
```

Shutdown:

- use `Ctrl+C` in the foreground console

## Verified Vibestorm Flows

The following commands were verified against the local OpenSim host:

```bash
PYTHONPATH=src python3 -m vibestorm.app.cli login-bootstrap \
  --login-uri http://127.0.0.1:9000/ \
  --first Vibestorm \
  --last Admin \
  --password "$VIBESTORM_PASSWORD" \
  --start 'uri:Vibestorm Test&128&128&25'
```

```bash
PYTHONPATH=src python3 -m vibestorm.app.cli resolve-seed-caps \
  --login-uri http://127.0.0.1:9000/ \
  --first Vibestorm \
  --last Admin \
  --password "$VIBESTORM_PASSWORD" \
  --start 'uri:Vibestorm Test&128&128&25' \
  EventQueueGet SimulatorFeatures
```

```bash
PYTHONPATH=src python3 -m vibestorm.app.cli event-queue-once \
  --login-uri http://127.0.0.1:9000/ \
  --first Vibestorm \
  --last Admin \
  --password "$VIBESTORM_PASSWORD" \
  --start 'uri:Vibestorm Test&128&128&25'
```

```bash
PYTHONPATH=src python3 -m vibestorm.app.cli handshake-probe \
  --login-uri http://127.0.0.1:9000/ \
  --first Vibestorm \
  --last Admin \
  --password "$VIBESTORM_PASSWORD" \
  --start 'uri:Vibestorm Test&128&128&25'
```

Observed live results include:

- successful XML-RPC login bootstrap
- seed capability resolution
- expected empty `EventQueueGet` poll
- UDP receipt of:
  - `PacketAck`
  - `RegionHandshake`
  - `AgentMovementComplete`
  - `ParcelOverlay`
  - `ObjectUpdate`

## Recommended Next OpenSim Work

1. add a stable session loop that sends `RegionHandshakeReply`
2. add periodic `AgentUpdate`
3. capture redacted packet and capability fixtures from the live local session
4. decode `ObjectUpdate` for world-state building

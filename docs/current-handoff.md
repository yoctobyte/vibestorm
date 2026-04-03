# Current Handoff

Last updated: 2026-04-03

## Summary

The repo is in active local OpenSim protocol reverse-engineering mode.

The current implementation supports:

- XML-RPC login bootstrap
- seed capability resolution
- `EventQueueGet` polling
- UDP handshake and steady-state session traffic
- normalized world-state updates for region, time, coarse agents, and first object entities
- local fixture capture and inventory rebuilding
- default SQLite evidence collection in `local/unknowns.sqlite3`
- nearby chat capture for timestamp correlation

## Current State

What works:

- `./run.sh opensim`
- `./run.sh session`
- `./run.sh session 180 --verbose`
- `./run.sh unknowns`
- `./run.sh fixtures`
- packet/message decoding for the current handshake and first `ObjectUpdate` slices

What is incomplete:

- multi-object `ObjectUpdate` semantic decoding
- deeper object update families such as `ImprovedTerseObjectUpdate`, `ObjectUpdateCached`, and `KillObject`
- full `TextureEntry` decoding
- reliable extraction of ordinary prim names from world traffic

What changed recently:

- object/capture evidence is now written to `local/unknowns.sqlite3`
- nearby chat is stored for timeline correlation
- `unknowns-report` now shows packet-level and entity-level census information
- protocol docs now include struct-style offset/length layouts

## Verification

Most recent local verification in this repo:

- `bash -n run.sh`
- `PYTHONPATH=src python3 -m compileall src test`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_udp_messages.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_udp_session.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_unknowns_db.py' -v`

## Recommended Next Step

Run a fresh real OpenSim session against a clean `local/unknowns.sqlite3`, then inspect:

1. how many `ObjectUpdate` packets were seen
2. how many entities were decoded
3. whether multi-object packets explain missing scene objects
4. whether local chat timestamps align with object/avatar changes

## Notes For The Next Agent

- Treat `local/unknowns.sqlite3` as disposable dev evidence, not as a golden artifact.
- If using the DB for analysis, clear or replace it first so test data does not pollute conclusions.
- Keep `docs/reverse-engineered-protocol.md` current when a field becomes trustworthy.

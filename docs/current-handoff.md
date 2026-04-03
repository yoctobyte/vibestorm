# Current Handoff

Last updated: 2026-04-03

## Summary

The repo is still in active local OpenSim protocol reverse-engineering mode, but the interrupted
desktop session clearly moved the evidence workflow forward beyond what this file previously said.

The current implementation now supports:

- XML-RPC login bootstrap
- seed capability resolution
- `EventQueueGet` polling
- UDP handshake and steady-state session traffic
- normalized world-state updates for region, time, coarse agents, first object entities, and terse object-update summaries
- local fixture capture and inventory rebuilding
- default SQLite evidence collection in `local/unknowns.sqlite3`
- per-session evidence scoping inside the SQLite database
- nearby chat capture for timestamp correlation
- aggregate inbound-message census and unknown UDP dispatch-failure logging

## Current State

What works:

- `./run.sh opensim`
- `./run.sh session`
- `./run.sh session 180 --verbose`
- `./run.sh unknowns`
- `./run.sh fixtures`
- packet/message decoding for the current handshake and first `ObjectUpdate` / `ImprovedTerseObjectUpdate` slices

What is incomplete:

- multi-object `ObjectUpdate` semantic decoding
- semantic decoding of the inner terse payload beyond the first inferred `local_id`
- deeper object update families such as `ObjectUpdateCached` and `KillObject`
- full `TextureEntry` decoding
- reliable extraction of ordinary prim names from world traffic

What changed recently:

- evidence rows are now grouped by recorded session in `local/unknowns.sqlite3`
- `unknowns-report` can now target the latest session, one specific session, or all sessions
- every recognized inbound UDP message is now counted and summarized in the evidence DB
- dispatch failures for unknown inbound UDP messages are now recorded with sequence, preview, and decoded message-number metadata when available
- `ImprovedTerseObjectUpdate` now has a structural parser and is summarized into world/evidence state
- local terse entries now store best-effort `local_id`, payload sizes, texture-entry sizes, and hex previews
- nearby chat is stored for timeline correlation
- `ChatFromSimulator` parsing now trims trailing NUL bytes from names and messages
- protocol docs now include struct-style offset/length layouts
- protocol docs now also record SL/OpenSim public-source clues about interest management, culling, and object-update families
- docs now include `protocol-hypothesis.md` as the clearer named compact working model
- protocol docs now include OpenSim `LLClientView` source-history clues for update-family selection and kill-record handling

## Verification

Most recent local verification in this repo:

- `bash -n run.sh`
- `PYTHONPATH=src python3 -m compileall src test`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_udp_messages.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_udp_session.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_unknowns_db.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_world_updater.py' -v`

Current result after reconstructing the interrupted session:

- `test_udp_messages.py`: passing
- `test_udp_session.py`: passing
- `test_world_updater.py`: passing
- `test_unknowns_db.py`: now passing after fixing one order-dependent assertion in the test itself

## Recommended Next Step

Run a fresh real OpenSim session and inspect the latest recorded session in `local/unknowns.sqlite3`:

1. how many `ObjectUpdate` packets were seen
2. how many `ImprovedTerseObjectUpdate` packets were seen relative to `ObjectUpdate`
3. how many distinct terse `local_id` values correlate with already-known full `ObjectUpdate` entities
4. whether unsupported terse payload structure explains the missing scene-object census
5. whether local chat timestamps align with object/avatar changes

## Notes For The Next Agent

- Keep prior sessions in `local/unknowns.sqlite3` unless there is a specific reason to discard them.
- Use session filtering rather than deletion for routine forensics:
  - default `unknowns-report` output is scoped to the latest session
  - use `--session-id` to inspect one run
  - use `--all` only for cross-session analysis
- If test or synthetic data polluted the main DB, move it aside and start a new DB file instead of deleting useful historical sessions.
- Keep `docs/reverse-engineered-protocol.md` current when a field becomes trustworthy.
- Prefer `docs/protocol-hypothesis.md` when a future agent wants the short version first.
- The most important current protocol lead is that terse entry `Data[0:4]` appears to be little-endian `local_id`.
- External docs now point at OpenSim interest-management and culling settings as a plausible reason that some sessions show terse traffic while others do not.
- OpenSim mirror snippets suggest `KillObject` is not just a simple delete message; it is part of a race-sensitive unsubscribe lifecycle and may batch multiple local IDs.

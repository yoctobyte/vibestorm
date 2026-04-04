# Current Handoff

Last updated: 2026-04-04

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
- semantic decoding of the inner terse payload beyond the current source-backed prim/avatar motion fields
- deeper object update families such as `ObjectUpdateCached`, `ObjectUpdateCompressed`, `ObjectPropertiesFamily`, and `ObjectExtraParams`
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
- local OpenSim source slices now exist under `referencedocs/UDP`, enabling source-derived UDP struct documentation
- source-derived notes now also cover `ChatFromSimulator`, `LayerData`, `ObjectPropertiesFamily`, `AgentUpdate`, `AgentThrottle`, and `ObjectExtraParams`
- `ImprovedTerseObjectUpdate` parsing is now aligned with the source-backed OpenSim prim/avatar layout instead of the earlier preview-only placeholder shape
- `KillObject` is now parser/world/session/SQLite tested end to end, including multi-local-id packets and object removal from `WorldView`
- `ObjectUpdateCached` and `ObjectUpdateCompressed` now emit explicit world/session events and are parser/session/SQLite tested end to end
- `ObjectPropertiesFamily` is now parser/world/session tested and enriches known `WorldObject` entries with the latest properties-family payload
- `ObjectPropertiesFamily` currently tolerates both OpenSim-style short-length UTF-8 strings and template-style byte-length strings because the local source and message template disagree there
- `ObjectExtraParams` now has a standalone parser, emits session/world events, and rich `ObjectUpdate.ExtraParams` is decoded into structured entries using the observed count/type/size/data inner blob shape from a captured sculpt-like prim update
- rich prim `ObjectUpdate.ExtraParams` also needed a 2-byte length-prefixed read in the real capture path; the previous 1-byte assumption was too narrow
- session mode now has optional camera sweep support, exposed in `run.sh` via `VIBESTORM_CAMERA_SWEEP=1`
- `tools/run_session_forensics.sh` now runs a sweep-enabled capture session and appends `./run.sh unknowns` into one timestamped text artifact under `local/session-reports/`
- `tools/capture_viewer_session.sh` now captures one real viewer session into `local/viewer-captures/`; when targeting loopback OpenSim it defaults to `lo` instead of `any`
- a real Firestorm capture against local OpenSim now confirms the pre-UDP bootstrap order: XML-RPC `login_to_simulator`, then seed-cap POST carrying `X-SecondLife-UDP-Listen-Port`, then early CAPS requests including `EventQueueGet` and `SimulatorFeatures`, and only then UDP
- Vibestorm live sessions now perform the same minimum pre-UDP CAPS prelude before `UseCircuitCode`: bind UDP socket, resolve seed caps with the real local UDP port, poll `EventQueueGet` once, fetch `SimulatorFeatures`, then start the UDP circuit
- Vibestorm login requests now also ask for Firestorm-style inventory/bootstrap `options`, and `LoginBootstrap` retains `inventory_root_folder_id`, `current_outfit_folder_id`, `my_outfits_folder_id`, and the initial outfit name/gender from the XML-RPC response
- the CAPS prelude now also resolves `FetchInventoryDescendents2` and performs one startup inventory fetch for the inventory root and Current Outfit Folder when those folder IDs are available from login
- session output now splits terse-only placeholders into avatar vs prim counts via `world[terse_only]=tracked:N avatars:A prims:P`, which makes sweep-session census gaps easier to classify without opening the DB first
- session output now also correlates terse-only avatar placeholders with the nearest coarse agent using horizontal distance (`xy_distance`), which was enough in recent live runs to identify long-lived terse-only local ID `492042976` as the second avatar rather than a missing prim

## Verification

Most recent local verification in this repo:

- `bash -n run.sh`
- `PYTHONPATH=src python3 -m compileall src test`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_udp_messages.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_caps_client.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_inventory_caps_client.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_event_queue_runtime.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_udp_session.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_unknowns_db.py' -v`
- `PYTHONPATH=src python3 -m unittest discover -s test -p 'test_world_updater.py' -v`

Current result after reconstructing the interrupted session:

- `test_udp_messages.py`: passing
- `test_caps_client.py`: passing
- `test_event_queue_runtime.py`: passing
- `test_udp_session.py`: passing
- `test_world_updater.py`: passing
- `test_unknowns_db.py`: now passing after fixing one order-dependent assertion in the test itself

## Recommended Next Step

Validate the widened login/bootstrap + inventory prelude against live OpenSim behavior:

1. run one fresh `./tools/run_session_forensics.sh 90`
2. confirm early session events now include `caps.seed.*`, `caps.event_queue`, `caps.simulator_features`, and `caps.inventory`
3. check whether avatar appearance/world coverage improves relative to the earlier UDP-first runs
4. if cloud/Ruth behavior persists, inspect the `caps.inventory` summary and compare the current outfit contents against the Firestorm capture next

## Notes For The Next Agent

- Keep prior sessions in `local/unknowns.sqlite3` unless there is a specific reason to discard them.
- Use session filtering rather than deletion for routine forensics:
  - default `unknowns-report` output is scoped to the latest session
  - use `--session-id` to inspect one run
  - use `--all` only for cross-session analysis
- If test or synthetic data polluted the main DB, move it aside and start a new DB file instead of deleting useful historical sessions.
- Keep `docs/reverse-engineered-protocol.md` current when a field becomes trustworthy.
- Prefer `docs/protocol-hypothesis.md` when a future agent wants the short version first.
- Use `docs/opensim-udp-reference.md` when the task is “what does the current OpenSim UDP source actually serialize?”
- The old terse guess is now stronger than that: the parser matches the OpenSim source-backed prim/avatar block layout and tests assert the decoded motion fields.
- External docs now point at OpenSim interest-management and culling settings as a plausible reason that some sessions show terse traffic while others do not.
- OpenSim mirror snippets suggest `KillObject` is not just a simple delete message; it is part of a race-sensitive unsubscribe lifecycle and may batch multiple local IDs. That batching is now covered by local tests.

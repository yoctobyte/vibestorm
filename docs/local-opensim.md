# Local OpenSim

This is the current local compatibility target for Vibestorm development.

## Purpose

Use local OpenSim for:

- login/bootstrap experiments
- UDP and capability testing
- packet capture and replay
- object and avatar protocol reverse-engineering
- safe fixture collection without touching a real grid

## Current Local Defaults

- login URI: `http://127.0.0.1:9000/`
- region name: `Vibestorm Test`
- start location: `uri:Vibestorm Test&128&128&25`
- default test avatar: `Vibestorm Admin`
- built-in tester profile avatar: `Vibestorm Tester`
- password: set locally via `VIBESTORM_PASSWORD` or the ignored
  `local/vibestorm-login.env` profile. The built-in `tester` profile does not
  include a password.

Localhost/OpenSim test passwords are allowed in tracked docs or fixtures only
when they are trivial, disposable, and clearly scoped to local testing. Never
track credentials for OSgrid, Second Life, GitHub, hosting providers, or other
real services.

## Main Commands

Start OpenSim:

```bash
./run.sh opensim
```

Run a normal session:

```bash
./run.sh session
```

Run as the local tester profile:

```bash
./run.sh tester session
./run.sh tester upload-smoke
./run.sh tester viewer3d
```

Grid-specific wrapper scripts keep credentials and safety defaults separated:

```bash
./local.sh session       # local OpenSim, default tester profile
./opengrid.sh login      # OSgrid/OpenGrid profile setup
./opengrid.sh session
./sl.sh login            # Second Life profile setup
./sl.sh bootstrap
```

They all delegate to `run.sh`; any command accepted by `run.sh` can be passed
through the wrapper.

Run a longer reverse-engineering session:

```bash
./run.sh session 180 --verbose
```

If no login environment/profile exists and stdin is interactive, `run.sh`
prompts for:

- sim location: `localhost`, `opengrid`, `sl`, or `custom`
- first name
- last name
- password

When accepted, it stores those values in `local/vibestorm-login.env` with mode
`600`. That file is intentionally ignored by git. It is local-file storage for
development convenience, not encrypted OS keyring storage.

Multiple saved credentials use a profile name before the command:

```bash
./run.sh osgrid login
./run.sh osgrid session
./run.sh tester login-show
```

Named profiles are stored as ignored files like
`local/vibestorm-login-osgrid.env` and `local/vibestorm-login-tester.env`.
Explicit `VIBESTORM_*` login env vars still take priority over profile files
and built-in defaults.

`run.sh` also tracks a grid mode through `VIBESTORM_GRID_MODE` or the wrapper
scripts:

- `local`: local OpenSim test mode
- `opengrid`: public OpenSimulator grid mode
- `sl`: Second Life / Agni mode

In SL mode, live commands require an explicit confirmation prompt, or
`VIBESTORM_SL_CONFIRM=1` in non-interactive use. SL mode also disables
automatic baked-texture uploads during session setup. Manual user actions,
including deliberate uploads, are still allowed after confirmation.

Manage the saved profile:

```bash
./run.sh login       # create or replace saved login details
./run.sh login-show  # show profile path and non-secret fields
./run.sh login-reset # delete the saved profile
```

If a saved login fails with the login-only exit status (`10`) from an
interactive terminal, `run.sh` asks whether to re-enter the saved login details
and retry once. Other command crashes/errors preserve their original exit
status and do not trigger the stale-login prompt.

## Viewer File Actions

In the 3D viewer, open `Tools -> Object Inspector`, select an object, and load
its inventory. The object-inventory file actions currently use local folders:

- `Save Item` opens a file dialog and saves the selected asset to the chosen
  path.
- `Save Text` opens a directory dialog and saves all visible script/notecard
  assets for the selected object into the chosen folder.
- `Upload File` opens a file dialog for one existing `.lsl`, `.txt`, or `.nc`
  file and uploads it into the user's inventory root through
  `NewFileAgentInventory`.
- `Upload Dir` opens a directory dialog and uploads all matching `.lsl`,
  `.txt`, and `.nc` files in that folder into the user's inventory root.

Uploading directly back into the selected object's task inventory is still
future work; it needs the task-inventory update caps rather than the
`NewFileAgentInventory` user-inventory cap.

Run a sweep-enabled forensic session and save both the session output and `unknowns` report to one text file:

```bash
./tools/run_session_forensics.sh 180
```

Capture a reference viewer session to a timestamped `pcap` plus decoded text summary:

```bash
./tools/capture_viewer_session.sh viewer-login
```

Useful environment overrides:

```bash
VIBESTORM_CAPTURE_HOST=127.0.0.1
VIBESTORM_CAPTURE_INTERFACE=lo
VIBESTORM_VIEWER_CAPTURE_DIR=local/viewer-captures
```

Inspect collected evidence:

```bash
./run.sh unknowns
```

Rebuild fixture inventory:

```bash
./run.sh fixtures
```

## Dev Notes

- `local/unknowns.sqlite3` is the default reverse-engineering evidence store.
- nearby chat lines are stored there too, so you can narrate what you are changing in-world.
- file-based packet captures are still optional via `VIBESTORM_CAPTURE_DIR=...`.
- `tools/run_session_forensics.sh` defaults to `VIBESTORM_CAMERA_SWEEP=1`, `VIBESTORM_CAPTURE_MODE=all`, and `VIBESTORM_CAPTURE_DIR=test/fixtures/live`.
- combined forensic text logs are written under `local/session-reports/` by default.
- `tools/capture_viewer_session.sh` records `tcpdump` traffic for one viewer session and writes both a `.pcap` and a decoded `.tcpdump.txt` summary under `local/viewer-captures/`.
- for loopback OpenSim captures, `tools/capture_viewer_session.sh` now defaults to `lo` instead of `any`.
- the first clean reference capture showed this viewer-side bootstrap order:
  1. `login_to_simulator` XML-RPC POST to `http://127.0.0.1:9000/`
  2. seed-cap POST to the returned `seed_capability`, including `X-SecondLife-UDP-Listen-Port`
  3. immediate CAPS requests like `EventQueueGet` and `SimulatorFeatures`
  4. UDP starts only after those early HTTP/CAPS steps
- the detailed bootstrap-era host note now lives in `archive/2026-04-bootstrap/`.

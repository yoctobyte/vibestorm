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
- test avatar: `Vibestorm Admin`
- password: `changeme123`

## Main Commands

Start OpenSim:

```bash
./run.sh opensim
```

Run a normal session:

```bash
./run.sh session
```

Run a longer reverse-engineering session:

```bash
./run.sh session 180 --verbose
```

Run a sweep-enabled forensic session and save both the session output and `unknowns` report to one text file:

```bash
./tools/run_session_forensics.sh 180
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
- the detailed bootstrap-era host note now lives in `archive/2026-04-bootstrap/`.

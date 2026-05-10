# Vibestorm

Vibestorm is a staged Second Life client project.

The current focus is protocol-core runtime work:

- login bootstrap
- UDP transport and message decoding
- capabilities and `EventQueueGet`
- normalized world/session models

## Status

The project has moved past initial scaffolding. The current codebase already includes:

- XML-RPC login/bootstrap
- seed capability resolution
- `EventQueueGet` polling
- UDP packet parsing, zerocode support, and message dispatch
- bounded live session handling with ACK/reliability tracking
- normalized region, coarse avatar, sim stats, time, and object-update models

The main remaining work is deeper message coverage and richer world decoding, especially around
object-update tails, appearance/state, and broader simulator behavior.

## Layout

- `docs/`: current docs plus archived planning/progress notes
- `spec/`: message and capability coverage tracking
- `third_party/secondlife/`: fetched canonical protocol artifacts
- `tools/`: reproducible helper scripts
- `src/vibestorm/`: Python package
- `test/`: fixtures and tests

## Read First

For current repo state and collaboration context:

- `projectstate.md`
- `docs/current-handoff.md`
- `docs/reverse-engineered-protocol.md`
- `docs/local-opensim.md`
- `AGENTS.md`

## Getting Started

Recommended:

```bash
uv sync
uv run vibestorm --help
```

Fallback:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
vibestorm --help
```

## Current CLI Surface

- `vibestorm login-bootstrap`
- `vibestorm resolve-seed-caps`
- `vibestorm event-queue-once`
- `vibestorm udp-probe`
- `vibestorm handshake-probe`
- `vibestorm session-run`
- `./run.sh viewer`
- `./run.sh viewer3d`

`session-run` is the most complete workflow today. It logs in, establishes the UDP circuit, runs
a bounded session loop, updates normalized world state, and can optionally capture selected inbound
messages for later fixture work.

`./run.sh viewer` runs the pygame bird's-eye viewer against the same live session path. It
auto-scales the UI from the desktop size and accepts `--ui-scale`, `--width`, and `--height`
overrides. The viewer exposes a pygame_gui menu/status shell, movement help, chat, a local
teleport-location request dialog, and the first read-only inventory manager window.

`./run.sh viewer3d` runs the OpenGL 3D viewer. It starts in 3D mode by default and currently
renders decoded terrain, water, primitives, first-pass lighting/texturing, diagnostics/render
settings, user inventory, and the object inspector with read-only task inventory asset viewing.

## Next Step

Build deeper message coverage on top of the existing runtime:

1. fuller `ObjectUpdate` tail decoding
2. object lifecycle coverage such as `KillObject`
3. richer avatar/chat/world message handling
4. continued live-capture driven fixture expansion

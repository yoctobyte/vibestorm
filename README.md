# Vibestorm

Vibestorm is a staged Second Life client project.

The current focus is protocol-core runtime work:

- login bootstrap
- UDP transport and message decoding
- capabilities and `EventQueueGet`
- normalized world/session models

## Status

The protocol core is done and there is a working 3D viewer on top of it:

- XML-RPC login/bootstrap, seed capability resolution, `EventQueueGet` polling
- UDP packet parsing, zerocode, message dispatch, and a live session loop with
  ACK and reliability tracking
- normalized region, avatar, sim stats, time, parcel and object-update models
- an OpenGL viewer drawing terrain, water, prims, meshes, sculpts and avatars,
  with a HUD, inventory and an object inspector -- mesh and sculpt fidelity is
  first-pass, as `viewer3d/perspective.py` says at the top of itself
- object sync: pull an in-world object's contents to a folder, push a folder
  back into it, or watch the folder and push as it changes

What is *not* done is the one thing this project cannot do for itself:
nothing here has ever logged in to the Second Life main grid, because that
needs the owner's account. Everything up to the password on that path is
verified; nothing past it is.

`docs/current-handoff.md` is the honest, detailed state -- including a list
of what each claim of "works" actually rests on.

## Protocol Work

Vibestorm is a new client implementation, built from public protocol artifacts, live traces, and
local OpenSim behavior. OpenSim has been the main practical reference target: running a local region
lets us compare packet shapes, capability flows, terrain data, inventory behavior, and simulator
edge cases without relying on a production grid.

This is not clean-room engineering; it is more like clean-kitchen engineering, where the counters
are covered with packet captures and source references. Nobody wore gloves, and OpenSim was
standing right there explaining the packets. The goal is still a clean, independent client codebase
rather than a fork of an existing viewer.

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
uv sync --extra dev
uv run vibestorm --help
```

The `dev` extra is what the fallback below installs as `.[dev]`, and the test
suite needs it: a base-only environment skips every viewer and GL test and
errors on the two Pillow-backed J2K ones.

Fallback:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
vibestorm --help
```

## Current CLI Surface

Protocol probes, one step each:

- `vibestorm login-bootstrap`
- `vibestorm resolve-seed-caps`
- `vibestorm event-queue-once`
- `vibestorm udp-probe`
- `vibestorm handshake-probe`

Sessions and world work:

- `vibestorm session-run` -- a bounded live session
- `vibestorm console` -- an unbounded one, streaming events to stdout
- `vibestorm world-census` -- what content a region actually holds
- `vibestorm inventory-walk` -- the account's inventory, or the grid library
- `vibestorm sync-object` -- bind an object to a folder: `--pull`, `--push`, `--watch`
- `vibestorm upload-notecard`, `vibestorm upload-empty-text-smoke`
- `vibestorm unknowns-report` -- what the diagnostics database recorded

Viewers:

- `./run.sh viewer` -- the 2D bird's-eye view
- `./run.sh viewer3d` -- the OpenGL viewer

`./run.sh` wraps all of these with login-profile handling and shorter names
(`census`, `eventq`, `udp`); `./run.sh --help` lists them.

## Launching The Viewer

```bash
./gui.sh              # 3D viewer against the local OpenSim
./gui.sh sl           # Second Life, at your home location
./gui.sh opengrid     # OSgrid
./gui.sh local --2d   # the 2D bird's-eye viewer
```

`gui.sh` only ever opens a viewer window and stays open until you close it.
The grid launchers (`local.sh`, `opengrid.sh`, `sl.sh`) default to the
`session` command instead -- a headless, time-limited protocol run -- so
`./local.sh` on its own gives you no window and exits by itself. Use
`./local.sh session` when that is what you want.

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

## What This Is For

The owner's five priorities, in their words, and where each stands. The
detail behind every line is in `docs/current-handoff.md`.

1. **A reasonable visualization of the world, without crashes.** The viewer
   holds 30 fps for hours against the local region; the crash work is ongoing
   and mostly about packets no local sim sends.
2. **Log in to the Second Life main grid, at the home location.** Blocked on
   the owner's credentials, and only on those. The login payload now carries a
   `mac` and `id0` -- sixteen random bytes generated once and kept in
   `local/vibestorm-install-id`, **never anything read off the hardware**. See
   `src/vibestorm/login/install_id.py`; `VIBESTORM_LOGIN_MAC=` and
   `VIBESTORM_LOGIN_ID0=` set to empty send nothing, as before.
3. **Edit an object and extract all its internals.** Done, live-verified.
4. **Upload a whole folder into an in-world object.** Done, live-verified,
   for scripts, notecards, textures and gestures.
5. **Sync an object's internals to an external folder.** Done, live-verified,
   including a watch mode that pushes as files change.

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
- region name: `Vibestorm Test` at grid location 1000,1000 (UDP port 9000)
- neighbour region: `Vibestorm North` at 1000,1001 (UDP port 9001)
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

## Two Regions, Since 2026-09-06

`local/opensim/runtime/bin/Regions/` holds one `.ini` per region and OpenSim
loads all of them. `Vibestorm Test` was alone there until 2026-09-06, so
nothing this client does about *neighbours* had ever been exercised against
anything: no `EnableSimulator`, no child agent, no region crossing. The second
file adds a region directly to the north:

```ini
[Vibestorm North]
RegionUUID = 73d6255a-5aac-46cc-a1aa-1848162e7eeb
Location = 1000,1001
SizeX = 256
SizeY = 256
SizeZ = 256
InternalAddress = 0.0.0.0
InternalPort = 9001
ResolveAddress = False
ExternalHostName = 127.0.0.1
MaptileStaticUUID = 00000000-0000-0000-0000-000000000000
TargetEstate = Vibestorm Estate
```

`TargetEstate` is not optional for an unattended start: without it OpenSim
stops at *"Do you wish to join region Vibestorm North to an existing estate?"*
and, with no terminal to answer, dies. The estate name has to be one that
already exists -- `Vibestorm Estate` here, which is what `estate_settings` in
`bin/OpenSim.db` holds.

`InternalPort` is the region's **UDP** port. In standalone there is still one
HTTP server, on 9000, for both regions; 9001 answers nothing over HTTP and it
is not a fault.

With both up, logging in to `Vibestorm Test` gets this from the simulator's
own log, which is the thing that was missing before:

    [ENTITY TRANSFER MODULE]: Informing Vibestorm Tester about neighbour
      Vibestorm North 127.0.0.1:9001 at (1000,1001)
    [SCENE Vibestorm North]: authorized child agent Vibestorm Tester
    [ENTITY TRANSFER MODULE] Vibestorm Test is sending Vibestorm Tester
      EnableSimulator for neighbour region Vibestorm North and
      EstablishAgentCommunication with seed cap http://127.0.0.1:9000/CAPS/...

## Runtime Prerequisites

OpenSim is a C# application. Our pinned build (`LastDotNetBuild.zip`, release
`r575abd6`) is a framework-dependent `net8.0` build, so it needs a **.NET 8**
runtime present on the machine. Nothing in Vibestorm itself needs .NET.

Ubuntu 24.04 packaged this; **Ubuntu 26.04 does not** (it ships only .NET 10).
On 26.04, install the runtime user-locally — no sudo, nothing system-wide:

```bash
curl -fsSL https://dot.net/v1/dotnet-install.sh \
  | bash -s -- --channel 8.0 --runtime dotnet
```

That lands in `~/.dotnet`, which `tools/start_opensim.sh` picks up
automatically. Override with `DOTNET_ROOT` if you keep it elsewhere. The
launcher fails with the install command rather than starting on the wrong
runtime.

Also required, and still a normal distro package:

```bash
sudo apt install libgdiplus
```

Without it, `VectorRenderModule` and `MapImageService` die at startup with
`DllNotFoundException: Unable to load shared library 'libgdiplus'`.

**Do not run the sim on .NET 9 or newer, including via
`DOTNET_ROLL_FORWARD`.** It boots, reports the region ready, and is then subtly
wrong: `BinaryFormatter` was removed in .NET 9, and OpenSim relies on it for the
asset cache, keyframed prim motion, and script state. Captures taken against
such a sim are not trustworthy. See `runtime-platform-risk.md` for the evidence
and the long-term options.

## Main Commands

Start OpenSim:

```bash
./run.sh opensim
```

**Headless, it needs `-console=rest`.** OpenSim's default console calls
`Console.KeyAvailable`, which throws when stdin is not a terminal, and the
prompt loop then spins on the exception -- measured at 96% of a core, forever.
Giving it a pseudo-terminal instead (`script`) is worse rather than better: the
console reads uninitialised bytes off it, prints them back as `Invalid
command`, and within a minute one of them matches `quit` and the simulator
shuts itself down. That happened twice while a second region was being added.
The remote console has no local prompt loop at all:

```bash
./tools/start_opensim.sh -console=rest 2>&1 | tee /tmp/opensim.log
```

Settles at about 4% of a core with two regions up, and takes no console
commands -- which is the trade, and the right one for an unattended run.

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

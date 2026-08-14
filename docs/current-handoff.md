# Current Handoff

Last updated: 2026-08-14 (documenting work through 2026-06-22)

## Where To Move Next

Three coherent tracks are open. All three are now blocked on the same kind of
work: **consuming decoders that already exist**, and live-verifying them.

1. Wire up the parcel track (newest, shortest path to visible value).
   The 2026-06-22 pass decoded `ParcelProperties`, the `ParcelOverlay` grid, and
   the parcel Bitmap — but `decode_parcel_overlay` / `decode_parcel_bitmap` have
   no caller in `src/`, and the HUD still prints `Parcel: unknown` because
   nothing assigns `scene.parcel_name`. Set the name from the bus event first,
   then reassemble the overlay grid and draw `border_segments` as plot edges.
   See the 2026-06-22 parcel/EQ/animation/sound update below.
2. Live-verify object sync: select a scripted object in `viewer3d`, download scripts
   via Save Text, edit a `.lsl` locally, then "Upload File" — confirm the upload dialog
   seeds `local/asset-downloads/<task-id>/`, syncs to the object, and the script
   recompiles (check chat for "Sync: … compiled OK"). The typed
   `ScriptRunningReply` EQG event now exists to confirm this server-side, but is
   not wired into the poll loop yet.
3. Continue real mesh/sculpt rendering:
   - live-verify `GetMesh` against a local OpenSim mesh object
   - ~~add normals/UVs and per-face/material grouping to decoded mesh assets~~
     (done 2026-06-22 in the decoder; renderer wiring still open — see that update)
   - live-verify sculpt map fetch/deformation against local OpenSim sculpted prims
   - add viewer-grade sculpt stitching/normals/UVs

Nothing from the 2026-06-22 session has been seen against live OpenSim traffic —
it is all unit-tested against synthetic packets. A live session is the highest-value
next action regardless of which track you pick.

## Object Sync Track

The next coherent file feature track is object-local script/notecard sync, not
more generic user-inventory upload.

Implement it in this order:

1. Add a task-inventory asset update CAP client beside
   `src/vibestorm/caps/asset_upload_client.py`.
   - Resolve `UpdateScriptTask` first, then fall back to
     `UpdateScriptTaskInventory` for script rows.
   - Resolve `UpdateNotecardTaskInventory` for notecard rows.
   - Match OpenSim's two-step shape: POST LLSD metadata to the CAP, receive
     `state=upload` plus `uploader`, then POST raw file bytes to the uploader.
2. Start with updating existing object inventory items only.
   - Script metadata is `item_id`, `task_id`, and `is_script_running`.
   - Notecard task updates appear to share the broader item-asset update path;
     verify the exact request keys against `referencedocs/Caps/BunchOfCaps/UpdateItemAsset.cs`
     before coding.
   - Do not create new object inventory rows yet; that can follow after update
     is proven live.
3. Add a narrow sync planner in `viewer3d`.
   - Use the selected object's `task_id` as the local folder key:
     `local/asset-downloads/<task-id>/`.
   - Match `.lsl` files to visible script inventory rows and `.txt` / `.nc`
     files to visible notecard rows by sanitized item name.
   - For the first pass, upload only exact name matches and report skipped
     files in chat/status.
4. Wire Object Inspector `Upload` to selected-object sync when an object
   inventory row set is loaded; keep the current user-inventory upload as the
   fallback when no selected object context exists.
5. Live verify on local OpenSim with `./run.sh tester viewer3d`:
   download an object's scripts/notecards, edit one local file, upload/sync,
   reload task inventory, and view the item again.

Keep these scope limits for the first pass:

- no bidirectional conflict resolution
- no deletes
- no creating missing object inventory items
- no recursive folder sync
- no automatic upload on every file change

## Update 2026-08-14: Self-Checking Ledgers, IM, Teleport

### The recurring problem, closed

`spec/message-coverage.md` and `spec/capability-coverage.md` have each drifted
twice, in both directions, and every drift was found by hand months later. Both
now re-derive themselves from the code:

- `test/test_message_coverage_ledger.py` — a row claiming support must name
  something the source mentions; every message with a parser must have a row;
  every message the client can *send* must have a row.
- `test/test_capability_coverage_ledger.py` — the same two, plus a scale check,
  because this ledger writes its status scale out in the document and a row can
  contradict it. It immediately caught a row reading `handled`, a status
  borrowed from the other ledger's scale.

Wire names are read from what the code actually does, never from a naming
convention: parsers from their `summary.name != "X"` guard, encoders from the
message-number prefix they write, resolved through the same template the client
dispatches with. The guess would be wrong — `parse_simulator_viewer_time`
decodes `SimulatorViewerTimeMessage`.

Between them these found **22 messages** with working code and no ledger row:
the appearance handshake, the xfer and transfer requests, map blocks, parcel
properties, task inventory, and the teleport request.

Neither checks `tested` versus `verified`. That is a claim about live evidence,
and asserting it offline would be the overclaim the distinction exists to
prevent. Nor can anything catch a message this client neither sends nor
parses — indistinguishable from a message that does not concern us.

### "Blocked on region content" was wrong twice

`ImprovedInstantMessage` was filed as needing a second avatar. It does not:
OpenSim's `InstantMessageModule.OnInstantMessage` routes on `ToAgentID` with no
self-check, so an IM addressed to our own agent id comes back through exactly
the inbound path a second avatar's would take. Sent live, delivered 5.5 s later.
Chat had left the same list a week earlier for the same reason.

**Before filing something as blocked on world content, check whether the client
can produce the traffic itself.** Twice the blocker was a missing outbound
message, not a missing object.

The IM encoder writes the trailing `EstateBlock` and `MetaData` blocks that the
template defines and OpenSim's handler ignores. The packet is deserialised in
full before the handler runs, so a block the deserialiser expects and does not
find is malformed, while trailing bytes it does not expect are not. libomv is
DLL-only in `opensim-source/`, so this was settled by the live round trip
rather than by reading its packet class.

### Teleport

The client could send `TeleportLocationRequest` and understood none of the
replies. Now decodes `TeleportStart`, `TeleportProgress`, `TeleportFailed` and
`TeleportLocal`, with `world/teleport_flags.py` naming the flag word — fully
sourced from `Constants.cs`, so unlike the parcel and region tables this one
has no unnamed bits and the pin test demands every flag be named.

Verified live both ways: a hop inside the region, and a teleport to a region
handle no region occupies (`'The region you tried to teleport to was not
found'`, zero `AlertInfo` blocks — OpenSim never populates that block).

Two corrections the live run forced:

- `TeleportLocal` is the **entire** response to a same-region hop. No
  `TeleportFinish`, no new circuit, no seed capability. The session must take
  its position from it or keep sending `AgentUpdate` from where the avatar used
  to be.
- This module first shipped `is_same_region_teleport` /
  `is_region_crossing_teleport` reading the `FinishedVia*` bits. They looked
  right and were dead code: **OpenSim sets no `FinishedVia*` bit anywhere**,
  it forwards the request's own flag word unchanged. The flags stay named for
  other grids; the predicates are gone, and a test pins the absence to the
  source tree rather than to one reading of it.

### Also

`UpdateScriptTaskInventory` is the name OpenSim marks `//legacy`; the current
name is `UpdateScriptTask`. The object-sync path asked only for the legacy
alias, which works today and would fail as "no task inventory caps available"
the day it is dropped. It now asks for both, current first.

### ViewerAsset

`ViewerAsset` had been resolved every session since the seed-cap list was
written and had no client, so notecards, scripts, animations and sounds could
only come down the UDP `TransferRequest` channel. `caps/viewer_asset_client.py`
now implements it, and `RequestAssetData` prefers it — the session loop sends a
`TransferRequest` only if the HTTP fetch fails, so this adds a path without
removing one. Bytes land in `session.fetched_assets` either way.

Live-verified twice: standalone against the region map texture (4376 bytes,
`image/x-j2c`, byte-identical to `GetTexture`), and end-to-end through the real
`_handle_request_asset_data` with no UDP transfer sent.

Two properties of the capability that are easy to get wrong:

- **The query key selects the asset type.** There is no generic `asset_id`, and
  an unrecognised key is answered 404 *before* the asset service is consulted.
  So `asset_type_query_key` raises rather than falling back to a plausible key,
  which would report "the sim does not have it" when the truth is "this client
  does not know that type".
- **The type check is not enforced.** `GetAssetsHandler` compares `asset.Type`
  against the key's implied type, logs `asset with wrong type`, and serves the
  bytes anyway — the `return` beneath the warning is commented out. Asking for
  the map texture as `notecard_id` returned the same 4376 bytes. A 200 is
  therefore no evidence about an asset's type.

The key table covers only the type numbers LSL pins, because `AssetType` is
libomv's enum and only the DLL ships in `opensim-source/`. `mesh_id` and the
five TGA/WAV/JPEG keys are absent for that reason alone — an unmappable type
stays on UDP rather than being routed to a guaranteed 404.

### A testing note worth keeping

Mutation-checking with `cp` to restore a file can lie. A restored file the same
size as the mutated one, written in the same second, leaves Python's `.pyc`
stale — so a mutation reads as caught when it is not, or a restore reads as
broken when it is not. Run mutation checks with `PYTHONDONTWRITEBYTECODE=1`.

The IM encoder's first mutation pass also missed a swapped `Offline`/`Dialog`
pair: two adjacent U8s, both 0 in every test, so the swap round-tripped
perfectly. There is now a test that gives them different values.

## Update 2026-05-25: Object Task Inventory Sync (Steps 2–4)

### What Changed

- **`src/vibestorm/caps/task_inventory_upload_client.py`** (new): two-step
  `UpdateScriptTaskInventory` / `UpdateNotecardTaskInventory` CAP client.
  `upload_task_script()` sends `{item_id, task_id, is_script_running}`, gets an
  uploader URL, then POSTs raw LSL bytes. `upload_task_notecard()` is identical
  except without `is_script_running`.

- **`src/vibestorm/viewer3d/hud.py`**:
  - New `on_upload_object_files` callback on `HUD.__init__`.
  - New `_selected_object_task_context()` method — returns `(task_id, rows)` for
    the currently selected object if its task inventory is loaded, `None` otherwise.
  - "Upload File" and "Upload Dir" buttons now detect object context: when a task
    context is present they open a sync dialog seeded at
    `local/asset-downloads/<task-id>/` and fire `on_upload_object_files`; when no
    object context they fall back to the existing user-inventory upload path.

- **`src/vibestorm/viewer3d/app.py`**:
  - New `_match_files_to_task_selections(upload_dir, asset_rows)` pure helper —
    matches `.lsl`/`.txt`/`.nc` files by safe-filename stem to loaded inventory
    rows (scripts asset_type=10, notecards asset_type=7); returns
    `(matched, unmatched)`.
  - New `sync_files_to_object_task_inventory` coroutine — resolves
    `UpdateScriptTaskInventory` / `UpdateNotecardTaskInventory` caps, runs the
    match planner, uploads matched files, reports each result and a summary in chat.
  - `on_upload_object_files` wired into the HUD constructor.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/hud.py src/vibestorm/viewer3d/app.py src/vibestorm/caps/task_inventory_upload_client.py`
- `uv run --extra viewer pytest test/test_task_inventory_upload_client.py test/test_viewer3d_object_inspector.py test/test_viewer3d_app_compositor.py -q` — 29 passed
- `./run.sh test` — 536 passed, 0 failed

### Concrete Next Step

Live-verify on local OpenSim with `./run.sh tester viewer3d`:
1. Select a scripted object, open Object Inspector, Load Inventory.
2. Save Text to `local/asset-downloads/<task-id>/`.
3. Edit the `.lsl` file locally.
4. Click "Upload File" — confirm dialog seeds `local/asset-downloads/<task-id>/`.
5. Select the edited file; watch chat for `Sync: … compiled OK` or compile errors.
6. Reload task inventory in viewer; confirm the script version changed.

If caps are missing (`Sync: no task inventory caps available`), check that OpenSim
has `UpdateScriptTaskInventory` and `UpdateNotecardTaskInventory` wired in BunchOfCaps.

## Update 2026-05-22: Pygame In-Game Login & Credential Saving

### What Changed

- **Launcher Integration (`run.sh`)**: Bypassed terminal interactive prompting (`prompt_login`) and re-entry/retry prompts for the `viewer` and `viewer3d` commands. Exported active profile paths via environment variables `VIBESTORM_LOGIN_PROFILE` and `VIBESTORM_LOGIN_PROFILE_NAME`.
- **Credentials Utility (`src/vibestorm/util/credentials.py`)**: Implemented a secure shell-compatible parser and writer for `.env` login profiles using `shlex` shell-safe quoting and strict file permissions (`mode 600`). It has robust fallback default credential resolution for the `tester` profile.
- **Login Screen UI (`src/vibestorm/viewer/login_screen.py`)**: Built a highly aesthetic in-game Pygame login screen containing:
  - Translucent glassmorphic center container with glowing highlights.
  - Linear vertical gradient background with custom-rendered, elegantly drifting/glowing background micro-particles.
  - Complete form inputs for Grid Preset Selection, Custom Grid URI, Avatar First/Last Name, Password (masked text entry), Start Location, and Remember Credentials.
  - Grid preset prefilling logic that automatically populates standard grids (Local OpenSim, OSgrid, Second Life) on selector change.
  - Asynchronous login via `LoginClient().login(...)` in the running event loop with a smooth "Connecting..." indicator, "Cancel" button, and descriptive inline error reporting.
  - Protected `asyncio.get_running_loop()` check to support headless synchronous unit testing without event loop crashes.
- **2D App Integration (`src/vibestorm/viewer/app.py`)**: Removed strict command-line argument requirements for credentials and wired the new `LoginScreen` loop to execute first if complete credentials are not supplied via CLI arguments.
- **3D App Integration (`src/vibestorm/viewer3d/app.py`)**: Removed strict command-line argument requirements and updated 3D viewer bootstrap to open a Pygame/ModernGL screen first, drawing the software `LoginScreen` UI onto the composited `world_surface` background quad before transition.

### What Was Verified

- **Unit Tests**:
  - `test/test_credentials.py` verified profile loading/saving, tester fallback defaults, and shlex unquoting/escaping.
  - `test/test_login_screen.py` verified UI widget construction, preset dropdown selection/prefilling, quit request action, and event handler consumption.
  - Full project pytest suite (525 tests) runs and successfully passes.
- **Headless Pygame Execution**: Verified standard startup workflows run flawlessly under the `dummy` SDL video driver.

### Concrete Next Step

- Manual verification: Run `./run.sh viewer` or `./run.sh viewer3d` on a live display. Fill in or load credentials, toggle "Remember Credentials", verify the glassmorphic animations, and successfully connect.

## Update 2026-05-17: Sculpt/Mesh Render Placeholders

### What Changed

- `viewer3d` now decodes the sculpt `ExtraParams` block
  (`ParamType=0x30`, `UUID + sculpt_type`) into renderer-facing scene
  metadata.
- Sculpt placeholders now choose an approximate existing primitive mesh:
  sphere, torus, cylinder, or flat cube/plane.
- SL mesh objects (`sculpt_type=5`) are tagged as `mesh` and keep their asset
  UUID on `SceneEntity.mesh_asset_id`; a follow-up update below adds the first
  actual `GetMesh` fetch/decode path.

### Current Boundary

- This first classification step was visual-only; see the follow-up mesh
  update below for the first real mesh asset path.
- Vibestorm still does not fetch or decode sculpt-map textures.
- Per-face mapping for non-cube primitives is still coarse; cube face-specific
  texture overrides remain the only detailed face mapping.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/scene.py src/vibestorm/viewer3d/perspective.py test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py`
- `uv run --extra viewer pytest test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py -q`

### Concrete Next Step

Continue with live verification and renderer fidelity: normals, UVs,
per-face/material grouping, and viewer-grade sculpt stitching.

## Update 2026-05-17: First Real Mesh Asset Path

### What Changed

- Added `src/vibestorm/caps/get_mesh_client.py` for `GetMesh` asset fetches.
- The session seed-cap prelude now requests `GetMesh2` and `GetMesh`, prefers
  `GetMesh2`, and defers mesh fetches until a mesh object is seen.
- Mesh objects discovered through sculpt `ExtraParams` type `0x30` with
  `sculpt_type=5` are fetched by `mesh_id`, cached as raw `.llmesh` files
  under `local/mesh-cache/`, and republished through `MeshAssetReady`.
- Added `src/vibestorm/assets/sl_mesh.py`, a narrow SL mesh decoder:
  binary LLSD header, high-LOD block lookup, compressed LLSD submesh array
  inflate, `Position` dequantization, and `TriangleList` index assembly.
- `viewer3d` now records mesh cache paths and uploads decoded high-LOD mesh
  geometry into the existing instanced GL renderer keyed by mesh asset UUID.
  If fetch/decode is missing or fails, the existing sphere placeholder remains.

### Current Boundary

- Only `high_lod` is decoded.
- No normals, UVs, skinning/rigging, physics blocks, LOD switching, or
  per-face material grouping yet.
- Mesh asset decoding is covered by synthetic tests; it still needs live
  verification against OpenSim mesh assets.
- Sculpt maps are handled by the follow-up sculpt update below.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/assets/sl_mesh.py src/vibestorm/caps/get_mesh_client.py src/vibestorm/bus/events.py src/vibestorm/udp/session.py src/vibestorm/udp/world_client.py src/vibestorm/viewer3d/scene.py src/vibestorm/viewer3d/app.py src/vibestorm/viewer3d/perspective.py test/test_sl_mesh.py test/test_get_mesh_client.py test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py test/test_world_client.py`
- `uv run pytest test/test_sl_mesh.py test/test_get_mesh_client.py -q`
- `uv run --extra viewer pytest test/test_viewer3d_scene.py test/test_viewer3d_perspective_gl.py -q`
- `uv run pytest test/test_udp_session.py test/test_world_client.py -q`

### Concrete Next Step

Create or rez a simple OpenSim mesh object, run `./run.sh tester viewer3d`,
and watch for `mesh.cache.ok` followed by visible non-placeholder geometry.
If the mesh appears, add UV/normal decode next; if it does not, inspect the
cached `.llmesh` header/block layout and adjust the LLSD/decompression path.

## Update 2026-05-17: First Sculpt Map Geometry Path

### What Changed

- Sculpted prims (`ExtraParams type=0x30`, sculpt type `1..4`) now enqueue
  their referenced sculpt texture UUID through the existing `GetTexture`
  object-texture fetch path.
- Added `src/vibestorm/assets/sculpt.py`, which converts RGB/RGBA sculpt-map
  pixels into a unit-sized triangle mesh:
  - RGB maps to local `[-0.5, 0.5]` xyz coordinates.
  - sphere/cylinder wrap horizontally.
  - torus wraps horizontally and vertically.
  - plane remains open.
  - sphere top/bottom rows converge to simple pole averages.
  - sculpt flags are preserved and applied: `0x40` reverses triangle winding
    for inside-out/inverted sculpts, and `0x80` mirrors local X.
  - large maps are downsampled to a 32x32 render grid for now.
- `viewer3d` now uploads cached sculpt PNGs as per-asset GL meshes keyed by
  sculpt texture UUID and sculpt type. If the texture is not cached or decode
  fails, the existing approximate primitive placeholder remains.

### Current Boundary

- This is not viewer-grade sculpt tessellation yet.
- No authored normals, UV recovery, exact SL stitching, mirror/invert handling,
  or sculpt LOD behavior.
- The path is covered by synthetic tests; it still needs live verification
  against local OpenSim sculpted prims.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/assets/sculpt.py src/vibestorm/udp/session.py src/vibestorm/viewer3d/perspective.py test/test_sculpt.py test/test_udp_session.py test/test_viewer3d_perspective_gl.py`
- `uv run pytest test/test_sculpt.py test/test_udp_session.py -q`
- `uv run --extra viewer pytest test/test_viewer3d_perspective_gl.py -q`

### Concrete Next Step

Rez or import a known sculpted prim in local OpenSim, run
`./run.sh tester viewer3d`, and check for its sculpt texture entering
`local/texture-cache/` followed by visible non-placeholder geometry. If the
shape is mirrored or pinched, tune the seam/stitching rules from the cached PNG
and live object's sculpt type.

## Update 2026-05-17: Avatar Placeholder And Camera Presets

### What Changed

- Added a dedicated `avatar_placeholder_mesh()` in `viewer3d.meshes`.
  Avatars now render as a simple human-like silhouette with torso, head, arms,
  legs, and a small forward-facing marker, rather than the cube fallback.
- The avatar mesh faces local +X, so existing ObjectUpdate quaternions visibly
  rotate the placeholder.
- Added camera presets:
  - `F1`: sim-wide orbit view.
  - `F2`: third-person view behind the avatar at roughly 10 m.
  - `F3`: avatar eye view.
- F2/F3 continuously refresh from the current avatar entity transform when
  world updates arrive.
- `docs/viewer-help.md` now lists the 3D camera keys.

### Current Boundary

- Avatar mesh is still a placeholder, not appearance-driven.
- No animations, skeleton, attachments, clothing, or body-shape visual params.
- First-person uses current avatar rotation only; camera collision and mouselook
  controls are not implemented.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/camera.py src/vibestorm/viewer3d/input.py src/vibestorm/viewer3d/meshes.py src/vibestorm/viewer3d/perspective.py src/vibestorm/viewer3d/app.py test/test_viewer3d_camera.py test/test_viewer3d_input.py test/test_viewer3d_meshes.py test/test_viewer3d_perspective_gl.py`
- `uv run --extra viewer pytest test/test_viewer3d_camera.py test/test_viewer3d_camera_matrices.py test/test_viewer3d_input.py test/test_viewer3d_meshes.py test/test_viewer3d_perspective_gl.py -q`

### Concrete Next Step

Live-verify F2/F3 against local OpenSim. If the camera points sideways or
backward, adjust `_avatar_forward()` based on observed avatar quaternion
convention; then add mouse steering for eye/behind modes.

## Update 2026-05-16: Grid Launchers And SL Guardrails

### What Changed

- Added thin launchers:
  - `./local.sh ...` uses `VIBESTORM_GRID_MODE=local` and the default
    `tester` profile.
  - `./opengrid.sh ...` uses `VIBESTORM_GRID_MODE=opengrid` and the default
    `osgrid` profile.
  - `./sl.sh ...` uses `VIBESTORM_GRID_MODE=sl` and the default `sl` profile.
- `run.sh` now derives a grid mode from `VIBESTORM_GRID_MODE`, the profile
  name, or a known login URI. `login-show` prints it.
- SL mode requires explicit confirmation before commands that touch the live
  simulator beyond plain login/cap inspection (`eventq`, `udp`, `handshake`,
  `session`, `console`, `viewer`, `viewer3d`, and `upload-smoke`). In
  non-interactive use, set `VIBESTORM_SL_CONFIRM=1`.
- SL mode passes `--no-auto-bake-upload` to bounded sessions, console, and both
  viewers. Deliberate user actions, including manual uploads, remain possible
  after confirmation.
- The session runtime now has `SessionConfig.auto_upload_bakes`; the Upload
  Baked Texture CAP is resolved but ignored when this flag is false.

### What Was Verified

- `bash -n run.sh local.sh opengrid.sh sl.sh`
- `uv run ruff check --select F,I src/vibestorm/app/cli.py src/vibestorm/viewer/app.py src/vibestorm/viewer3d/app.py src/vibestorm/udp/session.py`
- `uv run pytest test/test_udp_session.py test/test_viewer3d_app_compositor.py -q`
- `git diff --check`
- `./sl.sh login-show` selects the `sl` profile and Agni login URI without
  requiring credentials.
- A non-interactive `./sl.sh session 0` with dummy env credentials refuses to
  continue without `VIBESTORM_SL_CONFIRM=1`.

### Concrete Next Step

Use `./sl.sh login-show`, then `./sl.sh bootstrap`, then a short
`./sl.sh session 20 --verbose` on a disposable SL account only after accepting
the explicit confirmation prompt.

## Update 2026-05-17: File Dialogs For Viewer File Actions

### What Changed

- Wired `pygame_gui.windows.UIFileDialog` into the 3D Object Inspector file
  actions.
- `Save Item` now opens a save path picker seeded under
  `local/asset-downloads/<task-id>/`.
- `Save Text` now opens a directory picker and saves all visible object
  script/notecard assets into that chosen folder.
- The Object Inspector now has separate upload actions:
  - `Upload File` picks one existing `.lsl`, `.txt`, or `.nc` file.
  - `Upload Dir` picks a folder and uploads all matching files in that folder.
- The app upload path now accepts either one file or one folder. It still uses
  the existing `NewFileAgentInventory` user-inventory upload path.

### Current Boundary

- Multi-save is wired for object/task inventory rows whose asset UUIDs are
  visible and retrievable.
- User-inventory directory save is not wired yet; that needs user-inventory
  asset retrieval plumbing comparable to the current object `TransferRequest`
  path, plus a row-to-folder save planner.
- Uploading back into the selected object's task inventory is still future
  work and remains the next protocol task.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/hud.py src/vibestorm/viewer3d/app.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_viewer3d_object_inspector.py -q`
- `uv run --extra viewer pytest test/test_viewer3d_app_compositor.py test/test_viewer3d_object_inspector.py -q`
- `git diff --check`

## Update 2026-05-14: Viewer File Actions

### What Changed

- Added Object Inspector buttons for file actions:
  - `Save Item` queues a download for the selected object inventory asset.
  - `Save Text` queues downloads for every visible script/notecard asset in the
    selected object inventory.
  - `Upload` uploads local `.lsl`, `.txt`, and `.nc` files from `local/upload/`
    into the user's inventory root through `NewFileAgentInventory`.
- Downloaded object assets are written under
  `local/asset-downloads/<task-id>/` with `.lsl` for scripts, `.txt` for
  notecards, `.j2k` for textures, and `.bin` for unknown asset types.
- `AssetDataReady` handling now also drains pending file-save requests before
  showing the asset in the viewer window.

### Current Boundary

- Bulk object download is now wired for assets whose UUID is visible in the
  task inventory listing. If OpenSim withholds the asset UUID, the viewer still
  reports that as a permission/protocol limitation rather than issuing a doomed
  transfer request.
- Upload is currently user-inventory upload only. True object upload/sync needs
  the separate task-inventory update caps (`UpdateScriptTaskInventory`,
  `UpdateNotecardTaskInventory`, etc.) or equivalent UDP update flow.

### What Was Verified

- `uv run ruff check --select F,I src/vibestorm/viewer3d/app.py src/vibestorm/viewer3d/hud.py test/test_viewer3d_object_inspector.py test/test_viewer3d_app_compositor.py`
- `uv run --extra viewer pytest test/test_viewer3d_object_inspector.py test/test_viewer3d_app_compositor.py -q`

### Concrete Next Step

Implement the task-inventory update capability client and add a sync planner
that compares `local/asset-downloads/<task-id>/` against the selected object's
script/notecard inventory before uploading changes back into the object.

## Update 2026-05-14: Interactive Login Profile

### What Changed

- `run.sh` now loads login details from env vars first, then from ignored
  `local/vibestorm-login.env` if present.
- `run.sh` now accepts a profile name before the command. The default profile
  remains `local/vibestorm-login.env`; named profiles use ignored files like
  `local/vibestorm-login-tester.env`.
- `./run.sh tester ...` has a built-in local OpenSim fallback for the
  `Vibestorm Tester` account if that profile file does not exist yet. Env vars
  and explicit profile files still override the fallback.
- Added `./run.sh login`, `./run.sh login-show`, and `./run.sh login-reset`
  for changing user, password, sim preset, or start location without manually
  editing the profile.
- If a login command is launched with missing details from an interactive
  terminal, `run.sh` prompts for sim location (`localhost`, `opengrid`, `sl`,
  or `custom`), first name, last name, and password.
- Prompted credentials can be stored in `local/vibestorm-login.env` with mode
  `600`. This is local-file storage for development convenience, not encrypted
  OS keyring storage.
- Login-capable Python entrypoints now translate `LoginError` to exit status
  `10`. If a saved login command exits with status `10` from an interactive
  terminal, `run.sh` asks whether to re-enter saved login details and retry
  once. Other crashes/errors keep their original nonzero status and are not
  treated as failed logons.
- The `opengrid`/`osgrid` preset uses OSgrid's published login URI:
  `http://login.osgrid.org/`.
- Fixed a 3D viewer asset-view crash where the `AssetDataReady` subscriber
  unpacked object-inspector asset metadata as three fields even though the HUD
  stores five fields (`asset_id`, `asset_type`, `item_name`, `task_id`,
  `item_id`).

### What Was Verified

- Found the existing local test credential in ignored OpenSim console history,
  not in a tracked env file.
- `Vibestorm Tester` bootstrap succeeded using that ignored local credential.
- The upload smoke test succeeded after the stale "already logged in" presence
  expired.
- Prompt/storage path was checked against a temporary profile; it wrote a
  shell-sourceable env file with mode `600` before the intentionally bad login
  URI failed.
- `./run.sh login-show` shows only non-secret profile fields plus
  `password=set/missing`.
- Failure handling was checked with a temporary stale profile: interactive
  commands now offer one re-entry/retry path, while noninteractive failure
  preserves the underlying nonzero exit status.
- `./run.sh tester login-show` resolves the built-in local test profile when no
  `local/vibestorm-login-tester.env` file exists.
- `bash -n run.sh`
- `uv run ruff check --select F,I src/vibestorm/app/cli.py src/vibestorm/viewer/app.py src/vibestorm/viewer3d/app.py test/test_viewer3d_app_compositor.py`
- `uv run --extra viewer pytest test/test_viewer3d_app_compositor.py test/test_viewer3d_object_inspector.py -q`
- `uv run pytest test/test_asset_upload_client.py -q`

### Concrete Next Step

Implement object/task-inventory update caps so the new file UI can upload back
into the selected object rather than only into user inventory.

## Update 2026-05-13: NewFileAgentInventory Upload Smoke

### What Changed

- Added `src/vibestorm/caps/asset_upload_client.py` for the generic
  `NewFileAgentInventory` capability flow:
  - LLSD metadata prelude with `asset_type`, `inventory_type`, `folder_id`,
    `name`, `description`, and permission masks.
  - one-shot raw-byte POST to the returned uploader URL.
  - completion parsing for `state`, `new_asset`, `new_inventory_item`, and
    returned permission masks.
- Added `vibestorm upload-empty-text-smoke` and `./run.sh upload-smoke`.
  The command creates `local/upload-smoke/empty-space.txt` as an empty file,
  appends one space, uploads that one byte as a notecard/text item, then
  confirms the returned inventory item through `FetchInventory2`.
- Added focused tests in `test/test_asset_upload_client.py`.

### What Is Now Known

- Local OpenSim source for `NewAgentInventoryRequest` creates both the asset
  UUID and inventory item UUID server-side (`UUID.Random()`), so the new-file
  upload path should always return fresh GUIDs rather than client-chosen IDs.
- The generic upload completion reply shape is close to baked-texture upload
  but includes `new_inventory_item` and permission-mask fields.

### What Remains Unknown / TODO

- This is a CLI smoke path only. Viewer create/save/upload UI is still not
  wired.
- Object/task-inventory update caps (`UpdateScriptTaskInventory` /
  `UpdateNotecardTaskInventory`) are still separate future work.

### What Was Verified

- `uv run ruff check src/vibestorm/caps/asset_upload_client.py test/test_asset_upload_client.py`
- `uv run ruff check --select F,I src/vibestorm/app/cli.py src/vibestorm/caps/asset_upload_client.py test/test_asset_upload_client.py`
- `uv run pytest test/test_asset_upload_client.py test/test_inventory_caps_client.py -q`
- `uv run pytest -q` -> 487 passed, 28 pygame_gui font warnings
- Live OpenSim smoke with the ignored local `Vibestorm Tester` credential:
  uploaded one byte, returned `new_asset=8a3bc672-4a0e-4542-80dc-0973d63fd5e2`,
  returned `new_inventory_item=77798038-e03a-4dd5-8704-031203269a63`, and
  confirmed that item via `FetchInventory2`.

### Concrete Next Step

Wire this path into the viewer inventory UI as a minimal "new text/notecard"
action, then build richer save/edit flows on top.

## Update 2026-05-10: Asset Viewer — Read-Only Notecard / Script / Texture Display

### What Changed

Full end-to-end plumbing for viewing object-inventory assets (notecards, LSL scripts,
textures) in the 3D viewer. Read-only, no upload/edit yet.

**Protocol layer (`src/vibestorm/udp/`)**

- `messages.py`: Added `TransferInfoMessage`, `parse_transfer_info`,
  `TransferPacketMessage`, `parse_transfer_packet`, and `encode_transfer_request`.
  These cover the `TransferInfo` and `TransferPacket` UDP messages used by the
  simulator's asset-delivery channel.
- `session.py`: Added `PendingAssetTransfer` dataclass; `fetched_assets: dict[UUID, bytes]`
  and `pending_asset_transfers: dict[UUID, PendingAssetTransfer]` on
  `LiveCircuitSession`. Added `build_transfer_request_packet()`,
  `_handle_transfer_info()`, and `_handle_transfer_packet()` methods.
  - Supports **TaskInventory (source_type=3)** transfers: when `task_id` and `item_id`
    are provided, the expanded `TransferRequest` params are used (allowing
    retrieval of copy-protected scripts/notecards from object inventory).
  - Uses `item_id` as a surrogate `asset_id` for completion tracking when the
    sim hides the real asset UUID (sending zeros).
- `world_client.py`: Resolves `owner_id` from `world_view` when performing a
  `TaskInventory` transfer.


**Bus layer (`src/vibestorm/bus/`)**

- `commands.py`: Added `RequestAssetData(asset_id, asset_type)` command.
- `events.py`: Added `AssetDataReady(region_handle, asset_id, asset_type, data)` event.

**World client (`src/vibestorm/udp/world_client.py`)**

- Registered handler for `RequestAssetData` → calls `build_transfer_request_packet`
  and queues the outbound packet.
- Translates `transfer.complete` session events into typed `AssetDataReady` bus events.

**HUD (`src/vibestorm/viewer3d/hud.py`)**

- `inspector_inventory` changed from `UITextBox` → `UISelectionList` so items are
  individually selectable.
- `_object_inventory_html()` (renamed semantically; returns `list[str]`) now renders
  each inventory item as `"Name [asset_type_or_inv_type]"`, with NUL-char stripping.
- New `on_view_asset: Callable[[UUID, int], None]` callback on `HUD.__init__`.
- New `inspector_view_asset_button` beside Load Inventory; enabled when an inventory
  item with a viewable asset is selected.
- `register_inventory_snapshot_for_view(snapshot)` — called when inventory arrives;
  builds `_inspector_item_asset_map` so the button knows which asset+type to request.
- `enable_view_for_item(item_key)` — called when a selection-list row is highlighted.
- `show_asset_data(asset_id, asset_type, data, item_name=…)` — decodes and displays:
  - asset_type 7 (notecard) / 10 (lsltext): UTF-8 text in `asset_viewer_text`.
  - asset_type 0 (texture): decoded via PIL → pygame Surface in `asset_viewer_image`.
  - Other types: size/type summary.
- New `asset_viewer_window` (`UIWindow`, resizable, hidden by default).
- `_asset_type_string_to_int()` module-level helper converts string → int.

**App (`src/vibestorm/viewer3d/app.py`)**

- `on_view_asset` callback wired to `client.bus.dispatch(RequestAssetData(…))`.
- Bus subscriptions added after HUD creation:
  - `AssetDataReady` → `hud.show_asset_data(…)`.
  - `ObjectInventorySnapshotReady` → `hud.register_inventory_snapshot_for_view(…)`.
- Session-event logging extended to include `"transfer."` prefix.

### What Is Now Known

- The Transfer protocol handshake (TransferRequest → TransferInfo → TransferPacket*)
  works identically to the Xfer handshake but uses a different packet set.
- Texture bytes coming through Transfer are raw J2K; PIL can decode them if installed.
- The `_ASSET_TYPE_MAP` in hud.py lists all known SL/OpenSim asset type strings and
  their integer equivalents.

### What Remains Unknown / TODO

- Real-world test against a live OpenSim instance (no live session done yet).
- Texture assets via the GetTexture capability (HTTP) are faster; Transfer is UDP only.
  A future pass should prefer GetTexture for texture type=0 when the cap is available.
- Download / save-to-disk is not wired; next feature track.
- Upload (create/edit notecard, script) is not wired; later feature track.

### What Was Verified

- **Protocol plumbing**:
    - `TransferRequest` (source_type=2) baseline successfully retrieves global assets.
    - Correction from OpenSim source: `TransferRequest` (source_type=3 / `SimInventoryItem`) uses 101-byte params, not 85 bytes:
      `AgentID, SessionID, OwnerID, TaskID, ItemID, AssetID, AssetType, IsPriority`.
      OpenSim reads `TaskID` at offset 48, `ItemID` at 64, and the requested
      asset UUID at 80 before fetching from the asset service.
    - Status=1 in `TransferPacket` is now correctly treated as 'Done' rather than an error.
    - Asset data up to 80KB+ successfully received and reassembled across 130+ packets.
- **UI Integration**:
    - HUD successfully captures and passes `task_id` and `item_id` to the session layer.
    - Automated test script (verified locally then removed) successfully completed the full login -> object search -> inventory load -> asset fetch loop.
- `python3 -m pytest` → **479 passed, 0 failed**.

### Concrete Next Step

Perform a final manual visual check in the 3D viewer: select a scripted object, load its inventory, and "View" a script or notecard. Then proceed to the next feature track: **Download / Save to Disk**.


### Blocker: Task Inventory Asset Silence

While the protocol plumbing for `TransferRequest` is implemented and verified for global assets (`source_type=2`), requests for protected object inventory assets (`source_type=3`) currently result in simulator silence in the manual viewer run.

- **Status**:
    - `TransferRequest` (source=3) now dispatches the OpenSim-compatible 101-byte parameter block: `AgentID(16), SessionID(16), OwnerID(16), TaskID(16), ItemID(16), AssetID(16), AssetType(4), IsPriority(1)`.
    - `OwnerID` is resolved from `ObjectPropertiesFamily` before the request.
    - If the object-inventory listing reports a zero asset UUID, Vibestorm no
      longer sends a doomed transfer request. The Object Inspector marks the row
      as `asset withheld`, opens an explanatory Asset Viewer message, and logs
      `object_inventory.asset_withheld`.
    - OpenSim source shows zero asset IDs are intentional when the simulator
      withholds task inventory asset UUIDs because object inventory edit rights
      or script/notecard permissions are insufficient.
- **Known Working Case**:
    - An automated test once successfully fetched ~80KB for a `source_type=3` request, but results are inconsistent.
- **Top Hypotheses**:
    1. **Permission Denial**: The simulator may be silently dropping the request if the `OwnerID` or `AgentID` don't have view permissions for the specific `item_id`.
    2. **Xfer/Transfer Conflict**: The simulator may be ignoring new `TransferRequest`s while an `Xfer` (used for the initial inventory listing) is still technically open or being cleaned up.
    3. **Identifier Mismatch**: Verify if `TaskID` must be the object's root UUID or if it needs to be the specific part UUID for multi-part objects.
    4. **Zeroed AssetID**: OpenSim may send all zeros for `AssetID` in some task inventory listings. Source_type=3 still needs the requested asset UUID at offset 80, so zero IDs are currently treated as server-withheld assets rather than downloadable assets.

---




## Update 2026-05-09: First Inventory Manager UI

The next viewer track has started with user inventory before object
inspection/object inventory.

- `viewer3d` View -> Inventory now opens an "Inventory Manager" window instead
  of a flat text dump.
- The window is still read-only and uses the existing login/prelude
  `InventorySnapshotReady` data from `FetchInventoryDescendents2` /
  `FetchInventory2`.
- The left pane is a selection list with loaded folders, child folder entries,
  items, and resolved Current Outfit links. Child folders whose contents are
  listed but not fetched are marked `(not loaded)` / `F*`.
- Follow-up polish changed the left pane to a more traditional tree-like row
  format: folder rows are left-indented with `▾` / `▸`, loaded/unloaded folder
  glyphs (`◼` / `◻`), ordinary item bullets (`•`), and link arrows (`↗`).
  This is still backed by `pygame_gui.UISelectionList`, not a true native tree
  widget.
- Folder opening is now wired for user inventory. Selecting an unloaded child
  folder and pressing Open, or double-clicking it, calls
  `FetchInventoryDescendents2` for that folder, merges the returned
  `InventoryFetchSnapshot` into the existing snapshot, and republishes
  `InventorySnapshotReady` through the existing session event bridge.
- The right pane shows details for the selected folder/item: IDs, parent/owner
  fields, type/inventory type, flags, description, link status, and load state.
- Added `inventory_snapshot_rows()` as a pure row-model helper with tests, plus
  HUD selection/details/open tests.

Verification:

- `uv run ruff check src/vibestorm/viewer3d/hud.py test/test_viewer3d_hud_render_mode.py`
- `uv run --extra viewer pytest test/test_viewer3d_hud_render_mode.py -q`
- `uv run --extra viewer pytest test/test_inventory_caps_client.py test/test_viewer3d_hud_render_mode.py -q`

Known remaining inventory/tooling work:

- object inspector UI is now implemented with a read-only list of nearby objects and property joining.
- object inventory tree is protocol work after that: request/load inventory for selected nearby objects, then build editor/open/save/upload commands on top.

### Next Steps for Object Inventory

Goal:

- The View/Tools menu now has an "Object Inspector" window.
- The window provides a split-pane interface. The left pane shows nearby objects from `Scene.object_entities`, sorted by distance from `Scene.avatar_position` when available.
- The right pane shows grouped details joined from `SceneEntity` and `WorldView.objects` using `local_id_to_full_id`:
  - Identity: local ID, full UUID, display name
  - Transform: position, rotation, scale
  - Shape/render: pcode, kind, primitive shape, material, click action, default texture UUID, and per-face texture UUIDs
  - Object update/debug: variant, update flags, CRC, and data sizes
  - Properties: owner/group/permissions, name, description, sale fields from `ObjectPropertiesFamily`
- The bottom right pane contains an "Object Inventory" placeholder ("not requested yet").
- Tests were added in `test/test_viewer3d_object_inspector.py`.

Verification:

- `uv run ruff check src/vibestorm/viewer3d/hud.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_viewer3d_object_inspector.py -q`

Object inventory notes:

- Object inventory loading is now wired read-only for the inspector.
- The inspector bottom pane has a "Load Inventory" button. It dispatches
  `RequestObjectInventory(local_id)`, which queues `RequestTaskInventory`.
- The simulator's `ReplyTaskInventory` supplies a task UUID, serial, and xfer
  filename. Vibestorm now sends `RequestXfer`, confirms `SendXferPacket`
  packets, assembles the xfer payload, parses common `inv_item` blocks, and
  publishes `ObjectInventorySnapshotReady`.
- Empty `ReplyTaskInventory` filenames are treated as successful empty object
  inventory loads. This avoids leaving the inspector stuck in "request sent"
  when an object simply has no contents.
- Viewer3D prints object-inventory debug events to stdout while running:
  `task_inventory.request`, `reply`, `xfer.request`, `xfer.packet`,
  `xfer.confirm`, `xfer.unknown`, and `ready`. Use these lines to distinguish
  a missing simulator xfer from an xfer ID/packet parsing problem.
- `Scene.object_inventory_snapshots[local_id]` stores loaded object inventory,
  and the inspector displays item name/type/UUID in the bottom pane.
- Lazy user-inventory folder loads also materialize a successful empty response
  as a loaded folder with zero descendants, so the tree can distinguish
  "empty" from "not loaded yet".
- The parser is intentionally partial and read-only. It does not yet implement
  item open, asset download, save, upload, move, delete, script running state,
  or permission editing.

Verification:

- `uv run ruff check --select F,I src/vibestorm/world/object_inventory.py src/vibestorm/udp/messages.py src/vibestorm/udp/session.py src/vibestorm/udp/world_client.py src/vibestorm/bus/commands.py src/vibestorm/bus/events.py src/vibestorm/viewer3d/app.py src/vibestorm/viewer3d/hud.py src/vibestorm/viewer3d/scene.py test/test_object_inventory.py test/test_udp_messages.py test/test_world_client.py test/test_viewer3d_object_inspector.py`
- `uv run --extra viewer pytest test/test_object_inventory.py test/test_udp_messages.py test/test_world_client.py test/test_viewer3d_object_inspector.py -q`
- `uv run --extra viewer pytest`

## Update 2026-05-06: Terrain Heightmap + Surface Mesh

Viewer3D terrain work has moved past raw patch extraction.

- `src/vibestorm/world/terrain.py` now decodes standard 16x16 land
  `LayerData` patches all the way to height samples: libomv-compatible
  dequant table, copy-matrix reorder, two-pass IDCT, and final
  `mult/addval` height arithmetic.
- Important correction: the coefficient stream decoder now matches
  libopenmetaverse's real bit codes (`0` zero, `10` EOB, `110` positive,
  `111` negative). The earlier synthetic tests had encoded a different
  symmetric shape, so they were corrected at the same time.
- `RegionHeightmap` accumulates decoded land patches into a 256x256
  row-major sample array and tracks a `revision` for render-cache rebuilds.
- `viewer3d.Scene` consumes `LayerDataReceived` bus events, ignores non-land
  layers and other regions, and keeps the current `terrain_heightmap`.
- `PerspectiveRenderer` now builds a textured terrain heightfield mesh from
  the scene heightmap and draws it through the existing ground shader. It
  falls back to the flat region ground quad until terrain packets arrive.

Verification:

- `uv run pytest test/test_world_terrain.py test/test_viewer3d_scene.py -q`
- `uv run pytest test/test_viewer3d_perspective_gl.py -q`

Known remaining terrain gaps:

- Extended 32x32 patches are still rejected; only standard 16x16 land
  patches are decompressed.
- Wind/cloud layer data is surfaced but not rendered.
- No live OpenSim visual pass was run in this handoff; next concrete step is
  `./run.sh opensim` plus `./run.sh viewer3d`, then switch to 3D and confirm
  the ground surface is visibly elevated instead of flat.

Follow-up from the first live check:

- `viewer3d` now starts in 3D mode by default (`--render-mode 2d-map` is
  available for the old startup behavior).
- The viewer loop is capped at 20 FPS by default via `--max-fps 20`; pass
  `--max-fps 0` to disable the cap.
- The perspective renderer now draws terrain with a 1x1 fallback ground
  texture if terrain height data exists before the region map tile has loaded.
  This fixes the "no map tile means no terrain draw" path.
- A Diagnostics window is visible by default in 3D mode and available from
  Debug -> Diagnostics. It reports FPS, mode, region/map path, terrain
  dimensions/patch count/revision/min/max height, water level plus avatar
  under/above-water status, object/avatar/texture/chat counts.

Second follow-up from live debugging:

- The water plane now uses the parsed `RegionHandshake.WaterHeight` stored in
  `WorldView.region.water_height` instead of always using the default 20 m.
  The diagnostics window reports that same scene value.
- Basic 3D orbit inspection controls are wired:
  - right-drag rotates the orbit camera
  - mouse wheel changes orbit distance
  - Shift+right-drag pans the orbit target
  - Shift+PageUp/PageDown lifts/lowers the orbit target
  - Center/C now retargets orbit mode to the avatar/coarse self position

Third follow-up from live debugging:

- The water shader now applies subtle coordinate-based noise so the water
  plane is visually readable instead of a flat translucent sheet.
- Terrain heightfields now draw a bright green wire/grid overlay on top of the
  filled terrain mesh. This is intentionally texture-independent, so it should
  confirm whether `LayerData` has produced a mesh even when the map/terrain
  texture is missing or visually ambiguous.

Fourth follow-up for terrain diagnosis:

- `viewer3d` now accepts `--debug-terrain synthetic`. This seeds
  `Scene.terrain_heightmap` with a deterministic hill/valley/ripple surface
  and ignores live land `LayerData` while the override is active.
- Diagnostics now show terrain source (`live`/`synthetic`), min/max/mean,
  first patch keys, and the first four sample values. This should make it
  clear whether we are failing before GL (bad/flat decoded samples) or in GL
  (synthetic terrain also fails to draw).
- Follow-up after synthetic showed only grid lines: terrain fill is now a
  solid untextured green material whenever a heightmap exists. The textured
  ground path is left for the no-heightmap flat floor and future texture work.
- Follow-up after synthetic still looked flat: rendered terrain gained
  height-based color grading and a `--terrain-z-scale` option. It temporarily
  defaulted to `4.0` while geometry was suspect; later live validation moved
  the default back to real meter scale.
- Follow-up after synthetic still looked flat again: `Scene.apply_region_changed`
  was clearing the synthetic debug heightmap during the initial live
  `RegionChanged` event. Synthetic terrain now survives region changes, while
  normal live terrain is still cleared on region change.
- To diagnose live flat terrain, `RegionHeightmap.latest_layer_stats` now records
  the latest land LayerData packet's patch positions, ranges, DC offsets,
  prequant values, nonzero coefficient count, max absolute coefficient, and
  decoded per-packet height min/max/mean. Diagnostics shows these as `layer:`
  and `coeff:` lines.

Fifth follow-up for live flat terrain:

- Debug -> Sim Debug opens a "Sim Debug Heightmap" window showing the current
  `Scene.terrain_heightmap.samples` as a black/white normalized image. This is
  intentionally independent from the 3D mesh/material path; if live terrain is
  still flat in-world but the image has contrast, the bug is in mesh upload or
  render scaling. If the image is uniform gray, the decoded server heightmap is
  actually flat at the sample-array level.
- The heightmap window status line reports source (`live`/`synthetic`),
  dimensions, patch count, and min/max height for quick screenshots/logging.
- Focused verification: `uv run --extra viewer pytest
  test/test_viewer3d_hud_render_mode.py`.

Sixth follow-up after Sim Debug showed uniform gray live terrain:

- The root cause was the terrain `BitPack` bit order. OpenMetaverse writes
  integer fields as little-endian byte chunks while retaining MSB-first
  ordering inside each chunk. Live OpenSim LayerData starts with `08 01 10 4c`
  for stride 264, patch size 16, land type 0x4c; the previous reader treated
  the whole stream as MSB-first.
- `BitPack` and `BitPackWriter` now match OpenMetaverse chunk order. Tests pin
  the live header prefix (`0801104c`) and coefficient prefix-code bytes
  (`10 -> 80`, `110 -> c0`, `111 -> e0`).
- Saved LayerData previews now decode to plausible headers such as stride 264,
  patch size 16, land type 0x4c, ranges 1/3, and valid patch coordinates.

Seventh follow-up after live terrain had plausible heights but wrong shape:

- The first `BitPack` correction still mishandled non-byte-aligned multi-byte
  values. OpenMetaverse continues a split input byte across output-byte
  boundaries; after `PackBits(2, 2)`, `PackBits(0x123, 10)` must produce
  `88 d0`. The Python reader/writer now pins and matches that behavior.
- `END_OF_PATCHES` is decimal `97` (`0x61`), not hex `0x97`; the old constant
  came from misreading the name/comment. Tests now pin the marker byte.
- Added an OpenSim-generated sloped-patch fixture using
  `OpenSimTerrainCompressor.CreatePatchFromTerrainData`; Python decode
  recovers `height = 20 + x * 0.05 + y * 0.02` within about 0.01 m. This
  verifies coefficient magnitudes, EOD, dequant, copy matrix, IDCT, and
  per-patch placement against the actual OpenSim compressor.

Eighth follow-up after live terrain shape looked correct:

- `--terrain-z-scale` now defaults to `1.0` again so rendered terrain uses
  real meter scale. The option remains available for debugging exaggerated
  relief, e.g. `--terrain-z-scale 4`.

Ninth follow-up for render-debug controls:

- View -> Render Settings now opens a small render settings window. It exposes
  checkbox-style buttons for Terrain Surface, Mesh Lines, Water, and Objects.
  These write through to `Scene.render_terrain`, `render_terrain_lines`,
  `render_water`, and `render_objects`.
- The same window has a Water opacity slider. `Scene.water_alpha` defaults to
  `0.72`, making water less transparent than the original debug plane while
  still leaving submerged terrain readable.
- The renderer honors those scene flags in `PerspectiveRenderer.render_gl`.
  Mesh lines can now be hidden without disabling terrain fill; water and
  object rendering can also be isolated while debugging.

Tenth follow-up for first-pass lighting:

- `SimulatorTimeSnapshot` now retains the UDP `SunDirection`, and
  `viewer3d.Scene` surfaces it as `Scene.sun_direction` alongside
  `sun_phase`.
- The 3D renderer applies ambient + directional lighting to object meshes.
  Primitive normals are currently approximated from local vertex position, so
  this is a visual depth cue rather than final face-accurate prim shading.
- Filled terrain now uses a fragment normal derived from the rendered height
  surface and shades against the same sun direction. Mesh lines remain
  unlit/debug-bright.
- Texturing has not started yet beyond the existing map-tile/fallback terrain
  texture path. The next concrete rendering step is proper terrain/prim texture
  interpretation, starting with full `TextureEntry` decode and asset lookup.

Eleventh follow-up for first-pass texturing:

- When a terrain heightmap exists and the region map tile has been cached, the
  3D renderer now drapes that map tile over the terrain mesh instead of using
  only the debug height-color fill. If no map tile is available, the existing
  untextured height-color fill remains the fallback.
- The live session now watches `WorldView.objects` for non-zero
  `default_texture_id` values, fetches one pending texture at a time via the
  existing `GetTexture` capability, decodes JPEG2000, and caches PNGs under
  `local/texture-cache/<uuid>.png`.
- `texture.cache.ok` session events are bridged to a typed
  `TextureAssetReady` bus event. `viewer3d.Scene` records those paths in
  `Scene.texture_paths`.
- `PerspectiveRenderer` groups primitive draws by shape and available default
  texture. Textured prims use a coarse generated UV projection in the shader;
  this is intentionally first-pass only and not a replacement for proper
  `TextureEntry` per-face UV/material decode.
- Verification: `uv run --extra viewer pytest test/test_world_client.py
  test/test_udp_session.py test/test_viewer3d_scene.py
  test/test_viewer3d_perspective_gl.py`.

Twelfth follow-up for object UV scaling:

- The first object texture shader projected every prim texture through local
  XY. That made side faces collapse to a single texture column/row and looked
  like the object was sampling one pixel.
- Object texture sampling now uses generated per-face projection in the shader:
  X-facing faces sample Y/Z, Y-facing faces sample X/Z, and Z-facing faces
  sample X/Y. UVs are clamped to the unit face instead of wrapped at exact
  edges.
- Added a matching pure `generated_texture_uv()` helper and tests so the
  intended projection stays pinned.
- This still is not full SL `TextureEntry` material fidelity. It fixes the
  gross scale/projection issue for default-textured prim draw groups; per-face
  image IDs, repeats, offsets, rotations, alpha, and glow still need real
  `TextureEntry` decode and mesh/material batching.

Thirteenth follow-up for per-face texture plumbing:

- Added `vibestorm.world.texture_entry` with a first-pass `TextureEntry`
  parser. It decodes the image UUID section: default texture UUID plus
  face-mask texture UUID overrides using the OpenMetaverse MSB-first 7-bit
  mask encoding.
- `ObjectUpdateEntry`, `WorldObject`, and `viewer3d.SceneEntity` now retain the
  parsed `TextureEntry` object while preserving the existing
  `default_texture_id` field for fallback rendering.
- Improved-terse texture-entry payloads also update the retained parsed
  texture entry when they carry at least a default UUID.
- The renderer is not yet using per-face overrides. The next concrete step is
  to add logical face IDs to cube mesh triangles and batch/draw cube faces by
  `texture_entry.texture_for_face(face_index)`, falling back to the default
  texture for shapes without face IDs.

Fourteenth follow-up for cube per-face texture rendering:

- Cube rendering now draws six logical face submeshes. Each face resolves its
  texture through `SceneEntity.texture_entry.texture_for_face(face_index)` and
  falls back to `default_texture_id`/tint if the override or cached asset is
  unavailable.
- The object texture fetch queue now includes face-override texture UUIDs from
  parsed `TextureEntry`, not only default texture IDs.
- Non-cube primitive shapes still use the default texture draw path. That keeps
  spheres/cylinders/tori/prisms stable until their SL face mapping is modeled.
- Focused verification: `uv run --extra viewer pytest
  test/test_viewer3d_perspective_gl.py test/test_udp_session.py`.

## Update 2026-05-04: 3D Viewer Fork

3D viewer work has begun in a forked package rather than as an in-place
refactor of the 2D viewer.

- `src/vibestorm/viewer3d/` is a byte-for-byte copy of `src/vibestorm/viewer/`
  with intra-package imports retargeted and the window caption changed to
  "Vibestorm 3D Viewer". Behavior is identical to the 2D viewer today.
- `./run.sh viewer3d` runs the fork; `./run.sh viewer` is unchanged.
- The 2D `viewer/` package is now the stable reference. We don't intend to
  invest further in it; it stays for visual comparison and as a known-good
  baseline. Tests for `viewer/` still pass.
- The full plan, including renumbered implementation order with the fork as
  step 0, lives in `docs/viewer-3d-plan.md`.

Steps 1a, 1b-i, and 1b-ii are done.

- 1a: `viewer3d.scene` now exposes a renderer-agnostic `SceneEntity`
  (replacing `Marker`) with `kind`, full quaternion `rotation`,
  `default_texture_id`, `tint`, and a `shape` field. `Scene.sun_phase` is
  surfaced from `WorldView.latest_time`. `object_entities` /
  `avatar_entities` replace the old marker dicts.
- 1b-i: protocol fix. The `ObjectUpdate` parser had two self-cancelling
  off-by-one bugs (22-byte path/profile block, U16 ExtraParams length).
  Fixed: the block is decoded as 23 bytes via a new `PrimShapeData`
  dataclass, and ExtraParams uses U8 length per template. Side effect:
  `default_texture_id` is now the real UUID instead of being shifted
  left by one byte with a leading `0x00`. `docs/reverse-engineered-
  protocol.md` corrected; `test/fixtures/live/index.json` regenerated
  (now 43 captures vs 8).
- 1b-ii: `SceneEntity.shape` is now populated from real wire data via a
  new `classify_prim_shape(path_curve, profile_curve)` helper covering
  cube/sphere/cylinder/torus/prism/ring/tube. The OpenSim default sphere
  fixture classifies as `"sphere"`.

Test suite now 285 tests, all green. The 2D viewer reference under
`src/vibestorm/viewer/` is untouched.

Step 2 is done: `viewer3d/renderer.py` defines a `ViewerRenderer` protocol
plus a `TopDownRenderer` that wraps today's 2D draw. The app loop now
routes `update` / `render` / `clear_caches` through the renderer instead
of calling `render_scene` directly. Behavior unchanged. 289 tests, all
green.

Step 3 is done: "Render: 2D Map" and "Render: 3D" buttons in the View
menu, HUD tracks `render_mode`, status bar shows the active mode, and
selecting 3D posts a chat alert ("3D mode is not implemented yet").
296 tests, all green (7 HUD tests verified under `./run.sh test`).

Step 4 is done: `Camera3D` is a mode-aware camera with a Map mode that
reproduces today's pan/zoom math bit-for-bit, plus state fields for
Orbit/Eye/Free modes (yaw, pitch, distance, eye_position, target).
`Camera = Camera3D` alias preserves existing imports. The HUD render-
mode callback now also calls `camera.set_mode(...)`. 311 tests, all
green.

**The pre-3D refactor (steps 1a, 1b-i, 1b-ii, 2, 3, 4) is complete.**

Next planned step (`viewer-3d-plan.md` step 5): moderngl bootstrap. Add
the dependency behind a `viewer3d` extra in `pyproject.toml`, open a
hybrid `pygame.OPENGL | pygame.DOUBLEBUF` window, and draw a single
textured fullscreen quad (the cached region map tile) plus the existing
pygame_gui HUD on top. Goal: validate the GL+HUD compositing path
before any geometry lands.

## Summary

The bird's-eye viewer now has a runnable pygame v1. Session boots, fetches
its region map tile, caches it as PNG, surfaces inbound IM/Alerts in the
event stream, and the 2D viewer consumes `WorldView` + the cached map tile
directly.

## What Is Wired

Per session, automatically:
1. `RegionHandshake` → `RegionHandshakeReply` + `MapBlockRequest` for the
   current region's grid coords (`region_x // 256`, `region_y // 256`).
2. `MapBlockReply` is parsed; the entry matching our grid coords yields a
   `MapImageID` stashed as `session.region_map_image_id`.
3. The main loop polls for (GetTexture URL + image_id + not yet fetched) and
   runs `_fetch_and_cache_region_map`: HTTP GET → J2K decode → PNG written to
   `local/map-cache/<image_id>.png`. Path is exposed as
   `session.region_map_path` and surfaces in `SessionReport.region_map_path`.
4. Inbound `ChatFromSimulator` (existing), `ImprovedInstantMessage`,
   `AlertMessage`, and `AgentAlertMessage` all emit `chat.*` session events
   with the decoded text.
5. `LiveCircuitSession.build_chat_packet(text, *, chat_type=1, channel=0)`
   returns a ready-to-send packet (reliable + zerocoded) and emits a
   `chat.outbound` event.
6. `WorldClient` queues UI-built outbound packets; `run_live_session` drains
   that queue into the active UDP socket.
7. `WorldClient` publishes `InventorySnapshotReady` after the caps prelude
   fetches the login inventory/current-outfit snapshot.
8. `TeleportLocation` commands build reliable `TeleportLocationRequest`
   packets and queue them for the active circuit.
9. `src/vibestorm/viewer/` contains the pygame viewer:
   - `camera.py`: world/screen transform, zoom, pan, fit-region.
   - `scene.py`: render-state aggregation from typed bus events + `WorldView`,
     including self-position and inventory snapshot state.
   - `render.py`: map tile, grid, region border, object/avatar markers.
   - `hud.py`: top main-menu strip, bottom status bar, resizable chat,
     movement-help, teleport, options, and inventory windows.
   - `input.py`: movement keys, zoom wheel, right-drag pan, chat focus.
   - `app.py`: login + live session task + pygame loop.
10. `docs/viewer-help.md` is loaded into Help -> Movement Help.

## How to Verify

Run a live session against local OpenSim and check the report tail:

```
./run.sh opensim &
./run.sh session
```

Look for:
- `map[tile]=cached path=...` (success) — or `image_id_only` / `none events=...`
- A PNG appearing under `local/map-cache/<image_id>.png` matching the region's
  prerendered map.

Run the GUI:

```
./run.sh opensim &
./run.sh viewer
```

Expected v1 behavior:

- cached map tile as the region background once `MapBlockReply` + `GetTexture`
  complete
- colored markers for objects and avatars from `WorldView`
- WASD/arrows update movement control flags; mouse wheel zooms; right-drag pans
- Debug -> Center or `C` recenters on the avatar/coarse self position
- Enter focuses the chat window's input; submitting text sends `ChatFromViewer`
- the status bar shows avatar position, sim, parcel placeholder, map/object/avatar/chat counts
- Help -> Movement Help opens the 2D movement help file
- View -> Inventory shows the first read-only inventory snapshot from login/current-outfit fetches
- Tools -> Teleport sends a local `TeleportLocationRequest` to the current region handle
- UI scale is automatic from desktop size: 1920x1080 is 1x, 3840x2160 is 2x.
  Override with `./run.sh viewer --ui-scale N` if needed.

## What Remains for the Bird's-Eye Plan

- Full `TextureEntry` section-walking parser (default color first, then per-face).
- `ParcelOverlay` 4 KB bitfield decode → plot-edge polylines.
- Parcel status/name wiring after parcel metadata is decoded; the HUD currently says
  `Parcel: unknown`.
- Real inventory/asset management: folder browsing beyond the first snapshot, create/upload
  flows, asset permissions, and server-side store/update actions.
- Visual live pass against OpenSim to tune marker scale/colors, status text,
  main-menu contents, teleport behavior, inventory formatting, and chat-window persistence.

These are independent and can land in any order.

## Update 2026-06-22: Mesh Normals / UVs / Material Groups

`src/vibestorm/assets/sl_mesh.py` now decodes more than positions+indices per
submesh:

- `DecodedSLMesh.normals` — per-vertex normals. Decoded from the submesh
  `Normal` u16 array (domain fixed at -1..1); when absent, computed as smooth
  per-vertex normals from the triangle geometry.
- `DecodedSLMesh.uvs` — per-vertex `TexCoord0` (u16, domain from
  `TexCoord0Domain`, default 0..1); zero-filled when absent.
- `DecodedSLMesh.material_groups` — one `MeshMaterialGroup(face_index,
  index_start, index_count)` per submesh. SL submeshes map 1:1 to prim faces,
  so `face_index` is the `TextureEntry` slot for per-face material/texture
  assignment. Indices are rebased onto the combined vertex buffer.

These fields are additive; existing `.vertices`/`.indices`/`.submesh_count`
consumers are unchanged.

### Next (live, visual) step

The shared shape shader in `viewer3d/perspective.py` fakes normals via
`local_normal = normalize(in_pos)` (no `in_normal` attribute) and uploads the
mesh VBO as positions-only (`(vbo, "3f", "in_pos")`). To actually light meshes
correctly: add an optional `in_normal` attribute + a uniform flag to the shape
program, interleave `decoded.normals` into the mesh VBO, and bind per-face
texture groups using `material_groups`. This needs the GL viewer for visual
verification, so it was not bundled with the decoder change.

## Update 2026-06-22: Parcel / EventQueue / Animation / Sound Decode Pass

Written up 2026-08-14 from commits `5c1b75d..d7cb39d`; the session ended without
a handoff entry, so this reconstructs it. ~2250 lines added across 13 files.

### What Changed

**Parcel** (`src/vibestorm/world/parcel_overlay.py`, new)

- `decode_parcel_overlay` reassembles the N `ParcelOverlay` packets into a
  region-wide 4 m LandUnit grid: `ownership_at` / `ownership_at_meters`
  (PUBLIC/OTHER/GROUP/SELF/FOR_SALE/AUCTION) plus `border_segments`
  (west/south property lines in region meters). Cell ordering mirrors OpenSim
  `LandChannel.cs` / `LandManagementModule.cs` — row-major, y south→north
  outer, x west→east inner.
- `decode_parcel_bitmap` turns the `ParcelProperties` `Bitmap` field into a
  per-parcel membership mask over the same grid (`ParcelBitmap.contains` /
  `contains_meters` / `bounds_units` / `cell_count`). Bit order mirrors
  `LandObject.ConvertBytesToLandBitmap` — linear index `y*edge+x`, LSB-first
  per byte.
- `parse_parcel_properties` (`udp/messages.py`) decodes the `ParcelData` block
  through `GroupID`: ownership, AABB extent, Bitmap, area, prim counts, parcel
  flags, sale price, and the Name/Desc/MusicURL/MediaURL strings. Trailing
  single blocks past `GroupID` are **not** decoded.
- The two pair up: the per-parcel Bitmap says which cells are mine, the
  region-wide overlay grid says who owns each cell.

**EventQueueGet** (`src/vibestorm/event_queue/events.py`, new)

- `decode_event_queue_payload` turns a parsed EQG LLSD map into typed events:
  `EnableSimulator`, `EstablishAgentCommunication`, `TeleportFinish`,
  `CrossedRegion`, `ScriptRunningReply`, `ObjectPhysicsProperties`,
  `AgentGroupDataUpdate`, plus `UnknownEvent` for unrecognized names, and the
  ack id.
- `caps.llsd.parse_xml_value` gained binary-tag support (base64 default,
  base16/base85). OpenSim's LLSD encoder emits uint/ulong as big-endian binary
  blobs (region handles, sizes, flags, `GroupPowers` u64) and IPs as 4 binary
  bytes; those are coerced back to ints / dotted-quad IPs.
- `AgentGroupDataUpdate` merges the parallel `NewGroupData` array's
  `ListInProfile` flag into each membership by index, matching OpenSim's split
  encoding.
- Shapes verified against OpenSim `EventQueueGetHandlers.cs` and
  `LLSDxmlEncode.cs`.

**Animation / sound** (`src/vibestorm/udp/messages.py`)

- `parse_avatar_animation` — High #20 (wire `0x14`). Sender avatar id plus the
  running animation list (AnimID + sequence id), with the parallel
  `AnimationSourceList` ObjectID merged per index when present. Three Variable
  blocks, each with a 1-byte count.
- `parse_object_animation` — High #30 (`0x1E`). Same Sender/AnimationList shape
  minus the source/event lists.
- `parse_sound_trigger` — High #29. One-shot world sound: sound/owner/object/
  parent ids, region handle, position, gain.
- `parse_attached_sound` — Medium #13. Object-bound: sound/object/owner ids,
  gain, flags.
- `parse_attached_sound_gain_change` — Medium #14 (ObjectID + Gain).
- `parse_preload_sound` — Medium #15 (Variable DataBlock of ObjectID/OwnerID/
  SoundID entries).

**Wiring** (`udp/session.py`, `udp/world_client.py`, `bus/events.py`)

- `LiveCircuitSession.handle_incoming` dispatches all of the above instead of
  letting them fall through as unknown. `ParcelProperties` →
  `latest_parcel_properties` + `parcel.properties`; `ParcelOverlay` →
  `parcel_overlay_packets[seq]` + `parcel.overlay` (new `parse_parcel_overlay`
  body parser: SequenceID + Variable-2 Data); animations and sounds → their
  `avatar.*` / `object.*` / `sound.*` events. Decode failures record a
  `.decode_error` event rather than throwing.
- New typed bus events: `ParcelPropertiesReceived`, `ParcelOverlayReceived`,
  `AvatarAnimationReceived`, `ObjectAnimationReceived`, `SoundTriggered`,
  `AttachedSoundReceived`, `AttachedSoundGainChanged`, `PreloadSoundReceived`,
  each carrying the decoded dataclass.
- Session stores the latest decoded animation/sound message and fires
  `on_event` synchronously *after*, so the `WorldClient` bridge reads the
  just-set slot — the same "session is source of truth" pattern as
  `terrain.layer_data`.

### What Was Verified

Unit tests only — no live sim run. Session dispatch was exercised end-to-end
through `handle_incoming` with synthetic packets, and the bus path through
`session._record_event` → subscriber. Full suite passes (619 tests as of
2026-08-14). Nothing here has been seen against live OpenSim traffic.

### Current Boundary — three decoders are written but unreachable

This is the important part for whoever picks this up. Grep confirms:

1. **The whole typed EQG module is dead code outside tests.**
   `decode_event_queue_payload` has no caller in `src/`. The poll loop
   (`event_queue/client.py`, `poll_once`) still returns raw LLSD. Nothing
   consumes `EnableSimulator`/`TeleportFinish`/`ScriptRunningReply` yet.
2. **The parcel grid decoders are never called.** `session.parcel_overlay_packets`
   accumulates raw per-sequence bytes and publishes them, but no one calls
   `decode_parcel_overlay` to reassemble the grid, and no one calls
   `decode_parcel_bitmap` on `latest_parcel_properties.bitmap`.
3. **The HUD still says `Parcel: unknown`.** `viewer3d/scene.py:246` declares
   `parcel_name` and `:275` sets it to `None` — nothing ever assigns it, even
   though `ParcelProperties.name` is now decoded and on the bus.

So the decode work landed but the consumer side did not. That is the shortest
path to visible value.

### Concrete Next Step

Close boundary 3 first — it is small and makes the parcel work observable:
subscribe `viewer3d` to `ParcelPropertiesReceived`, set `scene.parcel_name`
from the decoded name, and confirm the HUD status bar shows a real parcel name
against local OpenSim (`./run.sh tester viewer3d`). Then reassemble the overlay
grid from `parcel_overlay_packets` and draw `border_segments` as plot edges —
that closes the long-standing bird's-eye "ParcelOverlay → plot-edge polylines"
item below.

But read the 2026-08-14 live-verification note below first: `ParcelProperties`
never arrives unsolicited, so step one is sending a request, not subscribing.

Wiring the typed EQG decoder into `poll_once` is independent and can land in
any order.

## Update 2026-08-14: First Live Verification Of The Parcel Decoders

First run of the 2026-06-22 work against live OpenSim (`Vibestorm Test`,
`127.0.0.1:9000`). Two 90–100s `./run.sh session --verbose` runs plus a
scripted bus-subscriber harness. No code changed.

### What Was Verified Live

- Session is healthy end to end: login → caps → UDP handshake →
  `AgentMovementComplete` → 90s of traffic → clean `LogoutRequest`/`LogoutReply`.
  293 messages, 9 seed caps, bake upload accepted (`uploaded:5 serial:5`),
  33 objects tracked with 32 carrying properties, map tile cached.
- `ParcelOverlay` **decodes correctly on real bytes.** The sim sent 4 packets
  (seq 0–3, 1024 B each) = 4096 cells = a 64×64 grid, exactly the predicted
  256 m ÷ 4 m LandUnit layout. `decode_parcel_overlay` reassembled it without
  error: `cells_per_edge=64`, ownership histogram `{other: 4096}` (the test
  region is one parcel owned by another account), and 128 border segments —
  64 west edges plus 64 south edges, i.e. exactly the region perimeter, which
  is what a single region-wide parcel should produce. First segments
  `(0,0,0,4)`, `(0,0,4,0)`, `(4,0,8,0)` confirm the west-edge/south-edge
  meter geometry.
- `AvatarAnimation` decodes live (`anims=1`, correct sender agent id).
- Session dispatch emits `parcel.overlay` and `avatar.animation` events with no
  `.decode_error` anywhere in either run.

### The Finding That Changes The Next Step

**`ParcelProperties` never arrives on its own — 0 received across both runs.**
No `ParcelPropertiesRequest` encoder existed anywhere in `src/`, so parcel data
was simply never asked for. The HUD `Parcel: unknown` problem was therefore not
a missing subscription.

> **Superseded on the same day — read the 2026-08-14 follow-up below.** The
> conclusion drawn here, that "the UDP path is the one to build against", is
> backwards. `ParcelProperties` is delivered over the *event queue* only. The
> request is still needed and still goes out over UDP; the reply does not come
> back that way.

### Still Unverified

`ObjectAnimation` and all four sound messages did not appear in live traffic —
a quiet test region with one avatar and no scripted sound emitters. They remain
synthetic-test-only. `decode_parcel_bitmap` is likewise unexercised on real
data, since it needs a `ParcelProperties` Bitmap to decode.

### Pre-existing, Not A Regression

The event-queue poll times out once at session start (`event queue poll timed
out after 5.0s`) and is not retried — no successful EQG poll occurs during a
normal session. That is the long-poll shape, but it means the EQG path is
effectively dormant, which is worth confirming when the typed decoder gets
wired into `poll_once`. One `GetTexture` 404 also appears for a texture the sim
does not hold.

*(That dormant queue turned out to be the actual root cause — see below.)*

## Update 2026-08-14: Parcel Identity, End To End

The HUD now shows a real parcel name. Three separate gaps were stacked behind
that one symptom, and each had to be closed before the next was visible.

### 1. Nothing ever asked for parcel data

Added `encode_parcel_properties_request` (`udp/messages.py`,
ParcelPropertiesRequest = Medium/11, Zerocoded) and autosend it after
`RegionHandshake`, beside `MapBlockRequest`, guarded by
`parcel_properties_request_sent`.

The bounds matter. `LandManagementModule.ClientOnParcelPropertiesRequest`
takes two paths: a box no wider than one 4 m LandUnit resolves a single
parcel, while anything larger is divided into LandUnits and walked, replying
once per distinct parcel found. So a region-sized box `(0, 0, 256, 256)`
enumerates every parcel. The bounds must stay inside the region — OpenSim
drops the request outright if `end > regionSize`, with no error to the client.
`SessionConfig.region_size_meters` (default 256) sizes the box; varregions
need it raised.

### 2. The event queue was dormant

This was the real root cause. `_run_caps_prelude` polled `EventQueueGet`
exactly once and then never again, so *every* EQG-only message was dropped on
the floor for the entire session.

Added `_run_event_queue_loop`: a background task started after the prelude,
cancelled at session teardown, that polls in a loop and acks each batch by id
(which is how the simulator knows it may drop delivered events). Long-poll
timeouts on a quiet queue are the normal idle shape and are now recorded as
`eventqueue.poll_timeout` rather than as errors; a 404 after logout is the
capability being torn down and is no longer reported at all. New knobs:
`SessionConfig.event_queue_polling` and `event_queue_timeout_seconds`.

### 3. `ParcelProperties` is an EVENT QUEUE message — the earlier note was backwards

`LLClientView.SendLandProperties` builds an EQG event via `eq.StartEvent(
"ParcelProperties", ...)` and returns early if no event queue exists. There is
**no `ParcelPropertiesPacket` send path anywhere in `LLClientView.cs`.**

Consequences worth internalising:

- `udp.messages.parse_parcel_properties`, written 2026-06-22, **never fires
  against OpenSim.** It is not wrong, just unreachable on this server. Keep it
  for SL/other-server compatibility, but do not expect it to run.
- The UDP `ParcelProperties` template entry is marked `UDPDeprecated`, and that
  turns out to be literally true on OpenSim.
- The request goes out over UDP; the reply comes back over HTTP. Neither
  transport tells the whole story on its own — which is exactly why the
  earlier single-transport reasoning went wrong in both directions.

Added `EVENT_PARCEL_PROPERTIES` / `ParcelPropertiesEvent` to
`event_queue/events.py`, decoding into the **same** `ParcelPropertiesMessage`
the UDP parser produces, so `ParcelPropertiesReceived` and every downstream
consumer are unchanged regardless of transport.

### 4. Consumer side

`LiveCircuitSession.handle_event_queue_batch` folds a decoded batch into
session state (parcel replies also land in `parcel_properties_by_local_id`,
since a region-wide request draws one per parcel). `viewer3d`'s `Scene` gained
`apply_parcel_properties`, wired in `_wire_scene`, preferring the parcel whose
Bitmap actually covers the avatar and falling back to the first reply while the
avatar position is unknown.

### One Live Bug Found And Fixed

`_as_uuid` raised on `''`. OpenSim writes an unset UUID as an empty `<uuid/>`,
which parses to the empty string rather than the all-zero form — hit
immediately on `GroupID` for an ungrouped parcel, and it killed the decode of
both parcel events. Blank now yields the null UUID; genuinely malformed values
still raise.

### What Was Verified Live

- `parcel.properties local_id=1 name='Your Parcel' area=65536
  owner=6571e388-…` — two replies, one from OpenSim's own login-time land info
  and one from our request.
- `decode_parcel_bitmap` on the live 512-byte Bitmap: 4096 cells, edge 64,
  bounds `(0,0,63,63)`, `contains_meters(128,128)=True`. Consistent with the
  overlay grid's 128 perimeter segments and with `area=65536` — a single
  region-wide parcel, three independent decoders agreeing.
- A headless `Scene` driven through the real `_wire_scene` renders
  `Parcel: Your Parcel`.
- 630 tests pass; no new lint findings.

## Update 2026-08-14: Parcel Borders (2D)

`Scene` now accumulates the sequenced `ParcelOverlay` packets and calls
`decode_parcel_overlay` after each piece, keeping `parcel_overlay` and
`parcel_borders` once the set is whole. Retrying per-packet rather than waiting
for an expected count means a late or reordered packet still completes the grid.
State clears on region change alongside the map tile.

The 2D top-down path draws the segments (`_draw_parcel_borders` in
`viewer3d/render.py`, between the region border and the entity markers), gated
on `Scene.render_parcel_borders`. That closes the bird's-eye
"`ParcelOverlay` → plot-edge polylines" item that had been open since the
original plan.

Verified live: 4 packets → grid decoded → **128 border segments** in a `Scene`
driven through the real `_wire_scene`, which for this single region-wide parcel
is exactly the region perimeter. 634 tests pass.

### 3D Borders Too — And A Note On "Needs Visual Verification"

Landed in the same pass. `perspective.py` gained a line VAO for
`scene.parcel_borders` reusing the terrain-line program, with endpoints lifted
onto the heightmap (or the flat ground plane before terrain arrives) plus a
0.35 m offset so lines follow the land instead of z-fighting it. The VAO
rebuilds only when the segments or terrain revision change.

**Worth knowing for every future "this needs the GL viewer" item in this repo:
`moderngl.create_standalone_context()` works on this machine** (NVIDIA GT 1030,
GL 3.3). So GL work can be verified headlessly by rendering into an offscreen
framebuffer and reading pixels back — no display, no manual look-and-see. The
new tests in `test_viewer3d_perspective_gl.py` render the region perimeter with
and without borders, assert pixels changed, and assert the brightest changed
pixel is green-dominant. That is a real check that geometry rasterized, not
just that a VAO was allocated.

This applies directly to the still-open mesh-normals item below, which was
deferred for exactly this reason.

### Mesh Normals Landed Too

With headless GL available, the deferred mesh-normals item was closed in the
same pass. Every VAO bound to the shape program now supplies interleaved
`"3f 3f"` position+normal. Primitive shapes and sculpt meshes bake the old
`normalize(in_pos)` approximation into their buffer (so their lighting is
byte-identical to before), while decoded mesh assets pass `decoded.normals`
through.

One test-design trap worth recording: the first version of the verification
compared a mesh **with** a `Normal` array against one **without**, and they
shaded identically — because `decode_sl_mesh_asset` *computes* normals from the
triangles when the asset omits them. The meaningful contrast is authored-vs-
computed, so the test now authors normals pointing +X on a triangle lying in
the XY plane (which the decoder would otherwise compute as +Z) and asserts the
frames differ.

### Per-Face Materials And The Event Queue, Consumed

Both remaining "decoded but dropped" items from the June pass are closed.

**`material_groups`.** Mesh index buffers now split along the decoded material
groups, and each slice draws with that prim face's `TextureEntry` override —
the treatment cube faces already had. Per-face buffers share the parent VBO;
only IBOs and VAOs are per face, and they rebind when the instance buffer
grows. Single-group meshes keep the one-draw-call path. The GL test paints a
two-submesh mesh red on face 0 and blue on face 1 and asserts both appear;
**both the structural and the pixel assertions were checked to fail when the
feature is stubbed out**, so neither passes vacuously. That check is cheap and
worth repeating for any future pixel-level test — a render test that cannot
fail is worse than no test.

**The event queue.** Typed EQG events now publish as `EventQueueEventReceived`,
one carrier rather than one bus event per message, because the queue's
vocabulary is open-ended — `UnknownEvent` publishes too, so a consumer can
handle something this client does not decode yet. `viewer3d` reports the two
that mean something to a person: `TeleportFinish` and `ScriptRunningReply`.

### Concrete Next Step

**Live-verify object sync**, the oldest open track (implemented 2026-05-25,
never confirmed against a running sim). Everything it needed now exists:
`ScriptRunningReply` arrives over the live queue and surfaces in viewer chat,
so the server-side "script compiled OK" confirmation is finally observable.
Select a scripted object in `./run.sh tester viewer3d`, Save Text, edit the
`.lsl`, upload, and watch the chat line.

### Mesh UVs (2026-08-14)

Also closed. Mesh vertex buffers carry `in_mesh_uv` and a `u_use_mesh_uv`
uniform selects authored coordinates per draw; primitives and sculpts keep the
generated fallback.

This forced a decoder change worth knowing about. `DecodedSLMesh.uvs` is
zero-filled when a submesh omits `TexCoord0`, so an authored `(0, 0)` was
indistinguishable from a missing array — and defaulting to the authored path
would sample an untextured mesh at a single texel, visibly *worse* than the
fallback. `DecodedSLMesh.has_authored_uvs` now records the difference, true
only when every submesh carried its own array.

That is the same trap as the normals work: **this decoder fills in defaults for
absent data, so "the field is populated" never means "the asset supplied it."**
Check for a dedicated flag before treating decoded geometry data as authored.

A GL-testing note: the first version of the test pinned UVs to exactly 1.0 and
the triangle rendered magenta rather than blue — at the texture edge,
wrap-around linear filtering blends the last texel with the first. Sample away
from the seam.

## Update 2026-08-14: ExtraParams Beyond Sculpt/Mesh

`vibestorm/world/extra_params.py` decodes the remaining prim feature blocks:
flexi (`0x10`), light (`0x20`), projector (`0x40`) and reflection probe
(`0x90`). Layouts from OpenSim `PrimitiveBaseShape`.

Two quantisations are easy to get wrong and are covered by dedicated tests:

- **Flexi softness is a 2-bit level split across the TOP bit of two different
  bytes**, whose low 7 bits carry tension and drag. Read the bytes naively and
  you get both a wrong softness and a wrong tension.
- **Light intensity rides in the colour's alpha channel**, so `LightParams.color`
  is RGB only. Treating alpha as opacity loses the intensity entirely.

Truncated blocks return `None` rather than raising — a malformed block should
cost one prim feature, not the whole object update — and `in_use=False` blocks
are skipped, since that is how a sim *clears* a feature.

Wired to a consumer in the same change, per the lesson from the June backlog:
`SceneEntity.extra_params` carries the decoded blocks and the Object Inspector
renders one row per block a prim actually has.

### What Was Verified Live

Two prims in the test region carry flexi blocks and decode to a plausible
configuration (softness 2, tension 1.0, drag 2.0, gravity 0.3, wind 0). The
region contains no light, projector or reflection-probe prims, so **those three
paths remain synthetic-test-only** — rez one of each to confirm them.

Render materials (`0x80`) and mesh flags (`0x70`) followed. Render materials
is the only variable-length block — a count byte then `(face_index, UUID)`
pairs — and OpenSim rejects the whole list when the declared count does not fit
the data rather than reading a partial one. This mirrors that: a half-applied
material set would be worse than none. Mesh flags returns `None` only for a
short block, so an explicit `0` stays distinguishable from absent, and the
aggregate filters on `is not None` rather than truthiness for the same reason.

## Update 2026-08-14: 32x32 LandExtended Terrain Patches

Varregions send terrain as 32x32 patches, which `decompress_patch` rejected
outright — the dequantize table, zig-zag copy matrix, cosine table and IDCT
were all hard-coded to 16. The algorithms were already generic in libomv; only
this port had baked in the edge. They are size-parameterized now, with tables
built once per size and cached. `DEQUANTIZE_TABLE16` / `COPY_MATRIX16` /
`COSINE_TABLE16` / `idct_patch16` remain as the 16-sized bindings, so nothing
that used them had to change.

`RegionHeightmap` also grows to fit. It was fixed at 256x256 and raised on any
patch landing outside — but a varregion is larger and its size is not known
until patches start arriving. Growth re-lays existing rows at the new stride,
capped at 2048 m so a malformed patch coordinate cannot allocate unbounded
memory.

**Unverified against a real varregion.** The test region is a standard 256 m
sim, so the 32x32 path is exercised only by synthetic bitstreams built with
`BitPackWriter`. The 16x16 path was live re-checked (14 LayerData messages, no
errors) to confirm the generalization did not regress it. Standing up a
varregion would confirm the rest.

## Update 2026-08-14: Object-Sync Dry Run (Read-Only)

The object script/notecard sync path has been implemented since 2026-05-25 and
never confirmed against a running sim, because confirming it means uploading
into in-world objects. A dry run verifies everything up to that point without
writing anything, and all of it works:

1. **`RequestTaskInventory` + xfer assembly** — 22 objects queried, every one
   returned a snapshot with a real `task_id`. Two carry a script:
   `'New Script'`, `asset_type='lsltext'`, with concrete `item_id`s
   (`8b2c2787-…` on task `43c98748-…`, `eb8743e4-…` on task `1ae29d6b-…`).
2. **Asset-type mapping** — task inventory reports `asset_type` as a *template
   string* (`'lsltext'`), not the integer the matcher and upload caps expect.
   `_asset_type_string_to_int` bridges it, mapping `'lsltext'` → 10. Anything
   consuming task inventory outside the HUD has to do the same conversion.
3. **Update capabilities** — `UpdateScriptTaskInventory`,
   `UpdateNotecardTaskInventory` and `UpdateScriptTask` all resolve against
   local OpenSim, so the upload half has somewhere to POST.
4. **The file matcher** — `_match_files_to_task_selections` pairs
   `New Script.lsl` with the live inventory row, reports `Unrelated.lsl` as
   skipped, and correctly ignores a non-uploadable `notes.png` rather than
   listing it as unmatched.

So the untested surface is now narrow: the two-step CAP POST itself
(`_request_uploader_sync` → upload bytes) and whether the sim recompiles the
script afterwards. Everything that feeds it is confirmed against live data.

Whoever runs the real verify: object local_id `234346577` or `234346578` each
hold one script, and their `task_id`s are above.

### Remaining

Nothing enumerated is now blocked on decoding. What is left is verification
against world content this test region does not contain:

- rez a light / projector / reflection-probe prim to confirm those three
  `ExtraParams` decoders live (only flexi has been seen)
- stand up a varregion to confirm 32x32 terrain live
- an object with a multi-submesh mesh and per-face materials would exercise the
  `material_groups` path against real assets rather than synthetic ones

## 2026-08-14 — SL prim face numbering, and the shape block compressed updates dropped

Two findings, the second much larger than the first and found only because the
first prompted a live census.

### TextureEntry faces did not match SL's face numbering

Per-face texturing existed only for cubes, and it split `CUBE_INDICES` by the
order `meshes.cube_mesh` happens to author faces in (-Z, +Z, +Y, -Y, +X, -X).
SL numbers box faces 0=+X, 1=+Y, 2=-X, 3=-Y, 4=top, 5=bottom. Every per-face
texture on a box therefore landed on the wrong side — a silent failure, since
the prim still rendered.

`meshes.shape_face_indices()` now returns the SL face → triangle index map for
the multi-face built-in prims, derived from SL's rule of walking the profile's
side segments first and appending the caps last (top before bottom). The
renderer's `_render_cube_faces` generalised into `_render_prim_faces` over
`_prim_face_meshes`, so cylinders (0=side, 1=top, 2=bottom) and prisms get
per-face textures too. Single-face prims — sphere, torus, sculpt — now read the
face 0 override rather than `TextureEntry`'s default; `texture_for_face` falls
back to the default anyway, so that only ever adds information.

The prism side-quad order is **derived, not observed**. The caps are confident;
which of the three side quads is SL's face 0 has never been checked against a
textured in-world prism, and a wrong guess rotates the three side textures
among themselves. `prism_face_indices` says so in its docstring.

Verified by offscreen GL readback aiming the camera at each face in turn, and
mutation-checked both ways (cube map reverted to slot order, cylinder map
rotated) to confirm the tests discriminate. One trap worth remembering: a
camera placed *directly* above its target has a view direction parallel to the
`(0, 0, 1)` up vector, which degenerates the view matrix and renders solid
black. The cap tests use `(1.5, 0, ±6)`.

### 30 of 32 live prims had no shape data at all

The census run to check whether the region contained any non-cube prims with
face overrides answered a different question instead: 30 of 32 prims reported
`shape=None`, so nearly every prim in the region was rendering as the fallback
cube regardless of its actual profile.

Cause: `decode_compressed_object_data` walked past the 23-byte path/profile
block with a bare `pos += 23` and never parsed it. `ObjectUpdateCompressed`
carries the bulk of object traffic in a populated region, so "compressed" meant
"shapeless".

The subtlety that makes this worth a separate parser: **the compressed block
does not use the message template's field order.** `ObjectUpdate` puts
ProfileCurve second, right after PathCurve; `LLClientView.CreateCompressedUpdateBlockZC`
writes the entire path group first and the profile group last. Reusing
`_parse_prim_shape_block` here raises nothing — it silently reports a profile
curve read out of the middle of `PathBegin`. Hence
`_parse_compressed_prim_shape_block`, plus a test whose fixture is authored so
the two readings disagree.

Live before: 30 unclassified, 2 cubes. Live after: 22 cubes, 5 spheres, 3 tori,
1 tube, 1 unclassified. The spheres and tori were always there.

### Still unobserved

The test region contains **zero** prims carrying per-face `TextureEntry`
overrides, so the face-map work is unit- and pixel-verified but not live-
verified. A prim with different textures on two faces would close that — and a
textured prism would settle the side-quad order.

## 2026-08-14 — hover text, wire to pixels

Prim floating text was recorded as a byte count and nothing else, in both
update paths; `ObjectUpdateCompressed` did not even do that, it stepped over
the field. Both paths now decode the string, and it carries through
`WorldObject` -> `SceneEntity` to an Object Inspector row *and* to a
camera-facing billboard in the 3D view.

**The colour is the trap.** OpenSim writes `argb ^ 0xff000000`
(`LLUDPZeroEncoder.AddColorArgb`), so fully opaque text goes out with alpha
byte `0`. Reading the byte as-is makes every ordinary hover text look
completely transparent — the feature would appear to work right up until
nothing rendered. `_decode_text_color` inverts it and tests pin both ends.

The billboard is scaled by eye distance so the text keeps a constant apparent
size, the way SL does it. Two consequences worth knowing:

- Moving the test camera closer does **not** make the label bigger. The GL
  tests need their own 256x256 framebuffer; at the file's shared 64x64 target
  a label is about 2 px tall and every glyph pixel is partial coverage, so the
  colour test can never pass.
- `depth_mask` lives on the bound framebuffer in moderngl 5, not on the
  context. Depth *test* stays on (text behind a wall is hidden); depth
  *writes* are off (overlapping labels blend instead of punching holes).

Live end-to-end: logged in, built a `Scene` from the live `WorldView`, aimed a
camera at local_id `234346578` and rendered offscreen. The magenta
`'hover text'` label appears above its prim — 96 pixels matching
rgba(255, 0, 255, 255), which is exactly the reading an uninverted alpha
decode would have reported as invisible. The same frame shows spheres and a
cylinder rendering as spheres and a cylinder, which is the compressed shape
fix visible in the same shot.

## 2026-08-14 — avatar names were in the data all along

`ObjectUpdate` NameValue pairs have been parsed into `WorldObject.name_values`
since early on, and nothing ever read them. Avatars never receive an
`ObjectPropertiesFamily`, so those pairs are the *only* place an avatar's name
arrives — which meant every avatar was anonymous in the inspector and
unlabelled in the 3D view while the data sat one attribute away.

A live census confirmed the shape before building on it:
`{'FirstName': 'Vibestorm', 'LastName': 'Tester', 'Title': ''}`.

`scene.avatar_display_name()` joins first and last, drops a `Resident` last
name (SL's placeholder for a single-name account, which viewers hide), and
puts a non-empty group `Title` on the line above. The empty-string `Title`
OpenSim sends for an untitled avatar must not become a blank first row on the
tag — that one has a test.

The hover-text billboard generalised into `_render_labels`, which walks both
sources: prim hover text brings its own colour, avatar tags get
`AVATAR_NAME_COLOR`. `render_hover_text` and `render_avatar_names` stay
independently switchable, and a test asserts turning one off does not silence
the other.

Live: rendered the region offscreen — "Vibestorm Tester" draws above the
avatar, the magenta hover-text prim draws beside it, and the spheres visible
in the same frame are the compressed-shape fix.

## 2026-08-14 — the sweep for content-gated code, and what it found

Chasing "is there any prim in the region with a per-face texture?" turned up
something better: **the Object Inspector crashed on exactly that prim.**
`TextureEntry.face_texture_ids` is a tuple of `(face, uuid)` pairs and the
inspector called `.items()` on it. Selecting any prim with a face override
would have raised `AttributeError` and taken the detail panel down. It never
fired because no prim in the region has one — the same absence that leaves the
new SL face maps live-unverified.

That is a *class* of bug, not one bug: branches that only execute when world
content the sim lacks comes into view. `test/test_rare_content_paths.py` now
covers the combination — one synthetic prim carrying per-face overrides,
flexi, light, projector, reflection probe, GLTF render materials, mesh flags,
hover text and a media URL, walked from the compressed wire blob through
`WorldView`, `Scene`, the inspector, and a real GL render.

Building that fixture pinned down a layout fact worth keeping: **the
compressed block's ExtraParams header is 6 bytes** (type u16, size u32) with
no in-use byte. The 7-byte in-use form is the `ObjectUpdate` /
`ObjectExtraParams` variant. `parse_shape_extra_params` tries both, so it
tolerates either, but `decode_compressed_object_data`'s own cursor walk
assumes 6 — the fixture assumed 7 and silently misaligned everything after it,
dropping the shape block.

Two decoding gaps closed alongside:

- **Attached sound.** Both paths stepped over the 25-byte sound group. Note
  they do not share a layout: the compressed block writes UUID/gain/flags/
  radius contiguously, while the full `ObjectUpdate` tail puts OwnerID
  *between* the sound UUID and its gain. A null sound UUID reports as `None`,
  since that is how a sim clears a looping sound. No prim in the region has
  one, so this is synthetic-only for now.
- **Permission masks.** The inspector printed five masks as raw hex. Bit
  values come from OpenSim's `PermissionMask` (`Framework/Util.cs`), not from
  memory: Transfer `1<<13`, Modify `1<<14`, Copy `1<<15`, Export `1<<16`, Move
  `1<<19`, and `All` deliberately excludes Export. Folded permissions (low
  nibble) are a *different* encoding of the same rights and stay separate —
  folding them in would claim a copy right the object does not have.
  Unrecognised bits survive as `unknown_bits`. Live across 32 objects x 5
  masks: every bit resolved, none unknown.

**Deliberately not done:** decoding `update_flags` into named prim flags.
`PrimFlags` lives in libomv, not in `opensim-source/`, and everything else
this session came from reading `LLClientView.cs` or `PrimitiveBaseShape.cs`
directly. Reconstructing a 32-bit flag table from memory would have produced
something plausible and unverifiable. It stays open until a libomv source is
available.

Also confirmed live and no longer a gap: **prim names**. 32 of 33 objects
carry an `ObjectPropertiesFamily` name; the one that does not is the avatar,
which now resolves from NameValues.

## 2026-08-14 — `./run.sh census`

The question "does this region contain a prim with X?" drives almost every
verification decision in this project: a decoder is only live-verified if the
sim produces content that reaches it. It got answered by a throwaway script
five separate times in one session, with results that could not be compared
between runs. It is now a command.

    ./run.sh census [--duration 30]

Reports object/avatar counts, the shape histogram, named vs unnamed, each
tracked feature with example `local_id`s, and every permission mask grouped by
what it grants. The load-bearing line is **`census absent=`** — a feature with
no example is named explicitly rather than left as a silent zero, because a
silent zero reads as "fine" and is how a decoder stays unverified for months.

Shape classification deliberately follows the *renderer's* precedence: a
sculpt/mesh hint beats the path/profile curves. So the histogram reports what
would actually be drawn, not what the curves alone imply. That is why the
census says 8 spheres where a curves-only script says 5 spheres and 3 tori —
those three are sculpts, and the renderer draws them via the sculpt path.

Current reading of the test region:

    census objects=32 avatars=1
    census shape[cube]=22 sphere=8 tube=1 unclassified=1
    census feature[hover text]=1
    census feature[flexi]=2
    census feature[sculpt or mesh]=3
    census absent=media url, attached sound, per-face texture, light,
                  projector, reflection probe, render materials, mesh flags
    census perms_unknown=none

That `absent=` list is the standing answer to "what content would let us
verify the rest", and it should be re-run rather than reasoned about after
anyone changes the region.

### The census immediately earned itself

The first report had one prim in an `unclassified` bucket. "Unclassified" on
its own is not actionable, so the census now prints the curves behind it — and
it said `census unclassified[path=0x80 profile=0x01]=1`, which named the bug
outright.

`classify_prim_shape` only knew path curves 0x10 (straight) and 0x20/0x30 (the
two circular extrusions). OpenSim's `Extrusion` enum
(`PrimitiveBaseShape.cs`) is Straight=0x10, Curve1=0x20, Curve2=0x30,
**Flexible=0x80**. Flexible is a *path mode*, not a shape of its own: a flexi
prim is a straight extrusion that bends at runtime, and the flexi ExtraParams
block carries the bending. So every flexi prim was falling through to the
unclassified fallback and rendering as a default cube regardless of its
cross-section — harmless for a flexi box, wrong for a flexi cylinder.

0x80 now joins the linear branch, with a test guarding the obvious wrong fix
of sending it to the circular branch instead. After it, the region reports no
unclassified prims and 23 cubes rather than 22.

The general lesson: a diagnostic that reports a *category* of ignorance
("unclassified", "unknown", "absent") should always carry the raw value that
produced it. The bucket says something is wrong; only the value says what.

### And then the `tube=1` bucket

The same report showed one tube. `_SHAPE_ALIASES` mapped tube to the *cube*
mesh and ring to the *torus*, so a square-section tube drew as a box and a
triangle-section ring as a round donut.

Both are swept cross-sections around a circular path, differing from the torus
only in the shape of that cross-section, so `meshes._swept_ring_mesh` is now
the shared body behind all three. One detail worth not re-discovering: the
tube's 4-gon profile is **phased 45 degrees**. A bare 4-side sweep puts profile
vertices at 0/90/180/270 and produces a diamond section, which reads as a lumpy
torus rather than a tube; a test pins the square section so the phase cannot
regress silently.

Tube and ring are single-face prims in SL, so they joined sphere, torus and
sculpts in reading the face 0 `TextureEntry` override rather than the entry
default. `_SHAPE_ALIASES` is now down to one entry: `mesh` still stands in as a
sphere until authored mesh assets are fetched and decoded.

Live-verified by rendering the region offscreen aimed at local_id 234346573 —
it draws as a hollow square-section ring.

## 2026-08-14 — the census hiding a zero in its own report

Reading the census output rather than just running it turned up two more
things.

**"sculpt or mesh=3" answered the wrong question.** Sculpts and authored
meshes ride the same ExtraParams block but fetch through different
capabilities — GetTexture for a sculpt map, GetMesh for a mesh asset. A count
that merges them does not say which pipeline a region exercises. The census now
names the kind (`sculpt:sphere`, `sculpt:torus`, `sculpt:plane`,
`sculpt:cylinder`, `mesh`) and prints the asset id per prim.

**And merging them hid a zero.** Three sculpts and no meshes produced a
non-zero total, so the GetMesh pipeline's complete absence of live coverage
never reached the `absent=` line — the exact silent zero the report exists to
prevent, inside the report itself. They are separate tracked features now, and
`absent=` correctly ends with `mesh asset`.

That immediately explained a session log. All three sculpt prims share asset
`be293869-d0d9-0a69-5989-ad27f1946fd4`, and a verbose session shows:

    event=texture.fetch.error id=be293869-d0d9-0a69-5989-ad27f1946fd4
        error=GetTexture ... failed: HTTP 404

So the sculpt render path cannot be verified here because **the sculpt map is
missing from the sim's asset store**, not because of anything client-side. And
`mesh.get_mesh_url_ready mesh fetch deferred until mesh object seen` is GetMesh
correctly idling — there is no mesh object in the region to fetch for.

Both are content problems, now visible rather than inferred. Re-uploading that
sculpt map would light up the sculpt path; a rezzed mesh object would light up
GetMesh and, with per-face materials, the `material_groups` path too.

## 2026-08-14 — the sound/animation events finally have a consumer

Six bus events (`AvatarAnimationReceived`, `ObjectAnimationReceived`,
`SoundTriggered`, `AttachedSoundReceived`, `AttachedSoundGainChanged`,
`PreloadSoundReceived`) were published from the session and subscribed by
nothing. The Scene now tracks them and the Object Inspector shows what an
object is doing *now* next to how it was *built* — the `ObjectUpdate` sound
block and the live AttachedSound can legitimately disagree, since a script can
swap either at runtime.

The modelling decision worth keeping: these are **current state, not a log**. A
new `AvatarAnimation` or `AttachedSound` replaces what was there, because that
is exactly how a sim stops an animation or swaps a sound. A trailing log would
show a stopped animation forever. Two consequences, both tested:

- a null sound id **clears** the entry rather than storing a zero UUID —
  otherwise "silent" and "playing asset 0" are indistinguishable
- an `AttachedSoundGainChange` for an object with no known sound is **ignored**,
  since inventing an entry from it would claim a sound id never seen

`SoundTrigger` is the exception. One-shot sounds have no lasting state, so they
go to a bounded tail (`SOUND_TRIGGER_HISTORY`) rather than a dict that grows
without limit in a busy region.

State is keyed by full UUID, which is how these messages address objects; a
local id only exists inside one region session.

Nothing in the test region emits any of them, so unit tests are the only
coverage until a sound emitter or animated object is rezzed — and `./run.sh
census` reports `attached sound` in its `absent=` list, so that stays visible.

## 2026-08-14 — every primitive had the wrong normals

Noticed by looking at a live render rather than a test: the avatar drew as a
smooth vertical plank instead of a figure. Its scale was correct (0.45 x 0.6 x
1.9 m, a real SL avatar bounding box), so the problem was shading.

`_interleave_vertex_attributes` falls back to `normalize(position)` when a mesh
supplies no normals — and **no built-in primitive supplied any**. That fallback
is exact for a sphere centred on the origin and wrong for everything else:

- a torus/tube/ring vertex on the inner wall of the hole got a normal pointing
  outward from the *world origin*, so the hole lit up backwards
- cylinder cap vertices shared a normal with the round side
- box corners got radial diagonals instead of flat face normals
- and the avatar placeholder's seven boxes sit *away* from the origin, so every
  part shaded into one gradient — the plank

Fixes, all in `meshes.py` behind `shape_normals()`:

- boxes and the prism are emitted **flat shaded**: a vertex per face corner
  rather than shared corners, because a shared corner can carry only one normal
- the cylinder splits its cap rings from its side rings for the same reason
- swept surfaces get analytic normals pointing away from the **centre of the
  tube** (the nearest point on the ring circle), not away from the origin

Index emission order is unchanged throughout, so the SL face maps still slice
correctly. A pleasant side effect: the flat cube normals now *independently*
confirm the face mapping derived earlier from OpenSim's profile-then-caps rule —
face 0 comes out (1,0,0), face 1 (0,1,0), face 2 (-1,0,0), face 3 (0,-1,0),
face 4 (0,0,1), face 5 (0,0,-1).

### The test lesson

Mesh-level tests proved `shape_normals()` was right. They did **not** prove the
renderer used it: deleting the pass-through left every one of them green. Two
GL tests now cover that, one per upload site — `_prim_face_meshes` (a cube face)
and `_shape_meshes` (the avatar's torso face) — each asserting the face shades
uniformly rather than as a gradient. Both were mutation-checked.

Worth remembering that the first mutation check *appeared* to pass because it
patched the wrong upload site. There are **three**, and each needed its own
test:

| site | shapes | normals from |
| --- | --- | --- |
| `_prim_face_meshes` | cube, cylinder, prism | `meshes.shape_normals()` |
| `_shape_meshes` (built-ins) | sphere, torus, tube, ring, avatar | `meshes.shape_normals()` |
| `_shape_meshes` (sculpt) | decoded sculpt maps | `smooth_vertex_normals()` |

(A fourth, `_mesh_face_meshes` for authored SL mesh assets, was already passing
`decoded.normals` correctly.)

Sculpts kept the position fallback a commit longer than the rest. A sculpt map
carries only positions, and only the *sphere* sculpt type is a surface centred
on the origin — torus, plane and cylinder sculpts were all lit wrongly. The
mesh decoder already computed smooth per-vertex normals for a submesh with no
`Normal` array, so that is now extracted as
`sl_mesh.smooth_vertex_normals(positions, indices)` and shared, rather than
having two implementations drift apart.

One detail there that looks like an oversight and is not: the triangle cross
product is accumulated **unnormalized**, so each face contributes in proportion
to its area. A sliver should not sway a shared vertex as much as a large
neighbouring face. There is a test pinning that.

## 2026-08-14 — TextureAnim, and a bug the "does not disturb" test caught

Texture animation was another byte count and nothing else. It is how a prim
scrolls, rotates, scales or flipbook-animates its texture — most of what makes
a region look alive.

Sourced, not reconstructed: `SceneObjectPart.AddTextureAnimation` writes the 16
bytes (Flags u8, Face s8, SizeX/SizeY u8, Start/Length/Rate f32) and the mode
bits are the LSL constants `ANIM_ON`..`SCALE` in `LSL_Constants.cs`.

Three details worth not rediscovering:

- **Face is signed.** `-1` means every face; reading it unsigned turns "all
  faces" into face 255.
- **"Off" is an empty block**, not 16 bytes with a cleared flag — absent and
  off are the same wire state.
- Under `ROTATE`/`SCALE` the SizeX/SizeY grid is unused, so `describe()`
  reports angles rather than printing a misleading `0x0` grid.

### The bug

Adding this broke the TextureEntry read, and the test that caught it was the
one asserting the new field "does not disturb" the old one — worth writing
every time a field is appended to a cursor-walked blob.

`decode_compressed_object_data` back-computed the TextureEntry start as
`pos - texture_entry_size`. That was correct only while nothing was parsed
after it. Appending the TextureAnim advance moved `pos` further on, so the
TextureEntry was then read ten bytes early and every prim's default texture
became garbage. The start offset is captured at read time now.

The compressed flag bit for TextureAnim (`0x0040`) is **inferred** from the gap
between `HAS_PARENT` (0x0020) and `HAS_ANG_VEL` (0x0080), not sourced. It is a
safe thing to be wrong about: OpenSim writes TextureAnim last, so no later
field depends on that cursor. Flagged here in case someone later finds the real
value.

`texture animation` joins the census `absent=` list — the test region has none.

## 2026-08-14 — SimStats: two enums, and why the live check mattered twice

Every session receives 41 region-health numbers — frame rate, physics time,
prim and script counts — decoded correctly into `(stat_id, value)` pairs and
then discarded: `SimStatSnapshot` kept only `len(message.stats)`. The snapshot
now stores named stats, and `format_world_status` prints a `world[sim_health]`
line.

Names come from `src/vibestorm/world/sim_stats.py`, transcribed from
`opensim-source/OpenSim/Framework/SimStats.cs`.

### Two enums, only one of which is on the wire

`SimStats.cs` defines **both** `StatsIndex` and `StatsID`, and they are easy to
confuse:

- `StatsIndex` numbers the slots of the sim's internal `float[]`.
- `StatsID` is the wire id. `LLClientView.SendSimStats` writes
  `SimStats.StatsIndexID[i]`, an array mapping slot → wire id.

The first version of the table was keyed on `StatsIndex`. The two enums are
**identical for ids 0-3** and diverge from 4 onward, so the mistake survives a
casual read: `StatsIndex` 4 is `Agents`, wire id 4 is `FrameMS`. The result is
not a crash or a missing value — it is a full status line of plausible numbers
under the wrong labels. `test_table_is_not_keyed_on_the_internal_array_index`
asserts the divergence point directly rather than trusting the 0-3 overlap.

Two further details worth keeping: the extension stats sit at **1000+**, not
packed just past the viewer range, and `UnAckedBytes` (24) is divided by 1024
by the writer — it is the only rescaled stat, hence the name `unacked kb`.

### The live run caught a second, quieter bug

After the table was fixed the status line *still* read
`sim fps=0 physics fps=0 time dilation=0 agents=0 …`. Every label correct,
every value zero — which reads exactly like an idle region rather than a bug.

`summarize_sim_stats` was re-naming stats that had already been named. It used
`getattr(entry, "stat_value", 0.0)`, and `NamedSimStat` carries `.value`, not
`.stat_value` — so the default silently supplied 0.0 for all 41. The defaults
are gone; the namer now takes raw entries only and raises on anything else, and
a test pins that feeding named stats back through it raises rather than
returning zeros.

Both bugs produced output that looked entirely reasonable. Neither would have
been caught by the unit tests alone — only by comparing against a running sim.
The confirmed live read: time dilation 1.0, sim fps 55.1, agents 1, total prims
32 (matching the census object count), active scripts 2, frame ms 18.16, ids
arriving out of numeric order exactly as `StatsIndexID` predicts. All 41 ids
resolved — no `world[sim_stats_unknown]` line.

## 2026-08-14 — the sourcing boundary, stated once

Three separate features have now been declined for the same reason, so it is
worth naming the pattern instead of rediscovering it:

**OpenSim's own enums live in `OpenSim/Framework/` and are sourceable. The
bitfields the viewer sees are libomv's (`OpenMetaverse.*`), and libomv is not
in `opensim-source/`.** OpenSim's call sites use those names freely, so a
grep finds `RegionFlags.AllowDamage` or `ChatSourceType.Agent` and it *looks*
sourced — but only the name is there, never the numeric value.

Declined on these grounds so far: `PrimFlags` (object `update_flags`), the
particle system block layout, and `ChatSourceType` / `ChatAudibleLevel`.

**Partially recanted for region flags** — see the parcel/region flags entry
below. `LSL_Constants.cs` turned out to define a sourced *subset* of both the
region and parcel flag words, which is enough to name those bits and report the
rest as unknown. Before declining anything on this basis, check LSL_Constants:
it exposes whatever a script can query, which is a surprising amount.

`RegionFlags` deserves a specific warning: `OpenSim/Framework/RegionFlags.cs`
**does** exist and defines an enum with that exact name — but it is the *grid
service's* region-record flags (DefaultRegion, FallbackRegion, Hyperlink), not
the region flags in `RegionHandshake`. Same trap as StatsIndex/StatsID: a real
file, the right name, the wrong enum.

## 2026-08-14 — chat had a type byte nobody read

`ChatFromSimulator` carries a chat type that reached the CLI as `type=1` and
the viewer not at all. `src/vibestorm/world/chat_types.py` names it from
`OpenSim/Framework/ChatTypeEnum.cs`.

The byte does three unrelated jobs, and conflating them was an actual bug:
types 4 and 5 are **start/stop typing**, which arrive as ChatFromSimulator
packets with an empty message. `Scene.apply_chat_local` appended every event
unconditionally, so each one became a blank row in the chat log. They now feed
a `typing_senders` indicator instead, cleared by stop-typing *or* by the sender
actually saying something (a sim does not reliably send stop-typing first).
Whisper and shout are shown as qualifiers; an ordinary say is left unmarked.

Type 3 is a dead second encoding of Say. It is deliberately left unnamed, so
that a sim sending it would show up as `unknown type 3` rather than being
folded silently into "say".

Live-verified by sending whisper/say/shout from a headless session and reading
the sim's echo back: types 0/1/2, `audible=1`, correct names. The typing path
is **not** live-verified — this client never sends typing notifications and the
test region has no second avatar to produce them.

`sourcetype` and `audible` stay raw ints; see the sourcing-boundary note above.

## 2026-08-14 — physics properties: decoded, now consumed, still unverifiable

`ObjectPhysicsProperties` was fully decoded by `event_queue/events.py` into
`ObjectPhysicsPropertiesEvent` — and nothing anywhere consumed it. Same shape
as the SimStats gap: correct decode, no destination.

`src/vibestorm/world/physics_shape.py` names the shape type from OpenSim's
`PhysShapeType` (`Framework/ExtraPhysicsData.cs`: prim 0, none 1, convex 2,
invalid 255). `Scene.object_physics` records it per prim and the inspector
shows it. Two deliberate choices:

- Material values equal to OpenSim's defaults (density 1000, friction 0.6,
  restitution 0.5, gravity 1.0 — `SceneObjectPart` field initialisers, checked
  against source) are **not** printed. Every prim carries these numbers;
  echoing them back implies someone chose them.
- `shape=none` also prints "no collision shape" on its own row. It is the one
  value with a visible in-world consequence: the prim is walked through.
- Keyed by **local id**, unlike the sound and animation rows beside it, which
  key by full UUID. `ObjectPhysicsProperties` addresses prims by local id. A
  test pins this, since getting it wrong would attach another prim's physics.

### Why this cannot be live-verified right now

`SceneGraph` calls `SendPartPhysicsProprieties` only from `UpdateExtraPhysics`
and `PrimMaterial` — that is, **only as an echo of an edit the viewer itself
just made**. The sim never sends it unprompted. A 25-second passive session
confirms this from the other side: the event queue delivered **no events at
all**, of any kind.

So reaching this code live requires modifying a prim in the region, which is
the same consent gate as the object-sync verify. Unit-tested and mutation-
checked, live-unverified, and it will stay that way until someone authorises
an in-world edit.

## 2026-08-14 — material and click action were integers on every prim

Both bytes arrive in every `ObjectUpdate` on every object, and both reached
the inspector raw: "Material: 3", "Click Action: 0". `world/prim_attributes.py`
names them from `LSL_Constants.cs` (`PRIM_MATERIAL_*`, `CLICK_ACTION_*`) — the
same source as the texture-animation modes.

The name is shown *with* the number (`metal (1)`), not instead of it. This is a
protocol client; a reader comparing the inspector against a packet dump needs
the byte.

`CLICK_ACTION_NONE` and `CLICK_ACTION_TOUCH` are **both 0**. Zero is named
"touch", because touch is what an unconfigured prim does — "none" would suggest
clicking does nothing.

The census now counts both, which is what makes the live check meaningful.

### What the live run actually proved, and what it did not

    census material[wood]=32
    census click_action[touch]=32

Every prim resolved, no unknown values — but every prim is also the *default*
(`SceneObjectPart.m_material` initialises to Wood; touch is the default click
action). So the tables are live-verified for exactly one value each. stone,
metal, glass, flesh, plastic, rubber, light, and sit/buy/pay/open/play/zoom are
unexercised, and will stay so until the region contains a prim that uses them.
That is a content gap, added to the standing list.

### A test caught a regression I introduced

Replacing the panel's defensive `getattr(w, "material", ...)` with direct
attribute access broke the TextureAnim inspector test, whose `_World` stub is
deliberately partial. The panel's contract is that it walks whatever it is
handed — every field in it uses `getattr` for that reason. Restored.

## 2026-08-14 — parcel and region flags, and a correction to the note above

**The sourcing-boundary note earlier in this file overstated the case for
region flags.** It said the viewer-facing `RegionFlags` bitfield is libomv's
and therefore unsourceable. That is true of the *complete* enum, but OpenSim's
`LSL_Constants.cs` defines nine `REGION_FLAG_*` and sixteen `PARCEL_FLAG_*`
constants with real numeric values — `llGetRegionFlags` and `llGetParcelFlags`
return exactly those words. A partial, sourced table is worth having.

`src/vibestorm/world/land_flags.py` decodes both, using the same contract as
`world/permissions.py`: name what is sourced, and report everything else
through `unknown_bits` rather than dropping it. `parcel_flags` reaches the
scene and the HUD diagnostics panel; `region_flags` is now carried on
`RegionInfo` (it previously existed only inside a debug detail string) and
printed by `format_world_status`.

A test asserts the two tables are **not** interchangeable: `0x40` is "allow
create objects" for a parcel and "block terraform" for a region, so using the
wrong table gives a wrong answer that still looks like a real flag name.

### Live results — both halves worth reading

    world[region_flags]=0x14108026 allow direct teleport, unknown 0x14008026

    parcel flags=0x2800204b allow fly, allow scripts, allow landmark,
                            allow create objects, allow all object entry,
                            unknown 0x20002000

Parcel decoding is genuinely exercised: five of the sixteen names appear on a
real reply. Region decoding resolves exactly **one** of nine — OpenSim's
`GetRegionFlags` sets a good many bits (AllowLandmark, AllowSetHome,
ExternallyVisible, AllowVoice, and others) whose numeric values LSL never
exposes, so they land in `unknown_bits`. That large unknown mask is the correct
output, not a defect, and it is exactly what the earlier note was right to be
cautious about — the fix is to report the gap, not to guess the bits.

## 2026-08-14 — the state byte, and a swap that hides in plain sight

Every prim carries a `state` byte the client kept raw. It means two different
things: for a tree or grass prim it is the species, and for an **attachment**
it is the attachment point — **nibble-swapped**. `LLClientView` does it twice,
in the full update and the terse one:

    int st = 0xff & (int)part.ParentGroup.AttachmentPoint;
    state = (byte)((st >> 4) | (st << 4));

This is the nastiest failure mode seen this session, worse than the
StatsIndex/StatsID mix-up, because the wrong answer is *in range*: attachment
point 1 (chest) arrives as `0x10` = 16, and 16 is itself a valid point (right
eye). Skipping the swap does not corrupt anything or raise — it silently
reports a different body part. A test asserts both halves: that `0x10` decodes
to chest, and that the raw 16 would have read as "right eye".

`world/attachments.py` decodes it, names all 55 points from `ATTACH_*` in
`LSL_Constants.cs`, and flags the eight HUD slots (31-38) separately since
those are screen-space and visible only to the wearer.

Crucially, the state byte alone cannot tell you a prim *is* an attachment — a
tree has a non-zero state too. OpenSim marks an attachment's root with the
`AttachItemID` NameValue, which the client already decoded, so that is the
test. `describe_attachment` returns None for anything else.

Points 29/30 are worth knowing about: `ATTACH_RPEC`/`ATTACH_LPEC` and
`ATTACH_LEFT_PEC`/`ATTACH_RIGHT_PEC` share those two values with the sides
**swapped** — an upstream SL bug (SVC-580) kept for compatibility. Named by
the newer, correctly-sided constants.

Live: `census absent=… attachment`. The test region has no attachments — the
tester avatar wears nothing — so this is unit-tested only. Rezzing anything
onto the avatar would exercise it, and the census now asks the question every
run.

## 2026-08-14 — sound flags, and the near-miss on sourcing them

The inspector showed `flags 0x00`. `world/sound_flags.py` names the byte:
LOOP, SYNC_MASTER, SYNC_SLAVE, SYNC_PENDING, QUEUE, STOP.

**The obvious source was the wrong one.** After the LSL_Constants win with
parcel flags, the natural next move was to reach for `SOUND_*` in
`LSL_Constants.cs` — PLAY 0, LOOP 1, TRIGGER 2, SYNC 4. Those are
`llLinkPlaySound` *parameters*, not the wire byte. The real enum is OpenSim's
own `SoundFlags` in `CoreModules/World/Sound/SoundModule.cs`, and the two
disagree on every value above 1: wire 2 is SYNC_MASTER, not TRIGGER; wire 4 is
SYNC_SLAVE, not SYNC. A test asserts exactly that, because the LSL-sourced
version would have produced confident, wrong, plausible names.

So the lesson from the parcel-flags entry needs a qualifier: LSL_Constants is
worth checking, but it describes *the scripting API*, which is not always the
wire. Confirm against the code that writes the bytes.

One behavioural consequence: `AttachedSoundState.is_silent` now also checks the
STOP bit. A stop message still names a sound, so "sound_id set" was being read
as "playing" when the sim had just told it to go quiet.

Live: unverified. No prim in the test region has a sound, and `census absent=`
has listed `attached sound` all session.

## 2026-08-14 — region-scoped state that outlived its region

Found by asking a maintenance question rather than a protocol one: *which of
the Scene's state is region-scoped, and who clears it?*

`Scene.apply_region_changed` already cleared entities, textures, parcel name,
overlay and map tile — with an explicit comment about not showing a stale tile
from the old region. But every **per-object side dict** survived the change:

    object_physics        attached_sounds      object_animations
    avatar_animations     recent_sound_triggers  typing_senders
    parcel_flags          sim_health

The entity dicts look after themselves — `refresh_from_world_view` rebuilds
them every frame from the WorldView, which is per-circuit and therefore
naturally region-scoped. These do not: they accumulate from bus events and
nothing pruned them.

`object_physics` is the one that is actually *wrong* rather than merely stale.
It is keyed by **local_id**, and local ids are assigned per region session — so
object 42 in the new region would silently inherit object 42's physics from the
region just left. The UUID-keyed dicts are less dangerous (a UUID is global) but
still wrong: an object left behind keeps a phantom looping sound forever.

`typing_senders` and `parcel_flags` were mine, added earlier this session;
`parcel_name` was already being cleared right beside `parcel_flags`, which made
the omission easy to see once the question was asked.

Each cleared field is mutation-checked individually, so the test cannot pass on
a partial fix.

**Worth repeating as a habit:** every time state is added to a long-lived
object, ask what its scope is and who resets it. Four of these eight fields
were added this session without that question being asked.

## 2026-08-14 — a GL texture leak in the label cache

The same "what is this scoped to, and who frees it?" question, asked of the
renderer's caches this time. `PerspectiveRenderer._hover_text_textures` is
keyed by **the text itself**, and had no eviction — only a teardown release.

So the cache is bounded by *distinct labels seen over the session*, not by the
prim count. A static "For Sale" sign is one texture forever, which is why this
never showed up in testing. But hover text is what scripts use for clocks,
visitor counters, vendor prices and status boards: a prim rewriting its text
once a second mints one GL texture per second and frees none until the
renderer is torn down.

Now an LRU capped at `HOVER_TEXT_CACHE_MAX = 64`, releasing on eviction.

The test uses a stub context rather than real GL, so the eviction policy is
covered without needing a GL machine. Three mutations, three distinct
failures:

- no eviction at all → 4 failures
- evicts but never calls `release()` → 2 failures
- no LRU touch on a cache hit → 1 failure

The middle one matters most: dropping the reference without `release()` looks
completely correct from Python — the dict shrinks, memory usage in the process
looks fine — while the texture stays allocated on the GPU. Only an explicit
assertion that evicted textures were *released* catches it.

## 2026-08-14 — the object texture cache, and two tests that lied

Same sweep, next cache. `_object_textures` is keyed by texture UUID and does
correctly release on re-upload when a path changes. It is also deliberately
**not** cleared on a region change — texture files persist on disk, so
revisiting a region reuses what is already uploaded — and that is exactly why
it needs a bound: it is the one GL cache nothing clears mid-session. Capped
LRU at 256, which is 256 real textures, not glyph bitmaps.

The interesting part was the tests, both of which passed while testing
nothing:

**1. The test called the evictor directly.** `_upload()` poked
`_object_textures` and then called `_evict_object_textures()` itself. Deleting
the evictor's call site from the real upload path still passed. This is the
same mistake as the normals work earlier in the session (patching one of three
upload sites and believing the green run) — a test that reaches past the code
path it is meant to cover. Fixed by going through `_upload_object_texture`
with a real PNG in a temp dir.

**2. The LRU test asserted the wrong thing.** It checked that a frequently
reused texture was still *present* after churn. Without an LRU touch, that
texture is evicted and then immediately re-uploaded on its next request — so
it is present at the end either way. Presence was never the property worth
asserting; the **upload count** was. It now asserts exactly `churn + 1`
uploads.

Both are the same underlying error: asserting a state that the bug also
produces. Worth checking for whenever a mutation "passes".

Final mutation matrix, all four failing distinctly:

| mutation | result |
|---|---|
| no eviction on upload | 2 failures |
| evict without `release()` | 1 failure |
| paths dict not evicted in lockstep | 1 failure |
| no LRU touch on cache hit | 1 failure |

## 2026-08-14 — the LRU caps were the wrong fix; reference pruning replaces them

**This supersedes the two cache entries above.** The leaks they describe were
real, but the fix — a least-recently-used count cap — was wrong, and would have
been much worse than the problem.

Both `_upload_object_texture` and `_hover_text_texture` are called **inside the
per-frame draw loop**, once per visible textured face group and once per label.
Neither the number of visible textures nor the number of visible labels is
capped anywhere. So the moment a region holds more than the cap, an LRU evicts
textures that are *still on screen*, and they are re-decoded from PNG and
re-uploaded to GL **every frame, forever**. Trading a slow memory leak for a
permanent per-frame stall is a bad trade, and 256 visible textures is an
ordinary region, not an extreme one.

All three caches now prune by **reference** instead:

- `_prune_object_textures(scene)` — keeps whatever is in `scene.texture_paths`
- `_prune_label_textures(active_texts)` — keeps the labels drawn this frame
- `_prune_mesh_assets(scene)` — keeps whatever is in `scene.mesh_paths`

Nothing in use can be released, by construction: the live set *is* what the
draw loop is able to ask for. The first two run once per frame; the freeing
happens naturally when `apply_region_changed` clears those scene dicts.

`_prune_mesh_assets` is new ground — I had claimed in the previous entry that
the mesh caches "don't warrant the same treatment" because they are keyed by
shape key. That was wrong: a mesh asset's shape key embeds its asset UUID, so
they accumulate per distinct mesh exactly like textures do, and a decoded
mesh's vertex and index buffers are bigger than a texture. It only takes care
of asset-derived keys — the built-in prim meshes share `_shape_meshes` and
releasing one would break every prim of that shape, which a test now pins.

Five mutations, five distinct failures: no release on texture prune, paths dict
not pruned in lockstep, no release on label prune, mesh face buffers not freed,
and pruning against an empty live set.

**The lesson worth keeping:** when a cache is filled from inside a render loop,
a capacity bound is not a safety measure — it is a thrash generator. Bound
those caches by what is live, never by how many.

## 2026-08-14 — testing a cache across frames, and why one-frame tests can't

The pruning above is a **cross-frame** mechanism: frame N uploads, frame N+1
decides what is still live. Every existing GL test in
`test_viewer3d_perspective_gl.py` renders exactly one frame and tears the
renderer down afterwards, so none of them could reach the failure mode at all.
Added a `_multi_frame` helper that drives several frames through one renderer.

Two things fell out, both instructive.

**The teardown ran before the caller saw the renderer.** The helper returned
the renderer from inside a `try` whose `finally` called `clear_caches()` — so
the cache assertions saw an emptied cache and failed with `0 != 1`. Python
runs the `finally` before the return completes. The pixel assertions, which
are the ones that matter, had passed all along. Now the cache size is read
before teardown and returned as a value.

**Then the real lesson: asserting the render output cannot detect thrash.**
Mutating `_prune_label_textures` to release *everything* every frame — the
worst possible prune — left all three new GL tests passing. Pruning runs
before the draw loop, and the draw loop re-rasterises whatever is missing, so
the frame still comes out pixel-correct. It just re-uploads a texture per label
per frame forever, which is exactly the pathology this work exists to prevent.

The fix is to assert **identity**, not presence or appearance: for unchanged
text the cached texture object must be the *same object* across frames. With
that, the prune-everything mutation fails.

This is the third time this session that a mutation passing revealed a test
asserting a state the bug also produces — see the object-texture entry above.
When a mutation passes, suspect the assertion before concluding the code is
fine.

## 2026-08-14 — constructing the large region the test sim doesn't have

The cache rework left one thing unobserved: the failure an LRU cap causes
needs *more distinct visible textures than the cap*, and the test region has
32 prims sharing a handful of textures. That is a content gap — but unlike a
sound emitter or an attachment, it is one a test can construct.

`LargeRegionTextureGLTests` builds a scene with **300 distinct textures**, each
its own PNG on disk and its own UUID, on 300 spread-out prims, and renders two
frames through one renderer on real GL. It asserts identity across frames:
every texture object after frame 2 must be the *same object* as after frame 1.
A second test clears `texture_paths` and `object_entities` the way
`apply_region_changed` does, and asserts everything is released.

Reinstating the reverted LRU-cap design fails **three** tests. Removing texture
pruning entirely fails one. So this test would have caught the bad fix before
it was committed — twice.

Distinct files as well as distinct ids, deliberately: a wrong implementation
that keyed only on path would look correct if 300 ids shared one file.

The general point: "we have no content that exercises this" is sometimes a real
blocker (a sound emitter must exist in-world to send AttachedSound) and
sometimes just an unasked question. Scale, breadth, and volume are usually
constructible; only genuinely external behaviour is not.

## 2026-08-14 — the last two orphaned bus events get a consumer

`EnableSimulator` and `CrossedRegion` were decoded, published on the bus, and
consumed by **nothing** — the last such pair. The scene's own docstring claimed
they "reach consumers through the bus", which described an intention rather
than reality; that line is corrected.

They deserve opposite treatment, which is why one handler would have been
wrong:

- **`EnableSimulator`** fires once per neighbouring region and is re-announced,
  so it is recorded as state (`Scene.neighbour_regions`, handle -> "ip:port")
  and shown as a count in the diagnostics panel. Announcing each one would put
  eight alerts on screen on arriving in a region with eight neighbours.
- **`CrossedRegion`** is rare and means the avatar is now somewhere else, so it
  posts an alert line, like `TeleportFinish` does.

A real crossing needs a neighbour region and the test sim is standalone, so
this is not live-verified — but the *events* are constructible, so the scene's
handling of them was never actually blocked on that. Only the transport
behaviour is (`world_client`'s documented "EnableSimulator → child circuit,
CrossedRegion → promote child" remains unimplemented and genuinely does need a
neighbour to build against).

`neighbour_regions` is cleared in `apply_region_changed`, decided when the
field was added rather than found later — the habit from the region-scope fix
earlier today.

### A fourth way a mutation can lie

The "neighbours not cleared" mutation passed, and this time neither the code
nor the assertion was at fault: the mutation script's string replace **did not
match**, so it silently changed nothing and tested the unmodified code. Re-run
by line index with an `assert` on the target line, it fails correctly.

Ad-hoc mutation scripts must assert the pattern was found. A `.replace()` that
misses is indistinguishable from a test that holds.

## 2026-08-14 — a gap that had already been closed

`projectstate.md` listed "deeper object update families such as
`ObjectUpdateCached` and `KillObject`" as an open gap. `ObjectUpdateCached` is
not a gap and has not been one for a while: `session.py` already requests a
full update for every cached entry via `RequestMultipleObjects`, chunked at
255 ids.

Measured against the live sim rather than assumed:

- `ObjectUpdateCached` arrives **twice**, at ~5.7 s and ~6.3 s — at region
  entry, not on a repeating timer.
- 13 `ObjectUpdate` events yield **33 tracked objects**, so the cached-request
  path is what populates most of the region. It demonstrably works.

I had gone looking for a specific bug — the handler re-requests unconditionally
rather than only on genuine cache misses, so a sim that re-announced cached
objects periodically would make us re-request things we already hold. That is
a real inefficiency in principle and a non-issue in practice at two messages
per session. Not worth code; worth the measurement that says so.

`KillObject` does remove objects from the `WorldView`. It has no live exercise
because nothing in the test region is ever deleted.

Also removed the stray empty `tests/` directory. The suite is `test/`; the
plural one was an empty leftover and had been sitting there unexplained.

**Stale gap entries are their own hazard.** This one would have had someone
implement a feature that already existed. Worth re-reading the gap list
occasionally and checking the entries still describe reality.

## 2026-08-14 — local ids are never reused, so don't "fix" the kill-side leak

Chasing the within-region version of this morning's cross-region bug: a killed
object leaves entries behind in the scene's `local_id`-keyed dicts
(`object_physics`, `object_inventory_snapshots`), and if the sim reused that
local id, a newly rezzed prim would inherit a dead one's physics.

It does not. `SceneBase.AllocateLocalId` is:

    return (uint)Interlocked.Increment(ref m_lastAllocatedLocalId);

A monotonic counter with no free list — within a region session's lifetime, a
local id is never handed out twice. So those leftovers are a **bounded leak,
not a correctness bug**, and the leak is bounded by objects derezzed during
one session.

Deliberately no code. Pruning them would have to run somewhere, and the
obvious place — pruning per frame against the WorldView — risks dropping state
for an object that is momentarily absent, and `ObjectPhysicsProperties` is
never re-sent unprompted. A real risk in exchange for no real gain. Recorded
here so the next person weighing it can skip the investigation.

What the look *did* find was a coverage gap: `apply_kill_object` removes
`terse_objects` entries, and no test asserted it. That matters for a
terse-only object, where the terse record is the *only* record — leaving it
behind keeps a dead prim moving in the viewer. Now covered both ways, and the
mutation (dropping the `terse_objects.pop`) fails.

## 2026-08-14 — auditing the gap list, which had drifted into a changelog

Prompted by finding one stale entry: if the list said `ObjectUpdateCached` was
open when it had been done for a while, what else was wrong? Two more were:

- **"semantic decoding of terse object payloads beyond the first inferred
  `local_id`"** — stale. `parse_improved_terse_object_update` decodes state,
  is_avatar, the avatar-only collision plane, position, velocity,
  acceleration, rotation, angular velocity and the TextureEntry. Nothing is
  left inferred.
- **"(`EnableSimulator`, `CrossedRegion`) remain the only bus events with no
  consumer"** — contradicted by a *newer bullet in the same list* saying they
  now have one. The list had grown two entries that disagreed.

The underlying problem is structural rather than any single wrong line.
"Current Gaps" had become a changelog: most bullets describe finished work,
written in the same voice and tense as the open items. There is no way to tell
"this is missing" from "this was built" by reading a bullet.

Fixed by putting the genuinely open work at the top, grouped by **what would
unblock it** — region content, consent, sources, or nobody-has-written-it-yet —
and labelling everything below as landed work. The history is kept; it just no
longer masquerades as a to-do list.

That grouping is also the honest summary of where this stands: almost
everything still open needs something from outside the codebase, and the only
two purely-unimplemented items are region-crossing transport and the inventory
write surface.

**A stale gap entry costs as much as a bug and leaves no trace.** Two of them
sent me on investigations this session that ended in no code. Worth re-reading
the list against reality now and then, which is what this was.

## 2026-08-14 — inventory asset types, and live data that proves the distinction

`./run.sh inventory-walk` now names item types and reports which *gap-closing*
types the account lacks — the account-side counterpart of the census
`absent=` line.

Values are the `INVENTORY_*` LSL constants, and the care went into deciding
**which field they name**. The wire carries both `type` (asset type) and
`inv_type` (inventory type). These constants are the asset numbering, pinned
by `llGetInventoryType` returning `item.Type`. libomv's `InventoryType` table
is not in `opensim-source/`, so `inv_type` is deliberately left unnamed.

The live walk settles it beyond the source reading:

    type=5   inv_type=18   'Default Shirt'
    type=13  inv_type=18   'Default Eyes'
    type=24  inv_type=18   (Current Outfit links)

`Default Shirt` is asset type 5 (clothing) but inventory type 18 (wearable).
Naming `inv_type` with this table would have been wrong on nearly every item
in a real account, and the two enums diverge worst where it is least visible —
an animation is asset 20 / inventory 19, a gesture 21 / 20.

Type 24 (link) and type 2 (calling card) appear live and are correctly
reported as `unknown type N`: real asset types that LSL does not expose. Not
guessing them is the point.

### What the account actually holds

    inventory type[body part]=12  type[unknown type 24]=6  type[clothing]=4
    inventory absent=object, sound, animation, gesture

So the standing region-content gaps **cannot be closed from this account's
existing inventory** — there is nothing to rez or play. That is a concrete
answer to a question that had been open all session.

### The upload smoke test creates a mistyped item — diagnosed from source

The notecard left by `upload-empty-text-smoke` reads back as
`type=0 inv_type=0`. Asset type 0 is *texture*; a notecard should be 7.

I first filed this as needing another upload to investigate. It did not —
OpenSim's source answers it outright. `BunchOfCaps.UploadCompleteHandler`
opens with:

    sbyte assType = 0;
    sbyte inType = 0;

and then assigns them only inside `if (inventoryType == ...)` branches. The
complete set of branches is **sound, snapshot, animation, animset, wearable,
object**. There is no `notecard` branch, and no `else`. An unrecognised type
falls through and the item is created with both fields still 0 — while the
upload reports success and `FetchInventory2` confirms the asset matches, which
is exactly why the smoke test has always passed.

Notecards and scripts are not uploaded through this capability at all: a
viewer creates them with `CreateInventoryItem` and fills them in through
`UpdateNotecardAgentInventory` / `UpdateScriptAgent`.

`NEW_FILE_INVENTORY_TYPES` now records the supported set, with a test that
re-parses the branch list out of `BunchOfCaps.cs` so it cannot drift. The
smoke command prints the stored types and a warning rather than reporting a
clean success. Still no fix to the upload path itself — the correct flow is a
different pair of capabilities and building it means writing to the user's
inventory, which is theirs to authorise.

**The generalisable bit:** "I would have to perform a side effect to find out"
was wrong. The server's own source said what it does with the request. Reading
beats poking, and it needed no permission.

## 2026-08-14 — "verified" was hiding two very different claims

The upload smoke test turned out to verify something weaker than it appeared
to, which prompted the same audit on `spec/message-coverage.md` that the gap
list got: does each claim mean what a reader would take it to mean?

The status scale said `verified` meant "covered by fixtures, **tests, or** live
session evidence". Those are not the same strength of claim, and this session
produced three decoders that passed their unit tests and were wrong on live
data — SimStats keyed to the wrong one of two enums, the inventory walk raising
on the real payload shape, and a GL cache policy that was correct per-frame and
pathological across frames. A green suite is evidence about the code; only a
live run is evidence about the protocol.

`tested` and `verified` are now separate statuses, and where a row covers
several sub-decoders the status reflects the **weakest** one.

Three rows changed as a result:

- **`ObjectExtraParams`** claimed `verified` for seven sub-decoders. Two —
  sculpt and flexi — are live-confirmed. Light, projector, reflection probe,
  render materials and mesh flags have never seen live data and are all on the
  census `absent=` list. Now `tested`, with the split spelled out.
- **`LayerData`** was `verified` on the strength of the 16x16 path; the 32x32
  LandExtended half has never run against a sim. Said so.
- **`ChatFromSimulator` / `ChatFromViewer`** were *understated* — marked
  `handled` and "needs an in-world speaker to observe", which stopped being
  true this session when I sent whisper/say/shout and read the sim's echo.
  Both are now `verified`, with the typing notifications still `tested`.

`SimStats` had **no row at all**, despite arriving in every session since the
project started. Added.

Note that the drift ran in both directions: one row overclaimed, one
underclaimed, one message was missing. A ledger nobody re-derives goes stale
in whichever direction the last edit happened to leave it.

## Notes For The Next Agent

- All viewer-data protocol primitives live in `src/vibestorm/udp/messages.py`
  (encoders/parsers) and `src/vibestorm/caps/get_texture_client.py`.
- `src/vibestorm/assets/j2k.py` is the Pillow-backed decoder. Pillow is in
  the optional `viewer` extra (`uv sync --extra viewer`).
- The viewer dependency is `pygame-ce` rather than classic `pygame`; current
  `pygame_gui` imports APIs that classic `pygame` 2.6.1 does not expose.
- `local/map-cache/` is gitignored by the existing `local/` rule.

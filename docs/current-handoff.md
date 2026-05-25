# Current Handoff

Last updated: 2026-05-25

## Where To Move Next

Two coherent next tracks are open:

1. Live-verify object sync: select a scripted object in `viewer3d`, download scripts
   via Save Text, edit a `.lsl` locally, then "Upload File" — confirm the upload dialog
   seeds `local/asset-downloads/<task-id>/`, syncs to the object, and the script
   recompiles (check chat for "Sync: … compiled OK").
2. Continue real mesh/sculpt rendering:
   - live-verify `GetMesh` against a local OpenSim mesh object
   - add normals/UVs and per-face/material grouping to decoded mesh assets
   - live-verify sculpt map fetch/deformation against local OpenSim sculpted prims
   - add viewer-grade sculpt stitching/normals/UVs

The object sync track is largely implemented (see 2026-05-25 update below); live
verification is the concrete next step. The sculpt/mesh track is the next rendering path.

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

## Notes For The Next Agent

- All viewer-data protocol primitives live in `src/vibestorm/udp/messages.py`
  (encoders/parsers) and `src/vibestorm/caps/get_texture_client.py`.
- `src/vibestorm/assets/j2k.py` is the Pillow-backed decoder. Pillow is in
  the optional `viewer` extra (`uv sync --extra viewer`).
- The viewer dependency is `pygame-ce` rather than classic `pygame`; current
  `pygame_gui` imports APIs that classic `pygame` 2.6.1 does not expose.
- `local/map-cache/` is gitignored by the existing `local/` rule.

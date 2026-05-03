# Viewer Phases — Implementation Roadmap

Sequenced plan for building the 2D bird's-eye viewer described in
`docs/viewer-architecture.md`. Each phase has a concrete deliverable, a
test strategy, a rough difficulty estimate, and the files it expects to touch.

The intent is *learn-by-doing*: each phase is small enough to land as a focused
PR and end with a runnable, testable checkpoint. Difficulty estimates may
shift as we discover surprises — record them in the phase's "Notes" section.

## Phase status legend

- ⏳ pending — not started
- 🚧 in progress
- ✅ done
- 📝 surprises — see notes

---

## Phase 1 — AgentUpdate emitter ⏳

**Goal:** the avatar can stand still and blink, walk forward, turn, and stop,
all from headless code (no GUI).

**Why first:** without `AgentUpdate` the sim treats the avatar as frozen
post-handshake. Movement, animations, and even camera-aware events depend on
it. This is a protocol gap before it's a UI problem.

**Deliverables:**

- `src/vibestorm/udp/messages.py`: `encode_agent_update(...)` builder for the
  High/4 AgentUpdate packet.
- New module `src/vibestorm/udp/control_flags.py`: `AgentControlFlags` IntFlag
  with the AT_POS / AT_NEG / LEFT_POS / LEFT_NEG / UP_POS / UP_NEG / FAST_*
  / NUDGE_* / FLY / TURN_LEFT / TURN_RIGHT bits, sourced from
  `opensim-source/.../AgentManager.cs`.
- `LiveCircuitSession`: periodic AgentUpdate emit at ~10 Hz when the agent
  is "active" (handshake complete). Fields:
  - `BodyRotation`, `HeadRotation` from a session-held quaternion (default
    identity).
  - `CameraCenter`, `CameraAtAxis`, `CameraLeftAxis`, `CameraUpAxis` from a
    session-held camera (default: behind+above the avatar).
  - `Far` = 64 m default.
  - `ControlFlags` from a session-held bitfield, settable via a method.
  - `Flags` = 0.
  - `State` = 0.
- Method on session: `set_control_flags(flags)`, `set_body_rotation(quat)`,
  `set_camera(at, left, up, center)`.
- Coalescing: don't emit if nothing changed *and* the previous emit was less
  than ~250 ms ago (keepalive cadence per LL convention).

**Tests:**

- `encode_agent_update` round-trip against fixture bytes (build, hex-compare
  to a captured AgentUpdate from `local/captures/` if available, else assert
  field offsets manually).
- `LiveCircuitSession` emits AgentUpdate after handshake completes (capture
  outbound packets in a fake transport).
- `set_control_flags(AT_POS)` causes the next AgentUpdate to carry the bit.
- Idle session emits keepalive AgentUpdate at the throttled cadence.

**Estimated difficulty:** medium-low. Encoding is mechanical; the only
sharp edges are quaternion field order (LLQuaternion is xyz only — w is
reconstructed) and the keepalive cadence.

**How to validate live:** run `./run.sh opensim &; ./run.sh session`, log in,
call `session.set_control_flags(AgentControlFlags.AT_POS)` from the console
hookup, and watch the avatar inch forward in the OpenSim console
(`show users`, position changes).

**Files touched:**

- `src/vibestorm/udp/messages.py`
- `src/vibestorm/udp/control_flags.py` (new)
- `src/vibestorm/udp/session.py`
- `test/test_udp_messages.py`
- `test/test_udp_session.py`

**Notes:** *(record surprises here as we go)*

---

## Phase 2 — Multi-circuit `WorldClient` skeleton ⏳

**Blocked by:** Phase 1 (so AgentUpdate routing is already in the per-circuit
session).

**Goal:** session ownership is no longer a singleton. A `WorldClient` owns N
`LiveCircuitSession` instances; one is "current".

**Deliverables:**

- New `src/vibestorm/udp/world_client.py`: `WorldClient` class.
  - `circuits: dict[int, LiveCircuitSession]` keyed by region_handle.
  - `current_handle: int | None`.
  - `add_circuit(...)`, `remove_circuit(handle)`, `set_current(handle)`.
  - Routes outbound packets via a `send(handle, packet)` method.
  - Aggregates `WorldView` via `world_view()` — for v1 just returns the
    current circuit's view; multi-region merging arrives in Phase 5.
- Refactor: `app/cli.py` constructs a `WorldClient` with one circuit instead
  of constructing `LiveCircuitSession` directly.
- The login flow stays the same; once handshake succeeds, the session is
  registered in the WorldClient as the current circuit.
- No new wire traffic. This is a pure refactor.

**Tests:**

- Existing session tests still pass through the `WorldClient` shim.
- `WorldClient.set_current()` switches which circuit receives outbound
  routing.
- `WorldClient.world_view()` returns the current circuit's view.

**Estimated difficulty:** medium. The risk is *spread of assumptions*:
many call sites probably grab "the session" and use it. Most of those should
become "the current circuit", but a handful (login, CAPS) belong on the
WorldClient itself. Read the call graph carefully before refactoring.

**Files touched:**

- `src/vibestorm/udp/world_client.py` (new)
- `src/vibestorm/udp/session.py` (small)
- `src/vibestorm/app/cli.py`
- `test/test_world_client.py` (new)
- existing session tests if their fixtures need to construct via WorldClient.

---

## Phase 3 — Command / event bus ⏳

**Blocked by:** Phase 1.

**Goal:** a typed in-process pub/sub layer that mediates between the
WorldClient and any consumer (pygame, CLI, future tk tools). Designed so a
later IPC server can sit in front of it without touching consumers.

**Deliverables:**

- `src/vibestorm/bus/__init__.py`: `EventBus` and `CommandBus` (or a single
  combined `Bus` — pick one in implementation).
- Event types as dataclasses in `src/vibestorm/bus/events.py`:
  `ObjectAdded`, `ObjectMoved`, `ObjectRemoved`, `AvatarAdded`, …, `ChatLocal`,
  `ChatIM`, `ChatGroup`, `ChatAlert`, `RegionChanged`, `LoginProgress`,
  `LoginComplete`, `Logout`, `Error`.
- Command types as dataclasses in `src/vibestorm/bus/commands.py`:
  `Move`, `SetCamera`, `TeleportLocation`, `TeleportLandmark`, `SendChat`,
  `SendIM`, `SendGroupIM`, `FetchInventoryFolder`,
  `RequestObjectProperties`.
- `WorldClient` translates inbound UDP/CAPS state changes into events and
  publishes them on the event bus.
- `WorldClient.handle_command(cmd)` dispatches on the command class to the
  appropriate UDP/CAPS action.
- The bus is *synchronous* (subscribers are called inline). For asyncio
  consumers we provide a `subscribe_async(prefix) -> AsyncIterator[Event]`
  helper backed by an `asyncio.Queue`.

**Tests:**

- Publishing an event delivers to all matching subscribers.
- Unsubscribing stops delivery.
- A command flows through `handle_command` to the right session method
  (use a fake transport and assert outbound bytes).
- An async subscriber receives events queued during processing.

**Estimated difficulty:** medium. The hard part is keeping the event
vocabulary stable — once tool windows depend on event names, renaming is
painful. Spend time on naming and field shape; lean toward a small number of
expressive events rather than many narrow ones.

**Files touched:**

- `src/vibestorm/bus/__init__.py` (new)
- `src/vibestorm/bus/events.py` (new)
- `src/vibestorm/bus/commands.py` (new)
- `src/vibestorm/udp/world_client.py`
- `src/vibestorm/udp/session.py` (publish events from handlers)
- `test/test_bus.py` (new)

---

## Phase 4 — Pygame v1 viewer ⏳

**Blocked by:** Phases 1, 2, 3.

**Goal:** a window that shows the region map tile, draws colored markers for
every object/avatar at the right place and size, lets the user move with WASD
and chat with a one-line input, and re-centers on the avatar with a key.

**Deliverables:**

- New `src/vibestorm/viewer/` package.
- `viewer/app.py`: pygame main loop. Initializes window, asyncio loop,
  WorldClient, command/event bus, viewer state.
- `viewer/camera.py`: world-to-screen transform; `zoom_at(point, factor)`,
  `pan(dx, dy)`, `center_on(world_pos)`.
- `viewer/scene.py`: subscribes to object.* / avatar.* events, maintains a
  scene dictionary keyed by local_id with cached oriented-bounding-rect
  vertices.
- `viewer/render.py`: draws the map tile background, then iterates the scene
  and draws markers. Marker color by pcode (constants in the same module).
- `viewer/hud.py`: pygame_gui setup. Zoom buttons, region label, chat ticker
  (last N chat events), one-line chat input.
- `viewer/input.py`: WASD/arrows → `Move(...)` command, mouse wheel → camera
  zoom, drag → camera pan, `C` key → center on avatar, `Enter` in chat input
  → `SendChat(...)`.
- New entrypoint `./run.sh viewer` (mirrors `console`/`session`).
- Pygame + pygame_gui added to the existing `viewer` extra in `pyproject.toml`.

**Tests:**

- Camera transform: round-trip world↔screen at various zooms.
- Scene update: applying object.added → object.moved → object.removed leaves
  an empty scene.
- HUD chat input: pressing Enter dispatches a `SendChat` command (use a fake
  bus).
- (No pixel tests. The viewer is a runnable artifact and tested by
  visual inspection against `./run.sh opensim`.)

**Estimated difficulty:** medium-high. Three sources of friction:

1. Pygame + asyncio integration. Pick one approach (architecture doc lists
   two) and stick with it.
2. Camera transform math (especially mapping `position` + `scale` + quaternion
   `rotation` to a 2D oriented rectangle).
3. pygame_gui ergonomics — the docs are decent but examples are scattered.

**How to validate live:** `./run.sh opensim &; ./run.sh viewer`. Expect to
see the region map under your avatar, a marker for yourself, markers for
other avatars and prims, smooth WASD movement, working chat input.

**Files touched:**

- `src/vibestorm/viewer/` (new package, ~6 files)
- `pyproject.toml` (pygame + pygame_gui in `viewer` extra)
- `run.sh` (new `viewer` subcommand)
- `test/test_viewer_camera.py`, `test/test_viewer_scene.py`, `test/test_viewer_hud.py` (new)

---

## Phase 5 — Cross-sim + teleport ⏳

**Blocked by:** Phases 1, 2.

**Goal:** the avatar can walk over a region edge into a neighbor, and can
teleport via landmark or location. Map tile and scene update accordingly.

**Deliverables:**

- Inbound message decoders:
  - `EnableSimulator` (Low/151) — neighbor sim coords + handle.
  - `EstablishAgentCommunication` event-queue event — neighbor seed cap.
  - `CrossedRegion` (Low/29) — agent has crossed.
  - `TeleportStart`, `TeleportProgress`, `TeleportFinish`, `TeleportLocal`,
    `TeleportFailed`, `TeleportCancel`.
- Outbound message builders:
  - `TeleportLocationRequest` (Low/65).
  - `TeleportLandmarkRequest` (Low/66).
  - `CompleteAgentMovement` (already exists; reuse for child→current
    promotion).
- `WorldClient` integration:
  - On `EnableSimulator` + `EstablishAgentCommunication`: open a child
    circuit (UseCircuitCode + handshake handshake-light) but do *not* set
    current.
  - On `CrossedRegion` / `TeleportFinish`: promote child→current; demote
    old current to child or drop it.
  - Emit `RegionChanged` on the event bus.
- Map cache: when a circuit becomes current, fetch its map tile if not
  already cached (we already have this code; just generalize).
- Pygame: re-center camera on new region origin; redraw map tile.

**Tests:**

- Decoders: round-trip for each new message against captured fixtures (collect
  during a manual cross-region walk).
- WorldClient: simulating an `EnableSimulator` event creates a child circuit
  and does not change `current_handle`.
- WorldClient: simulating `CrossedRegion` switches `current_handle` and emits
  `RegionChanged`.

**Estimated difficulty:** high. This phase has the most subtle protocol
state. Possible surprises: timing of EstablishAgentCommunication vs the
viewer-initiated handshake on the new circuit; throttle handling per
circuit; AgentUpdate must still go to the right circuit during a cross.

**Files touched:**

- `src/vibestorm/udp/messages.py`
- `src/vibestorm/udp/world_client.py`
- `src/vibestorm/udp/session.py`
- `src/vibestorm/viewer/scene.py` (multi-region origin)
- tests for each new decoder + integration tests for the WorldClient.

---

## Phase 6 — Inventory tk window ⏳

**Blocked by:** Phases 1, 2.

**Goal:** a separate process showing the user's inventory tree. Folders
expand lazily; items show name + asset type; texture items show a thumbnail
on hover.

**Deliverables:**

- IPC layer:
  - `src/vibestorm/bus/ipc_server.py`: Unix-socket JSON-RPC server bolted
    onto the existing in-process bus. Accepts subscribe / command / response.
  - `src/vibestorm/bus/ipc_client.py`: client-side wrapper used by tool
    processes.
- New CAPS clients:
  - `src/vibestorm/caps/fetch_inventory_descendents2_client.py` — POST LLSD
    with folder ID, parse LLSD response (folders + items + version + descendents).
- `WorldClient.handle_command(FetchInventoryFolder)` calls the CAPS client
  and emits `inventory.folder_loaded`.
- New tool entrypoint: `src/vibestorm/tools/inventory_window.py`.
  - Tk root, `ttk.Treeview` for folders/items.
  - Lazy expansion: on `<<TreeviewOpen>>`, if folder not yet loaded, send
    `FetchInventoryFolder` and wait for `inventory.folder_loaded`.
  - Right-click context menu (placeholders for rez/wear).
- New `./run.sh inventory` subcommand (spawns the tool process; the hub
  must already be running via `./run.sh viewer` or `./run.sh session`).
- The hub (viewer or session) auto-starts the IPC server on a known socket
  path under `local/run/`.

**Tests:**

- `fetch_inventory_descendents2_client` LLSD round-trip against a captured
  response.
- IPC server: subscribe + receive event over a real socket (test with a
  pytest unix-socket fixture).
- Tk window: minimal smoke test (construct + tear down) inside a hidden Tk root.

**Estimated difficulty:** high. AISv3 LLSD parsing is finicky and the
inventory CAP is poorly documented outside the OpenSim source. Plan to read
`opensim-source/OpenSim/Capabilities/Handlers/FetchInventory2/` and capture
real responses from a populated test account.

**Files touched:**

- `src/vibestorm/bus/ipc_server.py`, `ipc_client.py` (new)
- `src/vibestorm/caps/fetch_inventory_descendents2_client.py` (new)
- `src/vibestorm/udp/world_client.py` (handle_command branch)
- `src/vibestorm/tools/inventory_window.py` (new)
- `run.sh` (new `inventory` subcommand)
- tests for client, IPC, and window.

---

## Phase 7 — Chat / IM / group / friends / settings / teleport tk windows ⏳

**Blocked by:** Phases 1, 2, 5 (cross-sim is needed for teleport landing
correctness).

**Goal:** complete the social / control surface. Each tool is a small tk
window connected to the hub via the IPC layer built in Phase 6.

**Deliverables (one window per sub-bullet):**

- `tools/chat_window.py` — tabbed: Local + one tab per IM target + one tab
  per group session. Tabs auto-open on inbound. Each tab is a scrolling Text
  widget + entry field.
  - Inbound: subscribes to `chat.local`, `chat.im`, `chat.group`,
    `chat.alert`. Routes to the right tab.
  - Outbound: `SendChat`, `SendIM`, `SendGroupIM` commands.
  - New protocol bits as needed: outbound IM (already have inbound),
    group session join (`ChatterBoxSessionStartReply` event), group IM
    flavor of `ImprovedInstantMessage`.
- `tools/friends_window.py` — listbox of friends with online/offline dot,
  IM-this-friend / teleport-to-friend buttons.
  - New protocol bits: `OnlineNotification`, `OfflineNotification`,
    `AvatarPropertiesRequest` for names, friend-list fetch via inventory
    folder + buddy list (TBD — investigate first).
- `tools/settings_window.py` — checkboxes for skip-bake, debug-verbose,
  packet-noise filter; sliders for throttle. Persists to
  `local/settings.json`.
- `tools/teleport_window.py` — tabs: Landmark (picker into inventory tree),
  Location (region name + x/y/z fields), Lure inbox (pending teleport
  offers from `chat.im` with the right Dialog code).

**Tests:**

- Per-window smoke test (construct + dispatch a fake event, assert UI
  state changes).
- Protocol decoders / encoders for each new message, with round-trip tests.

**Estimated difficulty:** medium each. Chat is the largest because of group
session lifecycle. Friends needs the buddy-list protocol path which we
haven't touched.

**Files touched:**

- `src/vibestorm/tools/chat_window.py`, `friends_window.py`,
  `settings_window.py`, `teleport_window.py` (new)
- `src/vibestorm/udp/messages.py` (new encoders/decoders as needed)
- `src/vibestorm/caps/` (new clients as needed)
- tests for each.

---

## After Phase 7

Open follow-on work, in no particular order:

- TextureEntry section-walking parser → real per-face colors / textures.
- Object hovertext: render the in-world `Text` field above markers.
- ParcelOverlay decode → plot-edge polylines.
- Map zoom-out across multiple regions (`MapLayerReply`, `MapBlockRequest`
  for a wider grid).
- Object texture fetching → tinted markers / textured-quad overlay.
- LayerData (procedural terrain) → optional terrain shading layer.
- Outbound IM flavors: object-given, request-teleport, etc.
- Voice chat (out of scope unless we want it).

These can be slotted in after the v1 surface is working end-to-end.

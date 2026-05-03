# Viewer Architecture

Functional design for the Vibestorm 2D bird's-eye client. Companion to
`docs/viewer-phases.md`, which sequences the implementation work.

This is a *functional* design — it pins down what the pieces are, what they
own, and how they talk. Renderer fidelity, art direction, and UX polish are
out of scope. Code-level type signatures are illustrative; the implementation
PRs will pin them down.

## Goals

- Bird's-eye 2D view of one or more adjacent regions, with the prerendered map
  tile as background and oriented colored markers for objects/avatars.
- User can move (WASD / arrows / autopilot click), teleport (landmark, location,
  lure), and walk across region borders.
- Full local + IM + group chat surface, friends list with presence, inventory
  browser with thumbnails.
- Multi-window: the world view is one window; chat, inventory, friends,
  settings, teleport dialog are separate windows that can be opened/closed
  independently. The user can run with just the world view, or with any subset
  of tools.

## Non-goals (v1)

- 3D rendering of any kind.
- Procedural terrain (`LayerData` patches). The map tile is the background.
- Mesh / sculpt geometry. Markers are oriented bounding-box footprints.
- Per-face TextureEntry decoding. Default UUID + category color is enough.
- Voice chat.
- Outfit editing UI. Bake upload already happens automatically at login.

## Process model

The world view (pygame) and tool windows (tk) cannot share a single Python
event loop cleanly. We pick a pragmatic split:

- **Hub process** — owns the asyncio loop, the `WorldClient` (multi-circuit
  UDP+CAPS), the command bus, the event bus, and the asset cache. Runs the
  pygame world view in its main thread.
- **Tool processes** — each tk tool window (inventory, chat, friends, settings,
  teleport dialog) is a separate process spawned by the hub via
  `subprocess.Popen`. Each connects back to the hub over a local IPC channel.

**Why this shape:**

- Pygame and asyncio coexist easily in one process: pump `asyncio.run_until_complete(asyncio.sleep(0))` (or use `asyncio.run` with pygame on a thread) once per frame.
- Tk wants its own `mainloop()` and is finicky about threads. Giving each tool
  its own process sidesteps every thread-safety question.
- Tool windows can be killed and restarted without touching the live circuit.
- The IPC contract that tools use is the same contract a future
  separate-process pygame viewer would use, so we pay the design cost once.

**v1 simplification:** the IPC layer is *not built first*. Phase 4 (pygame v1)
runs entirely in-process and calls the command/event bus directly. The bus is
designed with a transport boundary in mind, so wrapping it in a small JSON-RPC
server (Phase 6) is additive, not a rewrite.

## Layered structure

```
+--------------------------------------------------------------+
| Hub process                                                  |
|                                                              |
|  +--------------------------------------------------------+  |
|  |  WorldClient                                           |  |
|  |    - LiveCircuitSession[region_handle]   (one current, |  |
|  |      others child sims)                                |  |
|  |    - login + seed caps + EventQueueGet                 |  |
|  |    - circuit routing                                   |  |
|  +--------------------------------------------------------+  |
|              ^                          |                    |
|              | commands                 | events             |
|              |                          v                    |
|  +--------------------------------------------------------+  |
|  |  Command / Event bus                                   |  |
|  |    - typed commands (move, teleport, chat, ...)        |  |
|  |    - typed events  (object.added, chat.local, ...)     |  |
|  |    - in-process pub/sub now; IPC server later          |  |
|  +--------------------------------------------------------+  |
|              ^                          |                    |
|              |                          v                    |
|  +--------------------------+  +-------------------------+   |
|  |  Pygame world view       |  |  Asset cache            |   |
|  |    - map tile bg         |  |    - textures (J2K→PNG) |   |
|  |    - object markers      |  |    - inventory items    |   |
|  |    - HUD (pygame_gui)    |  |    - object properties  |   |
|  +--------------------------+  +-------------------------+   |
+--------------------------------------------------------------+
              ^ IPC                       ^ IPC
              |                           |
+-------------+--------------+  +---------+--------------+
| Tk tool process            |  | Tk tool process        |
|   inventory window         |  |   chat window          |
+----------------------------+  +------------------------+
```

## Components

### `WorldClient`

Top-level session owner. Replaces today's "one `LiveCircuitSession` is the
session" assumption.

- Holds N `LiveCircuitSession` instances keyed by region handle.
- Exactly one is `current` (where the agent's avatar is); others are child
  sims (neighbors fetched after `EnableSimulator` / `EstablishAgentCommunication`).
- Owns login (XML-RPC), seed caps, EventQueueGet, the global asset cache.
- Routes outbound packets to the correct circuit. Most packets go to current;
  some (e.g. CompleteAgentMovement during region change) go to a child sim
  that's about to become current.
- Aggregates per-circuit `WorldView` snapshots into a multi-region scene that
  the renderer consumes.

### `LiveCircuitSession`

Stays as the per-region UDP+message handler. Same code path it has today, just
no longer assumed to be a singleton.

### Command bus

In-process pub/sub today; IPC-able later.

Commands the bus accepts:

- `move(direction_flags, fast=False)` — sets agent control flags for the next
  AgentUpdate tick.
- `set_camera(at, left, up, center)` — overrides camera vectors.
- `teleport_landmark(asset_id)` / `teleport_location(region_handle, position, look_at)`.
- `send_chat(message, type, channel)`.
- `send_im(target_agent_id, message)` / `send_group_im(session_id, message)`.
- `fetch_inventory_folder(folder_id)` / `request_object_properties(local_ids)`.

Each command returns a small ack/handle so the UI can correlate the resulting
events.

### Event bus

Typed events the renderer/tools subscribe to:

- World: `region.changed`, `object.added`, `object.moved`, `object.removed`,
  `avatar.added`, `avatar.moved`, `avatar.removed`, `terrain.tile_ready`.
- Chat: `chat.local`, `chat.im`, `chat.group`, `chat.alert`, `chat.outbound`.
- Inventory: `inventory.folder_loaded`, `inventory.item_added`,
  `inventory.thumbnail_ready`.
- Presence: `friend.online`, `friend.offline`, `friend.added`.
- Session: `login.progress`, `login.complete`, `logout`, `error`.

Subscribers are dumb — they receive events and update local state. Renderers
do not poll the WorldClient directly.

### Asset cache

- Textures: `local/texture-cache/<uuid>.png` (J2K-decoded). Already in use for
  region map tiles; extends to object textures and inventory thumbnails.
- Object properties: in-memory map keyed by local_id (today's
  `ObjectPropertiesFamily` cache moves here).
- Inventory: in-memory tree of folders/items, lazily populated.

### Pygame world view

- Owns the camera transform (zoom + pan).
- Default zoom: full sim (256×256 m fits the window).
- Center-on-avatar: re-center camera and follow.
- Layers (back to front): map tile → terrain overlay (later) → parcel outlines
  (later) → object markers → avatar markers → HUD.
- Object marker: oriented rectangle from `position` + `scale` + `rotation`,
  colored by pcode + (later) default-face color, optional name label / hovertext.
- HUD via `pygame_gui`: zoom buttons, region label, mini-coords, chat ticker,
  one-line chat input.
- WASD / arrows → command bus `move(...)`.
- Mouse wheel → zoom; click-and-drag → pan; double-click world → autopilot
  (translates to a series of `move` flags or a `MoveTo`-style command, TBD).

### Tk tool windows

Each is a standalone Python script invoked as a subprocess. They share a
common `vibestorm.tools.ipc_client` module that opens the IPC channel,
subscribes to relevant events, and exposes a thin command-sending wrapper.

- `inventory.py` — `tk.Treeview`, lazy folder expansion, double-click to copy
  asset UUID, right-click context menu (rez/wear later).
- `chat.py` — tabbed: Local, then one tab per IM target, one per group session.
  Each tab is a scrolling text widget + entry field.
- `friends.py` — listbox with presence dots, IM-this-friend button.
- `settings.py` — toggles (skip bake upload, debug/verbose, throttle settings).
- `teleport.py` — modal: landmark picker (from inventory), or region+x,y,z
  text fields, or "accept lure" inbox.

## IPC contract (deferred to Phase 6)

Local IPC between hub and tool processes. Not built until inventory tk window
lands.

Sketch:

- Transport: Unix domain socket (`local/run/vibestorm.sock`) with newline-
  delimited JSON. Trivial to debug with `nc -U`.
- Protocol: client sends `{"id": N, "method": "...", "params": {...}}` for
  commands; receives `{"id": N, "result": ...}` or
  `{"id": N, "error": ...}`. Events are pushed unsolicited as
  `{"event": "...", "data": {...}}`.
- Tool processes subscribe by method call (`subscribe`, with a list of event
  prefixes). The hub keeps a per-connection subscription set.
- Authentication: none in v1 — the socket lives under `local/run/` with
  user-only permissions.

The command and event names match the in-process bus exactly so the bus
implementation can grow an IPC server without touching consumers.

## Open questions

- **Renderer process split.** Should the pygame viewer eventually become a
  separate process too, or stay in the hub? Probably stays — the renderer
  needs the highest-frequency event firehose and IPC adds latency. But this
  is reversible.
- **Asyncio + pygame integration.** Two viable shapes: (a) asyncio loop in a
  worker thread, pygame on main thread, communicate via a thread-safe queue;
  (b) single thread, pygame ticks asyncio via `loop.run_until_complete(asyncio.sleep(0))`
  per frame. Decide in Phase 4. (a) is more idiomatic; (b) has fewer surprises
  with pygame on macOS.
- **Movement model.** AgentUpdate carries control flags + camera. For "click
  here to walk" autopilot we either (a) compute headings client-side and
  toggle AT/LEFT flags, or (b) use OpenSim's autopilot via a chat command.
  Defer to Phase 4 (and probably do (a)).
- **Multi-region rendering.** When child sims are connected, do we render
  their objects too? Probably yes for visible neighbors at zoom-out; needs
  a per-region origin offset in the camera transform.

## Sequencing

See `docs/viewer-phases.md` for the implementation roadmap.

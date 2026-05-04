# Viewer 2D / 2.5D / 3D Plan

Last updated: 2026-05-04

## Intent

Evolve the current pygame 2D viewer into a single viewer shell with selectable
rendering modes:

- `2D` (Map): current top-down map view. Stable baseline.
- `3D` (Perspective): real 3D rendering with approximate geometry.
- `2.5D` (Pseudo-3D): optional pygame oblique projection. **Status: deferred.**
  See "On 2.5D" below — it duplicates camera/depth-sort work the 3D path needs
  anyway. Build it later only if a no-GL fallback is genuinely required.

## Strategy: Fork, then Re-Merge

The 3D work happens in a forked package, **not** a refactor of the live 2D
viewer. The 2D viewer is in maintenance mode (per `project_vibestorm.md`,
console UI is the active surface) and we don't want to risk breaking it
while experimenting with a GL backend.

- `src/vibestorm/viewer/` — 2D bird's-eye viewer. Stable reference. Bugs may
  go unfixed; we don't care to invest in it. Tests stay green.
- `src/vibestorm/viewer3d/` — copy of the above on 2026-05-04, byte-for-byte
  apart from intra-package import retargets and the window caption. This is
  where 3D rendering lands, where `SceneEntity`/`Camera3D`/moderngl get added,
  and where the renderer interface eventually re-emerges.
- `./run.sh viewer` runs the 2D viewer; `./run.sh viewer3d` runs the fork.

The eventual goal: `viewer3d` grows a renderer abstraction with both a
top-down 2D mode and a perspective 3D mode, and the original `viewer/`
package can be retired. Until then, drift between the two is the explicit
cost of avoiding a risky in-place refactor.

Reuse the existing login/session loop, `WorldClient`, `Scene`, HUD, chat,
inventory, status bar, and menu shell **inside** `viewer3d/`. The split
between rendering modes happens at the renderer and camera layers within
the fork.

## What the Renderer Already Has

Before designing a "3D scene shape," inventory what's already on the wire and
in `WorldView`/`Scene`. The 3D renderer should consume this — not a parallel
pipeline.

Per object (`world.models.WorldObject`, populated from `ObjectUpdate` and
refreshed by `ImprovedTerseObjectUpdate`):

- `position: (x, y, z)` — SL world frame, meters, Z-up.
- `scale: (sx, sy, sz)` — meters.
- `rotation: (x, y, z, w)` — unit quaternion.
- `pcode` — prim, avatar, tree, grass, particle.
- `default_texture_id: UUID | None` — first decoded face texture.
- `extra_params_entries` — already parsed; sculpt/mesh markers present.
- `properties_family.name` — display name when known.
- For `prim_basic`: `path_curve` + `profile_curve` are decoded by the message
  parser even if not yet stored on `WorldObject`. They classify the prim as
  cube/sphere/cylinder/torus/prism/ring etc.

Per terse object (`TerseWorldObject`):

- `position`, `velocity`, `acceleration`, `rotation`, `angular_velocity`.
  Enough for client-side dead reckoning between updates.

Per region (`WorldView.latest_time` → `SimulatorTimeSnapshot`):

- `sun_phase` (float) — drives the 3D directional light.
- `usec_since_start`, `sec_per_day` — full diurnal cycle if wanted.

Per region (cached on disk):

- `local/map-cache/<uuid>.png` — region map tile, usable as the ground texture
  in 3D until terrain (`LayerData`) is decoded.

Per agent (`WorldView.coarse_agents`):

- `(x, y, z)` integer-resolution positions; `is_you` marks the local avatar.
  Useful for placing the orbit/eye camera before any full ObjectUpdate for the
  agent has arrived.

What is **not** yet available and should be assumed-absent in v1:

- Terrain elevation. `LayerData` (heightmap patches) is undecoded.
- Per-face textures beyond `default_texture_id`. `TextureEntry` per-face
  decode is deferred per project state.
- Mesh and sculpt geometry. Asset fetch + decode is unimplemented.
- Full avatar appearance applied to a 3D body. Bake upload works at login,
  but no avatar mesh exists to texture.

## Working Model

Console output is one-dimensional, the current pygame surface is two-dimensional,
and a 3D viewer ultimately projects onto that same 2D surface. So 2D, 2.5D,
and 3D are rendering strategies over the same world snapshot, not separate
products.

For the first 3D pass, visual fidelity is secondary. Mesh and sculpt rendering
are skipped. Approximate every entity from `position`, `scale`, `rotation`,
`pcode`, and (when available) `path_curve`/`profile_curve`.

## Coordinate Conventions

Spell this out once, in the renderer-facing scene layer, so individual
renderers don't each get it wrong:

- **SL world frame**: X east, Y north, Z up. Right-handed.
- **OpenGL camera frame**: looking down −Z, Y up. Right-handed.
- **Top-down screen frame**: X right, Y down (pygame), so the 2D code already
  flips Y in `Camera.world_to_screen`.

The 3D renderer should accept SL-frame positions/quats from `Scene` and remap
internally (e.g. swap Y/Z and negate one axis when uploading to GL). Do not
push the conversion into `Scene` — 2D consumers depend on the SL frame.

## Shared Viewer Shell

Keep one pygame app:

- same login/session startup
- same live `run_live_session(...)` background task
- same `WorldClient` bus
- same `Scene` data aggregation (extended, not replaced)
- same pygame_gui HUD/menu/chat/status/inventory windows
- renderer mode switching from the View menu

The app loop should not know the details of each renderer beyond the small
renderer interface below.

### Compositing the HUD over a GL frame

This is the one shape change the existing app needs. Today `app.py` does:

```python
render_scene(screen, camera, scene)
hud.update(dt, scene)
hud.draw(screen)
pygame.display.flip()
```

For a GL renderer the screen surface is the default framebuffer and pygame_gui
must blit on top after the GL pass. Two viable shapes:

- **Hybrid display** (recommended first): create the window with
  `pygame.OPENGL | pygame.DOUBLEBUF`, run GL each frame, then render
  pygame_gui to an offscreen `pygame.Surface`, upload that surface as a
  textured fullscreen quad, and `pygame.display.flip()`. One window, one
  swap; HUD stays in pygame_gui.
- **Two surfaces** (simpler, slower): keep the existing software display and
  drive 3D through a separate moderngl standalone context that draws into a
  pygame Surface via PBO readback. Easier to bolt on, but readback per frame
  is a performance trap.

Pick the hybrid path when 3D actually lands.

## Renderer Interface

Replace today's free function with a small interface that survives both
software (2D) and GL (3D) backends:

```python
class ViewerRenderer(Protocol):
    def attach(self, screen_size: tuple[int, int]) -> None: ...
    def detach(self) -> None: ...
    def resize(self, screen_size: tuple[int, int]) -> None: ...
    def handle_event(self, event) -> bool: ...
    def update(self, dt: float, scene: Scene, camera: Camera3D) -> None: ...
    def render(self, scene: Scene, camera: Camera3D) -> None: ...
```

Notes vs the prior contract:

- No `surface` argument. The renderer owns its target (software surface for
  2D; default framebuffer for 3D).
- `attach` / `detach` give the GL backend a hook to (re)create context, VAOs,
  and shaders when the user switches modes. The 2D backend's implementations
  are no-ops.
- `Camera3D` is shared state; see "Camera" below. The 2D top-down case is
  expressed as a Camera3D with a fixed orthographic top-down projection.

Expected implementations:

- `TopDownRenderer` — current 2D map view, refactored behind the interface
  with no behavior change.
- `PerspectiveRenderer` — moderngl-backed 3D.
- `Pseudo3DRenderer` — only if/when 2.5D is needed; deferred.

The HUD renders on top in every mode (see compositing note above).

## Scene Data Shape

`Scene` already aggregates the right fields for 2D. Extend it once so 2D and
3D both consume it:

- Promote markers from a 2D-flavored `Marker` to a renderer-agnostic
  `SceneEntity`:

  ```python
  @dataclass(slots=True, frozen=True)
  class SceneEntity:
      local_id: int
      pcode: int
      kind: Literal["prim", "avatar", "tree", "grass", "particle", "unknown"]
      position: tuple[float, float, float]   # SL frame, meters
      scale: tuple[float, float, float]
      rotation: tuple[float, float, float, float]  # quat
      shape: PrimShape | None        # cube / sphere / cylinder / torus / prism / ring
      default_texture_id: UUID | None
      name: str | None
      tint: tuple[int, int, int]     # fallback color when no texture
  ```

- `Scene.refresh_from_world_view` populates `SceneEntity` once. The current
  2D `Marker` becomes a thin view derived from `SceneEntity` (or replaced
  outright).
- Add `Scene.sun_phase: float | None` from `WorldView.latest_time` so the 3D
  light has a source.
- Multi-region: when `WorldClient` exposes neighbor `WorldView`s, `Scene`
  takes a list of `(region_origin_xy, world_view)` and namespaces local_ids
  by region to avoid collisions.

Keep the existing apply_* event handlers untouched — only the WorldView
reconciliation step changes.

## Primitive Shape Tier

Rendering every prim as a box loses too much. The protocol gives enough to
pick a better primitive for free:

| `profile_curve & 0x07` | Profile     | Combined with path → primitive |
| ---------------------- | ----------- | ------------------------------ |
| 0                      | Circle      | Cylinder, Sphere, Torus        |
| 1                      | Square      | Cube, Tube                     |
| 2                      | IsoTriangle | Prism                          |
| 3                      | EquilTriangle | Prism                        |
| 4                      | RightTriangle | Prism                        |
| 5                      | HalfCircle  | Sphere, hemisphere             |

(Cross with `path_curve`: line → extrusion, circle → revolve.)

The 3D renderer keeps a small library of static meshes (unit cube, unit UV
sphere, unit cylinder, unit torus, unit prism). Each entity picks one and
applies `scale`/`rotation`/`position`. Hollow, twist, taper, and shear are
ignored in v1.

Avatars and trees stay as billboards or capsules (a stretched cylinder with
sphere caps is fine). Particles stay as billboards or skipped.

## Camera

Replace the existing 2D `Camera` (or wrap it) with a single `Camera3D` whose
mode controls the projection:

- `Map`: orthographic top-down, current 2D pan/zoom math expressed as ortho
  half-extents derived from `zoom`. The 2D renderer ignores yaw/pitch.
- `Orbit`: third-person camera around the local avatar (or selected entity).
  Mouse drag rotates yaw/pitch; wheel adjusts distance.
- `Eye`: first-person from avatar head position; yaw mirrors avatar yaw,
  pitch is mouse-driven.
- `Free`: fly camera independent of avatar; WASD moves it, right-drag rotates,
  scroll changes speed.

Existing avatar-movement keys (WASD/arrows) keep producing
`AddControlFlags`/`RemoveControlFlags` regardless of camera mode, except in
`Free` where they steer the camera.

The active camera should eventually feed `SetCamera` so the simulator's
interest list reflects what the user is looking at — without that, object
updates may stay coarse around an actually-looked-at area.

## 2D Mode (Map)

Stable baseline. After the renderer-interface refactor, behavior is unchanged:

- cached region map tile as background
- 16 m grid and region border
- oriented marker rectangles per object/avatar
- pan/zoom via `Map` camera
- good for navigation, debugging, server-state inspection

This stays the default until 3D is shippable.

## 3D Mode (Perspective)

Backend recommendation: **moderngl** running inside a pygame OpenGL window.

- pygame already manages the window and event loop; moderngl gives a clean
  Python API over a real GL context.
- pygfx/wgpu is a viable later upgrade but adds a dependency and a learning
  curve before it pays.
- Raw PyOpenGL works too but is more boilerplate per mesh.

First 3D target:

- region floor as a single textured quad using the cached map tile (Z = 0).
- objects: instanced primitive meshes from the shape tier, tinted with
  per-pcode fallback color, optionally textured with the prim's default
  texture once a texture cache exists.
- avatars: capsules or upright billboards.
- one directional light driven by `Scene.sun_phase`; ambient fill; simple
  exponential fog.
- HUD as a fullscreen quad textured from the pygame_gui surface.
- no mesh/sculpt geometry, no terrain elevation, no per-face textures.

Performance budget for v1: a couple hundred prims, one draw call per primitive
shape via instancing. Don't optimize further until the live region forces it.

## On 2.5D

The previous plan put 2.5D before 3D. Reconsider: 2.5D in pygame requires
projection math, depth-sorting, oblique camera handling, and shape silhouettes.
All of that gets thrown away (or rebuilt in shaders) when 3D lands. The
intermediate "validates the camera/entity boundaries" claim is mostly absorbed
by the renderer-interface refactor, which can be validated with `TopDownRenderer`
alone.

Recommendation:

- **Skip 2.5D as a planned step.** Go 2D → renderer-interface refactor → 3D.
- Revisit 2.5D only if a no-GL deployment target appears (e.g. a remote shell
  with software rendering) or if 3D bring-up stalls and a stopgap is needed.

## Multi-Region Rendering

`WorldClient` is multi-circuit. The 2D viewer currently only renders the
current region. The 3D renderer should at least anticipate neighbors:

- `Scene` accepts `(region_origin_world_xy, world_view)` per circuit.
- Entities are placed at `region_origin + local_position`.
- Region floors tile across the visible neighborhood when child sims have
  posted `RegionMapTileReady` events.
- v1 can ship single-region; the data-shape decision must be made *before*
  3D entity placement so the offset is a no-op rather than a refactor.

## Controls

Keep user muscle memory stable across modes:

- WASD/arrows: avatar movement (Map, Orbit, Eye); camera movement (Free).
- mouse wheel: zoom (Map), camera distance (Orbit), FOV or speed (Eye/Free).
- right-drag: pan (Map), rotate (Orbit/Eye/Free).
- `C`: recenter camera on avatar.
- View menu: render mode + camera mode.
- chat input and other pygame_gui text fields consume keys before movement
  handling. (Already true — preserve through the refactor.)

## Acceptable First-Pass Gaps

- No mesh, no sculpt, no terrain elevation.
- No per-face textures except possibly the ground map tile.
- Approximate rotations (use the quat directly; ignore non-uniform scale +
  shear interplay).
- Parcel overlays still depend on future parcel-metadata work.
- Sky is a single colour or a solid horizon gradient — no skybox.

The first 3D goal is spatial correctness and a useful camera, not parity with
a full SL viewer.

## Suggested Implementation Order

All work happens inside `src/vibestorm/viewer3d/` unless noted. Each step is
shippable on its own. Cost annotations are rough.

0. **Fork the viewer.** *(done 2026-05-04.)* Copy `viewer/` → `viewer3d/`,
   retarget intra-package imports, change window caption, add `viewer3d`
   command to `run.sh`. Behavior identical to the 2D viewer.
1a. **`SceneEntity` DTO.** *(done 2026-05-04.)* Promoted `Marker` in
    `viewer3d/scene.py` to a renderer-agnostic `SceneEntity` carrying
    `kind`, full quaternion `rotation`, `default_texture_id`, `tint`, and
    a placeholder `shape: PrimShape | None = None`. Surfaced `sun_phase`
    on `Scene`. Renamed `object_markers`/`avatar_markers` to
    `object_entities`/`avatar_entities`. New tests in
    `test/test_viewer3d_scene.py` (22 tests, all passing). The original
    `viewer/` package is untouched.
1b. **Parser → `shape`.** Extend the inbound `ObjectUpdate` parser in
    `src/vibestorm/udp/messages.py` to extract the 22-byte pre-tail block
    (`PathCurve`, `ProfileCurve`, `PathBegin`/`End`, `PathScaleX`/`Y`,
    `PathTwist`, `PathRevolutions`, `ProfileBegin`/`End`/`Hollow`, …),
    surface `path_curve`/`profile_curve` on `WorldObject`, and classify
    them into `PrimShape` for `SceneEntity.shape` in `viewer3d/scene.py`.
    Project memory already flagged this 22-byte block as "names known,
    semantics not"; this is the cheapest concrete win. Both 2D and 3D
    paths benefit. *(medium; touches `udp/messages.py`,
    `world/models.py`, `viewer3d/scene.py`, plus new tests)*
2. **Renderer interface inside `viewer3d/`.** Introduce the
   `ViewerRenderer` protocol and extract the existing 2D draw into a
   `TopDownRenderer` implementation. The fork now has the seam needed for
   mode switching. *(small)*
3. **Render-mode menu state.** Add View → Mode and per-mode camera-mode
   submenus to the `viewer3d` HUD. Mode switching is a no-op for modes
   that don't exist yet. *(small)*
4. **`Camera3D`.** Replace the 2D `Camera` in `viewer3d/` with a mode-aware
   camera; Map mode reproduces today's pan/zoom. *(medium; touches camera
   math + input)*
5. **moderngl bootstrap.** Add the dependency (gated behind a `viewer3d`
   extra in `pyproject.toml`), open a hybrid GL+pygame_gui window, draw a
   single textured quad (the map tile) plus the existing HUD. Validate the
   compositing path before any geometry. *(medium; new code, no protocol
   risk)*
6. **PerspectiveRenderer v0.** Instance one primitive (cube) per
   `SceneEntity`. Tint by pcode. Verify scale/rotation/position match the
   2D map at the same camera target. *(medium)*
7. **Primitive library.** Add sphere/cylinder/torus/prism meshes; pick by
   `SceneEntity.shape`. *(medium)*
8. **Lighting + fog.** Directional light from `sun_phase`; ambient;
   exponential fog. *(small)*
9. **Camera modes.** Orbit, Eye, Free. *(medium; mostly input glue)*
10. **`SetCamera` mirroring.** Outbound camera-vector update from active 3D
    camera. Validates with the simulator's interest list. *(small)*
11. **Multi-region rendering.** Extend `viewer3d` `Scene` to take neighbor
    `WorldView`s; place entities and floor tiles in a shared world frame.
    *(medium; depends on `WorldClient` exposing neighbors, which is
    independently planned)*
12. **Texture cache.** Resolve `default_texture_id` via `GetTexture`, cache
    PNG, and bind per-prim. *(medium–large; first real asset pipeline beyond
    map tiles and bakes)*
13. **Retire `viewer/` (optional).** Once `viewer3d` covers both 2D Map and
    3D Perspective modes and is stable, delete `src/vibestorm/viewer/` and
    repoint `./run.sh viewer` at `viewer3d` with a forced Map mode.

Items 1a–4 are the pre-3D refactor inside the fork. Items 5–8 are the
minimum viable 3D mode. 9–12 are quality-of-life and fidelity follow-ups.
13 is the eventual cleanup.

## Recommendation

Step 1a (`SceneEntity` DTO) is done. Next is step 1b — the wire-format
parser extension that fills `SceneEntity.shape`. It pre-pays the
primitive-library work (step 7) and resolves a long-standing project gap
(the 22-byte pre-tail block in `ObjectUpdate`). Once shape data is real,
step 2 (renderer interface) and step 4 (`Camera3D`) are small mechanical
refactors that prepare the fork for the moderngl bootstrap (step 5).
Skip 2.5D unless a concrete need emerges.

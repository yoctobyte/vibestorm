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
1b-i. **Parser decodes 23-byte path/profile block.** *(done 2026-05-04.)*
    Extended `_parse_one_object_update_entry` to decode the 23-byte
    `PathCurve`/`ProfileCurve`/`PathBegin`/.../`ProfileHollow` block (per
    `message_template.msg` lines 3307–3324) into a new `PrimShapeData`
    dataclass surfaced as `ObjectUpdateEntry.shape`. Fixed two
    self-cancelling off-by-one bugs (block was being skipped as 22 bytes;
    `ExtraParams` was being read with U16 length prefix instead of U8
    per template line 3339). Net effect: every TextureEntry/TextureAnim/
    NameValue/Data/Text/MediaURL/PSBlock payload was previously read one
    byte early; `default_texture_id` was the genuine UUID shifted left
    with a leading `0x00`. Updated 20 synthetic test bodies, regenerated
    `test/fixtures/live/index.json` (now 43 captures vs 8), and corrected
    `docs/reverse-engineered-protocol.md`. *(commit `baee76a`.)*
1b-ii. **`SceneEntity.shape` from path/profile.** *(done 2026-05-04.)*
    Surfaced `shape: PrimShapeData | None` on `WorldObject`. Added a
    `classify_prim_shape(path_curve, profile_curve) -> PrimShape | None`
    helper in `viewer3d/scene.py` covering cube/sphere/cylinder/torus/
    prism/ring/tube. Populated `SceneEntity.shape` from the new
    `WorldObject.shape` in the refresh loop. The OpenSim default sphere
    fixture now classifies as `"sphere"`. New tests for the classifier
    and end-to-end Scene population.
2. **Renderer interface inside `viewer3d/`.** *(done 2026-05-04.)*
   Introduced the `ViewerRenderer` protocol in
   `src/vibestorm/viewer3d/renderer.py` and a `TopDownRenderer` that
   wraps the existing 2D draw. `viewer3d/app.py` now holds a
   `ViewerRenderer` reference and routes `update` / `render` /
   `clear_caches` through it. The fork has the seam needed for mode
   switching; behavior unchanged. The protocol stays small (no
   `attach`/`detach`/`handle_event` yet) — those land when 3D backends
   need them in step 5. New tests in
   `test/test_viewer3d_renderer.py` (4 tests).
3. **Render-mode menu state.** *(done 2026-05-04.)* Added "Render: 2D
   Map" and "Render: 3D" buttons to the View menu. HUD tracks
   `render_mode` and exposes an `on_render_mode_change(mode)` callback;
   the status bar now shows the active mode. The app wires the callback
   to a no-op for `2d-map` and a chat alert ("3D mode is not implemented
   yet — staying on 2D Map.") for `3d`. Per-mode camera-mode submenu
   moves to step 4 alongside `Camera3D`. New tests in
   `test/test_viewer3d_hud_render_mode.py` (7 tests).
4. **`Camera3D`.** *(done 2026-05-04.)* Renamed `Camera` to `Camera3D`
   (with a `Camera = Camera3D` alias for backward compat) and added a
   `mode: CameraMode = "map"` field plus 3D-mode state (`yaw`, `pitch`,
   `distance`, `eye_position`, `target`). Map mode reproduces today's
   pan/zoom math bit-for-bit; 3D modes are state-only stubs that the
   PerspectiveRenderer in step 5+ will consume. The HUD's render-mode
   callback now also calls `camera.set_mode("orbit" if 3d else "map")`.
   New tests in `test/test_viewer3d_camera.py` (15 tests).
5a. **Renderer-swap proof.** *(done 2026-05-05.)* Added a software
    `PerspectiveRenderer` placeholder in `viewer3d/perspective.py`
    (dark-blue fill + crosshair + camera/scene labels) and a
    `build_renderer(mode, camera) -> ViewerRenderer` factory in
    `viewer3d/app.py`. `on_render_mode_change` now genuinely swaps the
    active `ViewerRenderer` (clearing the previous renderer's caches
    first) instead of flagging 3D as unimplemented. Picking "Render: 3D"
    in the View menu visibly switches the world surface; picking
    "Render: 2D Map" returns to the map view. New tests in
    `test/test_viewer3d_perspective.py` (9 tests). The placeholder
    keeps `ViewerRenderer`'s shape so step 5b can replace its body
    with moderngl without touching the swap plumbing.
5b-i. **moderngl + GLCompositor module.** *(done 2026-05-05.)* Added a
    `viewer3d` extra in `pyproject.toml` (Pillow + pygame-ce +
    pygame_gui + `moderngl>=5.10,<6`). Created
    `src/vibestorm/viewer3d/gl_compositor.py`: a small `GLCompositor`
    class owning a moderngl context, a fullscreen-quad VAO,
    textured-quad shaders, and a name → `moderngl.Texture` cache.
    Public surface: `clear(color)`, `upload_surface(name, surface)`,
    `draw(name, alpha=False)`, `has_texture`, `texture_size`,
    `release()`. Quad UV mapping flips V so pygame surfaces (top-row
    first) render right-side up without per-frame memory copies. New
    tests in `test/test_viewer3d_gl_compositor.py` (9 tests) drive
    real GL via `moderngl.create_standalone_context()` against a
    custom RGBA framebuffer; tests skip cleanly when no standalone
    context is available (CI without GPU/EGL). The compositor is GL
    only — no pygame display interaction yet, so the existing 2D path
    is untouched. *(medium; new code, no protocol risk)*
5b-ii. **Wire compositor into app.** *(done 2026-05-05.)* Switched the
    pygame display to `OPENGL | DOUBLEBUF | RESIZABLE`, created a
    `moderngl.Context` from the live window, and routed every frame
    through the compositor: the active renderer draws into a software
    `world_surface`, `pygame_gui` draws into a per-pixel-alpha
    `hud_surface`, the compositor uploads both as textures and draws
    them as fullscreen quads (world opaque → HUD with source-over
    alpha) before `display.flip()`. Resize reallocates surfaces and
    updates `ctx.viewport`. The `PerspectiveRenderer` placeholder now
    blits the cached map tile scaled to the surface (with its own
    in-renderer cache invalidated by `clear_caches`) so the user
    sees "the map tile drawn as a textured quad" when they pick
    Render: 3D. As a side-fix the long-latent unimported
    `clear_tile_cache` reference in `_with_render_cache_clear` is
    now a real import. New helpers `allocate_frame_surfaces` and
    `composite_frame` are extracted at module level and unit-tested.
    New tests in `test/test_viewer3d_app_compositor.py` (9 tests).
    *(medium; first real GL window, no geometry yet)*
6. **PerspectiveRenderer v0.** *(done 2026-05-05.)* Camera math
   (`look_at`, `perspective`, `orbit_eye`, `view_matrix`,
   `projection_matrix`) added to `Camera3D` in pure Python — no
   numpy dep. The `ViewerRenderer` protocol grew a `render_gl(scene,
   *, aspect)` hook; `TopDownRenderer` is a no-op there,
   `PerspectiveRenderer` is upgraded into a real GL renderer:
   compiles vertex/fragment shaders, allocates a unit-cube VBO + IBO,
   instances per-`SceneEntity` model matrices + tint into a dynamic
   buffer that grows as the entity count exceeds capacity, and
   draws with depth testing on (then off, so the HUD overlay still
   composites cleanly). The factory threads the moderngl context
   through (`build_renderer(mode, camera, *, ctx)`); the frame loop
   inlines the compositor sequence so `render_gl` runs between the
   world quad and the HUD overlay. The composite helper splits into
   `composite_world` / `composite_hud` for that. New tests in
   `test/test_viewer3d_camera_matrices.py` (14 tests, pure Python)
   and `test/test_viewer3d_perspective_gl.py` (6 tests, real GL via
   standalone context — a tinted unit cube actually appears at the
   framebuffer center). *(medium)*
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

Items 1a–4 are the pre-3D refactor inside the fork. Items 5a–8 are the
minimum viable 3D mode. 9–12 are quality-of-life and fidelity follow-ups.
13 is the eventual cleanup.

## Recommendation

Steps 1a, 1b-i, 1b-ii, 2, 3, 4, 5a, 5b-i, 5b-ii, and 6 are done. The
fork now renders real 3D geometry: one tinted unit cube per
`SceneEntity`, drawn through a perspective projection with depth
testing, on top of the cached map tile, with the HUD composited on
top via the existing GL compositor. **The minimum viable 3D mode is
shippable.** Next is step 7: primitive library — pick the cube /
sphere / cylinder / torus / prism mesh from
`SceneEntity.shape` (already populated by step 1b-ii) so the world
stops rendering as boxes-everywhere. After that, step 8 lights the
scene from `Scene.sun_phase`. Skip 2.5D unless a concrete need
emerges.

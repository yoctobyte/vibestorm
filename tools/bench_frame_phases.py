"""Where a frame goes on a region with almost nothing in it.

`bench_render_frame.py` answers "what do fifteen thousand prims cost", and
answers it with the sky, the terrain and the water switched *off* -- which is
right for that question and leaves this one unasked: the local test region
holds three prims and one avatar, and a two-hour soak of it ran at 6.2 frames
a second, first half and second half alike. Three prims cannot cost 160
milliseconds. Something else in the frame does, and nothing measured so far
was in a position to say what.

So this one holds the world fixed at the size the soak actually ran against
and turns the *scenery* on and off instead, one piece at a time, against a
real 256x256 heightmap. A row that barely moves when its feature is switched
off was not the cost; the row that collapses is.

Two things it deliberately does not measure, because they need a window and a
running session rather than a standalone context: the HUD's redraw and upload,
and `Scene.refresh_from_world_view`. `VIBESTORM_PROFILE_FRAMES=1` in a live
viewer covers both, and this bench exists to narrow what to look for there.

    .venv/bin/python tools/bench_frame_phases.py
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


from vibestorm.viewer3d.camera import Camera3D  # noqa: E402
from vibestorm.viewer3d.scene import PCODE_PRIM, Scene, SceneEntity  # noqa: E402
from vibestorm.world.terrain import RegionHeightmap  # noqa: E402

FRAME_SIZE = (1280, 800)
REGION_M = 256.0

#: What the local test region holds: three prims rezzed for the object-sync
#: work, and the avatar. Not a round number on purpose -- the point of this
#: bench is the world the soak actually ran against.
PRIMS = 3

#: The scenery, in the order the renderer draws it. Each row switches exactly
#: one of these off and leaves the rest alone.
FEATURES = ("render_sky", "render_terrain", "render_water", "render_neighbours")


def build_scene(*, terrain: bool = True) -> Scene:
    scene = Scene()
    for local_id in range(1, PRIMS + 1):
        scene.object_entities[local_id] = SceneEntity(
            local_id=local_id,
            pcode=PCODE_PRIM,
            kind="prim",
            position=(128.0 + local_id, 128.0, 25.0),
            scale=(0.5, 0.5, 0.5),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
        )
    if terrain:
        width = height = 256
        samples = [
            22.0 + 3.0 * math.sin(x / 19.0) * math.cos(y / 23.0)
            for y in range(height)
            for x in range(width)
        ]
        scene.terrain_heightmap = RegionHeightmap(
            width=width, height=height, samples=samples, revision=1
        )
    scene.water_height = 20.0
    scene.avatar_position = (128.0, 128.0, 26.0)
    return scene


def _time(fn, ctx, frames: int) -> float:
    fn()
    ctx.finish()
    best = float("inf")
    for _ in range(frames):
        start = time.perf_counter()
        fn()
        ctx.finish()  # the GPU's share counts too
        best = min(best, time.perf_counter() - start)
    return best * 1000.0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=30, help="frames to time per row")
    args = parser.parse_args(argv)

    try:
        import moderngl
    except ImportError as exc:
        print(f"moderngl unavailable: {exc}")
        return 1
    try:
        ctx = moderngl.create_standalone_context()
    except Exception as exc:  # pragma: no cover - depends on the machine
        print(f"no GL context: {exc}")
        return 1

    print(f"GL: {ctx.info.get('GL_RENDERER', '?')}")
    from vibestorm.viewer3d.perspective import PerspectiveRenderer

    colour = ctx.texture(FRAME_SIZE, components=4)
    depth = ctx.depth_renderbuffer(FRAME_SIZE)
    fbo = ctx.framebuffer(color_attachments=[colour], depth_attachment=depth)
    fbo.use()
    ctx.viewport = (0, 0, *FRAME_SIZE)
    aspect = FRAME_SIZE[0] / FRAME_SIZE[1]

    def fresh():
        camera = Camera3D(
            mode="eye",
            eye_position=(REGION_M / 2.0, REGION_M / 2.0, 26.0),
            target=(REGION_M / 2.0, REGION_M, 24.0),
        )
        return PerspectiveRenderer(camera, ctx=ctx)

    rows: list[tuple[str, float]] = []

    renderer = fresh()
    scene = build_scene()
    rows.append(
        ("everything on", _time(lambda: renderer.render_gl(scene, aspect=aspect), ctx, args.frames))
    )
    rows.append(
        (
            "  renderer.update only",
            _time(lambda: renderer.update(1.0 / 30.0, scene), ctx, args.frames),
        )
    )
    rows.append(
        (
            "  scene.advance_* only",
            _time(
                lambda: (scene.advance_clouds(1 / 30), scene.advance_water(1 / 30)),
                ctx,
                args.frames,
            ),
        )
    )

    for feature in FEATURES:
        renderer = fresh()
        scene = build_scene()
        setattr(scene, feature, False)
        rows.append(
            (
                f"without {feature}",
                _time(lambda: renderer.render_gl(scene, aspect=aspect), ctx, args.frames),
            )
        )

    renderer = fresh()
    scene = build_scene(terrain=False)
    rows.append(
        (
            "no heightmap at all",
            _time(lambda: renderer.render_gl(scene, aspect=aspect), ctx, args.frames),
        )
    )

    # The one cost that is not per frame but reads like it: the terrain mesh is
    # rebuilt whenever the heightmap's revision moves, and a region sends its
    # terrain a patch at a time with a bump apiece.
    renderer = fresh()
    scene = build_scene()
    renderer.render_gl(scene, aspect=aspect)
    bumped = float("inf")
    for _ in range(5):
        scene.terrain_heightmap.revision += 1
        start = time.perf_counter()
        renderer.render_gl(scene, aspect=aspect)
        ctx.finish()
        bumped = min(bumped, time.perf_counter() - start)
    rows.append(("after a heightmap revision bump", bumped * 1000.0))

    width = max(len(name) for name, _ in rows)
    for name, ms in rows:
        print(f"  {name:{width}s} {ms:8.2f} ms  ({1000.0 / ms if ms else 0:6.0f} fps ceiling)")

    fbo.release()
    colour.release()
    depth.release()
    ctx.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

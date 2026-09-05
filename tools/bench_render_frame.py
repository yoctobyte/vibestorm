"""What a frame of ``PerspectiveRenderer.render_gl`` costs, by region size.

Companion to ``bench_scene_refresh.py``. That one measures deriving the scene;
this one measures drawing it. Together they are the floor under the viewer's
frame rate in a region of a given size, which is the number that decides
whether this client is usable on a real grid rather than on a test sim holding
a dozen prims.

It draws into an off-screen framebuffer through a standalone GL context, so it
opens no window. On this machine that is the real GPU; on a machine without
one it is llvmpipe, and the numbers then say more about Mesa than about the
renderer -- the row prints the renderer string so it is obvious which.

    .venv/bin/python tools/bench_render_frame.py
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from uuid import UUID  # noqa: E402

from vibestorm.viewer3d.camera import Camera3D  # noqa: E402
from vibestorm.viewer3d.scene import PCODE_PRIM, Scene, SceneEntity  # noqa: E402
from vibestorm.world.texture_entry import TextureEntry  # noqa: E402

#: Big enough that the per-frame CPU work is not lost in the noise of a
#: 64x64 test framebuffer, small enough to stay off any real display.
FRAME_SIZE = (1280, 800)

#: A region is 256 m across, and a viewer stands in the middle of one.
REGION_M = 256.0

SIZES = (1000, 5000, 15000)


def build_scene(count: int, *, per_face: bool = False, seed: int = 7) -> Scene:
    """A region of ``count`` cubes scattered over the ground.

    ``per_face`` gives each one a ``TextureEntry`` that names a texture for a
    single side. That is what forces the renderer to draw the cube as six
    meshes instead of one, and it is worth measuring separately: it is the
    minority case in-world, and it costs six times as much.
    """
    rng = random.Random(seed)
    scene = Scene()
    entry = (
        TextureEntry(default_texture_id=UUID(int=1), face_texture_ids=((2, UUID(int=2)),))
        if per_face
        else None
    )
    for local_id in range(1, count + 1):
        scene.object_entities[local_id] = SceneEntity(
            local_id=local_id,
            pcode=PCODE_PRIM,
            kind="prim",
            position=(rng.uniform(0.0, REGION_M), rng.uniform(0.0, REGION_M), 25.0),
            scale=(0.5, 0.5, 0.5),
            rotation=(0.0, 0.0, 0.0, 1.0),
            rotation_z_radians=0.0,
            texture_entry=entry,
        )
    scene.render_sky = False
    scene.render_terrain = False
    scene.render_water = False
    return scene


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=20, help="frames to time per row")
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

    rows = [(n, False) for n in SIZES] + [(n, True) for n in SIZES]
    for count, per_face in rows:
        # Standing in the middle of the region looking out across it, the
        # way an avatar does, so the whole region is roughly what is in front
        # of the camera.
        camera = Camera3D(
            mode="eye",
            eye_position=(REGION_M / 2.0, REGION_M / 2.0, 30.0),
            target=(REGION_M / 2.0, REGION_M, 26.0),
        )
        renderer = PerspectiveRenderer(camera, ctx=ctx)
        scene = build_scene(count, per_face=per_face)
        renderer.render_gl(scene, aspect=aspect)  # warm shaders and buffers
        times = []
        for _ in range(args.frames):
            start = time.perf_counter()
            renderer.render_gl(scene, aspect=aspect)
            ctx.finish()  # the GPU's share counts too
            times.append(time.perf_counter() - start)
        best = min(times) * 1000.0
        faces = "per-face textures" if per_face else "one texture each  "
        print(
            f"{count:6d} prims in view, {faces}  {best:7.2f} ms/frame  "
            f"({1000.0 / best:6.0f} fps ceiling)"
        )

    fbo.release()
    colour.release()
    depth.release()
    ctx.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

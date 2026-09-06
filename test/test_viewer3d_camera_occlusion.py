"""The camera not seeing round things, and what was done about it.

Two halves of one complaint. The ground march already existed but only ran
when the *eye itself* was underground, so a camera in clear air with a ridge
between it and the avatar answered "I am not underground" and drew the
inside of the hill across the picture. And nothing at all stopped a prim
standing in the way -- a wall, a floor above, the side of a building the
avatar has walked up to.

The two are kept separate on purpose. The ground is a heightfield and is
marched; prims are boxes and are cast against. What they share is where they
end: the eye is pulled in *along the segment it was on*, never lifted or
slid sideways, so the direction the viewer asked to look from survives.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")


def _entity(
    local_id: int,
    position: tuple[float, float, float],
    scale: tuple[float, float, float] = (2.0, 2.0, 2.0),
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0),
    tint: tuple[int, int, int] = (200, 200, 200),
):
    from vibestorm.viewer3d.scene import SceneEntity

    return SceneEntity(
        local_id=local_id,
        pcode=9,
        kind="object",
        rotation_z_radians=0.0,
        position=position,
        scale=scale,
        rotation=rotation,
        tint=tint,
    )


def _scene(*entities):
    from vibestorm.viewer3d.scene import Scene

    scene = Scene()
    scene.object_entities = {entity.local_id: entity for entity in entities}
    return scene


class RidgeBetweenTests(unittest.TestCase):
    """The ground march, now that it runs whether or not the eye is in it."""

    def test_a_ridge_between_the_two_pulls_the_camera_in(self) -> None:
        # The defect in one test: the eye is in clear air ten metres up, and
        # a wall of terrain stands between it and the avatar.
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        def ground(x: float, y: float) -> float | None:
            return 40.0 if -6.0 <= x <= -4.0 else 0.0

        held = eye_clear_of_the_ground((-10.0, 0.0, 10.0), (0.0, 0.0, 10.0), ground)

        self.assertGreater(held[0], -4.0, "the camera stayed behind the ridge")

    def test_ground_that_only_reaches_the_eye_itself_still_counts(self) -> None:
        # The march samples every metre out from the target and the eye is
        # its last sample, not a case of its own. Stop one sample short and
        # ground that begins between the last sample and the eye is missed
        # entirely -- which is the camera buried in a hillside, reported as
        # clear.
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        def ground(x: float, y: float) -> float | None:
            return 40.0 if x <= -9.5 else 0.0

        held = eye_clear_of_the_ground((-10.0, 0.0, 10.0), (0.0, 0.0, 10.0), ground)

        self.assertGreater(held[0], -9.6, "the camera was left inside the ground")

    def test_clear_air_all_the_way_leaves_the_eye_alone(self) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        eye = (-10.0, 0.0, 50.0)
        held = eye_clear_of_the_ground(eye, (0.0, 0.0, 50.0), lambda x, y: 0.0)

        self.assertEqual(held, eye)

    def test_the_void_past_the_region_edge_blocks_nothing(self) -> None:
        # `None` is "there is no ground here", which is not the same as
        # ground at zero: a camera out over the void is inside nothing.
        from vibestorm.viewer3d.camera import eye_clear_of_the_ground

        eye = (-10.0, 0.0, 1.0)
        held = eye_clear_of_the_ground(eye, (0.0, 0.0, 1.0), lambda x, y: None)

        self.assertEqual(held, eye)


class SightBlockedTests(unittest.TestCase):
    """Pulling the eye in front of whatever the scene says is in the way."""

    def test_nothing_in_the_way_leaves_the_eye_alone(self) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_view

        eye = (10.0, 0.0, 0.0)
        held = eye_clear_of_the_view(eye, (0.0, 0.0, 0.0), lambda target, eye: None)

        self.assertEqual(held, eye)

    def test_a_blocker_past_the_eye_leaves_it_alone(self) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_view

        eye = (10.0, 0.0, 0.0)
        held = eye_clear_of_the_view(eye, (0.0, 0.0, 0.0), lambda target, eye: 1.0)

        self.assertEqual(held, eye)

    def test_the_eye_stops_short_of_what_is_in_the_way(self) -> None:
        from vibestorm.viewer3d.camera import SIGHT_CLEARANCE_M, eye_clear_of_the_view

        held = eye_clear_of_the_view(
            (10.0, 0.0, 0.0), (0.0, 0.0, 0.0), lambda target, eye: 0.5
        )

        self.assertAlmostEqual(held[0], 5.0 - SIGHT_CLEARANCE_M)

    def test_the_clearance_is_in_metres_not_in_fractions(self) -> None:
        # A boom twice as long must still stop the same distance in front of
        # the wall, not twice as far.
        from vibestorm.viewer3d.camera import SIGHT_CLEARANCE_M, eye_clear_of_the_view

        near = eye_clear_of_the_view(
            (10.0, 0.0, 0.0), (0.0, 0.0, 0.0), lambda target, eye: 0.5
        )
        far = eye_clear_of_the_view(
            (20.0, 0.0, 0.0), (0.0, 0.0, 0.0), lambda target, eye: 0.25
        )

        self.assertAlmostEqual(near[0], 5.0 - SIGHT_CLEARANCE_M)
        self.assertAlmostEqual(far[0], 5.0 - SIGHT_CLEARANCE_M)

    def test_a_blocker_against_the_avatar_stops_just_short_of_it(self) -> None:
        # Never past the target, and never onto it either.
        from vibestorm.viewer3d.camera import MINIMUM_BOOM_M, eye_clear_of_the_view

        held = eye_clear_of_the_view(
            (10.0, 0.0, 0.0), (0.0, 0.0, 0.0), lambda target, eye: 0.01
        )

        self.assertAlmostEqual(held[0], MINIMUM_BOOM_M)

    def test_the_camera_never_lands_on_the_avatar(self) -> None:
        # An eye on its target has no direction to look in: `look_at`
        # normalises a zero vector, every axis of the view matrix goes to
        # zero with it, and the world maps to the origin with w = 0. Nothing
        # raises; the frame is simply wrong.
        from vibestorm.viewer3d.camera import eye_clear_of_the_view, look_at

        target = (0.0, 0.0, 0.0)
        for fraction in (0.0, 0.001, 0.01, 0.02):
            held = eye_clear_of_the_view(
                (10.0, 0.0, 0.0), target, lambda t, e, f=fraction: f
            )
            self.assertNotEqual(held, target, f"a blocker at {fraction} collapsed it")
            self.assertNotEqual(
                look_at(held, target)[:3],
                (0.0, 0.0, 0.0),
                f"a blocker at {fraction} made the view matrix degenerate",
            )

    def test_a_boom_shorter_than_the_minimum_is_left_alone(self) -> None:
        # The minimum cannot be honoured and the eye cannot be pushed *out*,
        # so what is left is to leave it where it was.
        from vibestorm.viewer3d.camera import MINIMUM_BOOM_M, eye_clear_of_the_view

        eye = (MINIMUM_BOOM_M / 2.0, 0.0, 0.0)
        held = eye_clear_of_the_view(eye, (0.0, 0.0, 0.0), lambda target, e: 0.1)

        self.assertAlmostEqual(held[0], eye[0])

    def test_pulling_in_does_not_change_where_the_camera_looks_from(self) -> None:
        from vibestorm.viewer3d.camera import _normalize, _sub, eye_clear_of_the_view

        target = (0.0, 0.0, 10.0)
        eye = (-8.0, -6.0, 14.0)
        held = eye_clear_of_the_view(eye, target, lambda t, e: 0.6)

        wanted = _normalize(_sub(eye, target))
        got = _normalize(_sub(held, target))
        for axis in range(3):
            self.assertAlmostEqual(got[axis], wanted[axis], places=5)

    def test_an_eye_sitting_on_its_target_is_not_divided_by(self) -> None:
        from vibestorm.viewer3d.camera import eye_clear_of_the_view

        eye = (3.0, 3.0, 3.0)
        held = eye_clear_of_the_view(eye, eye, lambda target, e: 0.5)

        self.assertEqual(held, eye)


class RayHitsEntityTests(unittest.TestCase):
    """The box test both the picker and the camera go through."""

    def _hit(self, origin, direction, entity):
        from vibestorm.viewer3d.perspective import _ray_hits_entity

        return _ray_hits_entity(origin, direction, entity)

    def test_a_ray_straight_at_a_box_meets_its_near_face(self) -> None:
        entity = _entity(1, (10.0, 0.0, 0.0), scale=(2.0, 2.0, 2.0))
        self.assertAlmostEqual(self._hit((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), entity), 9.0)

    def test_a_ray_that_misses_meets_nothing(self) -> None:
        entity = _entity(1, (10.0, 20.0, 0.0))
        self.assertIsNone(self._hit((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), entity))

    def test_a_ray_pointing_away_meets_nothing(self) -> None:
        entity = _entity(1, (10.0, 0.0, 0.0))
        self.assertIsNone(self._hit((0.0, 0.0, 0.0), (-1.0, 0.0, 0.0), entity))

    def test_a_ray_starting_inside_the_box_meets_nothing(self) -> None:
        # Both callers want this: a click inside the prim you are standing in
        # should reach what is beyond it, and a camera already inside
        # something has nothing to be pulled in front of.
        entity = _entity(1, (0.0, 0.0, 0.0), scale=(10.0, 10.0, 10.0))
        self.assertIsNone(self._hit((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), entity))

    def test_a_prim_is_met_where_it_is_turned_to_not_where_its_bounds_are(
        self,
    ) -> None:
        # A long thin prim turned forty-five degrees about z. An
        # axis-aligned test against its bounds would be met well before its
        # own face is.
        import math

        half = math.sqrt(0.5)
        entity = _entity(
            1, (10.0, 0.0, 0.0), scale=(8.0, 0.5, 4.0), rotation=(0.0, 0.0, half, half)
        )
        # Turned about z, the 8 m axis now runs along y, so along x the prim
        # is only half a metre thick.
        self.assertAlmostEqual(
            self._hit((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), entity), 9.75
        )

    def test_a_prim_with_no_shape_yet_is_not_in_the_way(self) -> None:
        from vibestorm.viewer3d.scene import SceneEntity

        entity = SceneEntity(
            local_id=1,
            pcode=9,
            kind="object",
            rotation_z_radians=0.0,
            position=(10.0, 0.0, 0.0),
            scale=None,
            rotation=None,
        )
        self.assertIsNone(self._hit((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), entity))


class FirstPrimInTheWayTests(unittest.TestCase):
    """Walking the region for whatever stands across the camera boom."""

    def _blocked(self, scene, target, eye):
        from vibestorm.viewer3d.perspective import _first_prim_in_the_way

        return _first_prim_in_the_way(scene, target, eye)

    def test_an_empty_region_blocks_nothing(self) -> None:
        self.assertIsNone(self._blocked(_scene(), (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))

    def test_a_prim_across_the_boom_is_found(self) -> None:
        scene = _scene(_entity(1, (5.0, 0.0, 0.0), scale=(2.0, 2.0, 2.0)))

        self.assertAlmostEqual(
            self._blocked(scene, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)), 0.4
        )

    def test_a_prim_past_the_eye_is_not_in_the_way(self) -> None:
        scene = _scene(_entity(1, (20.0, 0.0, 0.0)))

        self.assertIsNone(self._blocked(scene, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))

    def test_a_prim_beside_the_boom_is_not_in_the_way(self) -> None:
        scene = _scene(_entity(1, (5.0, 20.0, 0.0)))

        self.assertIsNone(self._blocked(scene, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))

    def test_the_nearest_of_several_is_the_one_that_counts(self) -> None:
        scene = _scene(
            _entity(1, (8.0, 0.0, 0.0)),
            _entity(2, (3.0, 0.0, 0.0)),
            _entity(3, (6.0, 0.0, 0.0)),
        )

        self.assertAlmostEqual(
            self._blocked(scene, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)), 0.2
        )

    def test_a_wide_prim_whose_centre_is_far_off_is_still_found(self) -> None:
        # The reject box is grown by each prim's own reach rather than by a
        # fixed margin, which is the whole reason a megaprim standing well
        # off the line still counts.
        scene = _scene(_entity(1, (5.0, 30.0, 0.0), scale=(4.0, 80.0, 4.0)))

        self.assertIsNotNone(self._blocked(scene, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))

    def test_an_eye_sitting_on_its_target_is_not_divided_by(self) -> None:
        scene = _scene(_entity(1, (5.0, 0.0, 0.0)))

        self.assertIsNone(self._blocked(scene, (1.0, 1.0, 1.0), (1.0, 1.0, 1.0)))

    def test_the_region_next_door_is_not_consulted(self) -> None:
        # Its prims are 256 m away and the boom is metres long, and its local
        # ids mean nothing here.
        from vibestorm.viewer3d.scene import Scene

        scene = Scene()
        scene.neighbour_object_entities = {(7, 1): _entity(1, (5.0, 0.0, 0.0))}

        self.assertIsNone(self._blocked(scene, (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))


class SightBlockedByTests(unittest.TestCase):
    """The closure the renderer hands the camera once a frame."""

    def test_it_answers_the_same_as_the_walk(self) -> None:
        from vibestorm.viewer3d.perspective import (
            _first_prim_in_the_way,
            sight_blocked_by,
        )

        scene = _scene(_entity(1, (5.0, 0.0, 0.0)))
        target, eye = (0.0, 0.0, 0.0), (10.0, 0.0, 0.0)

        self.assertEqual(
            sight_blocked_by(scene)(target, eye),
            _first_prim_in_the_way(scene, target, eye),
        )

    def test_the_same_question_is_only_worked_out_once(self) -> None:
        # The camera is asked for its eye several times a frame -- the view
        # matrix, the water pass, the picker -- and each answer walks the
        # whole region.
        from vibestorm.viewer3d import perspective

        scene = _scene(_entity(1, (5.0, 0.0, 0.0)))
        calls = 0
        real = perspective._first_prim_in_the_way

        def counted(scene, target, eye):
            nonlocal calls
            calls += 1
            return real(scene, target, eye)

        perspective._first_prim_in_the_way = counted
        try:
            blocked = perspective.sight_blocked_by(scene)
            blocked((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
            blocked((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
            blocked((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
        finally:
            perspective._first_prim_in_the_way = real

        self.assertEqual(calls, 1)

    def test_a_different_question_is_worked_out_again(self) -> None:
        from vibestorm.viewer3d import perspective

        scene = _scene(_entity(1, (5.0, 0.0, 0.0)))
        blocked = perspective.sight_blocked_by(scene)

        self.assertIsNotNone(blocked((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))
        self.assertIsNone(blocked((0.0, 20.0, 0.0), (10.0, 20.0, 0.0)))


class CameraEyeTests(unittest.TestCase):
    """The two held together, in the camera that has to use both."""

    def _camera(self, **kwargs):
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(**kwargs)
        camera.set_mode("free")
        return camera

    def test_a_camera_told_nothing_goes_where_it_is_put(self) -> None:
        camera = self._camera(
            eye_position=(-10.0, 0.0, 5.0), target=(0.0, 0.0, 5.0)
        )

        self.assertEqual(camera.eye(), (-10.0, 0.0, 5.0))

    def test_a_prim_in_the_way_pulls_the_camera_in(self) -> None:
        camera = self._camera(
            eye_position=(-10.0, 0.0, 5.0), target=(0.0, 0.0, 5.0)
        )
        camera.sight_blocked = lambda target, eye: 0.5

        self.assertGreater(camera.eye()[0], -10.0)
        self.assertLess(camera.eye()[0], 0.0)

    def test_first_person_is_never_pulled_in(self) -> None:
        # In `eye` mode the eye is the avatar's own head. Where that goes is
        # the simulator's business.
        camera = self._camera(
            eye_position=(-10.0, 0.0, 5.0), target=(0.0, 0.0, 5.0)
        )
        camera.set_mode("eye")
        camera.sight_blocked = lambda target, eye: 0.1
        camera.ground_height = lambda x, y: 100.0

        self.assertEqual(camera.eye(), (-10.0, 0.0, 5.0))

    def test_the_ground_and_the_prims_both_get_a_say(self) -> None:
        # The ground runs first and the prim question is then asked about the
        # segment that came back, so the nearer of the two wins whichever it
        # is. Here the prim is nearer.
        camera = self._camera(
            eye_position=(-10.0, 0.0, 5.0), target=(0.0, 0.0, 5.0)
        )
        camera.ground_height = lambda x, y: 40.0 if x < -8.0 else 0.0
        seen: list[tuple] = []

        def blocked(target, eye):
            seen.append(eye)
            return 0.25

        camera.sight_blocked = blocked
        held = camera.eye()

        self.assertGreater(seen[0][0], -10.0, "the prims were asked about the raw eye")
        self.assertGreater(held[0], seen[0][0], "the prim never pulled it further in")


def _try_create_context():
    try:
        import moderngl
    except ImportError:
        return None, "moderngl not installed"
    try:
        return moderngl.create_standalone_context(), None
    except Exception as exc:  # noqa: BLE001 - any GL failure is a skip
        return None, f"standalone GL context unavailable: {exc}"


class CameraOcclusionGLTests(unittest.TestCase):
    """Render it: is the avatar still behind the wall, or in front of it?"""

    FBO_SIZE = (64, 64)

    def setUp(self) -> None:
        ctx, err = _try_create_context()
        if ctx is None:
            self.skipTest(err)
        self.ctx = ctx
        self._color_tex = ctx.texture(self.FBO_SIZE, components=4)
        self._depth_rb = ctx.depth_renderbuffer(self.FBO_SIZE)
        self.fbo = ctx.framebuffer(
            color_attachments=[self._color_tex], depth_attachment=self._depth_rb
        )
        self.fbo.use()
        ctx.viewport = (0, 0, *self.FBO_SIZE)

    def tearDown(self) -> None:
        self.fbo.release()
        self._color_tex.release()
        self._depth_rb.release()
        self.ctx.release()

    def _scene(self):
        """A marker at the target and a wall four metres in front of it."""
        scene = _scene_with_wall()
        scene.render_water = False
        scene.render_terrain = False
        return scene

    def _draw(self, renderer, scene):
        self.ctx.clear(red=0.0, green=0.0, blue=0.0, alpha=1.0, depth=1.0)
        renderer.render_gl(scene, aspect=1.0)
        data = self.fbo.read(components=4)
        width, height = self.FBO_SIZE
        offset = (((height - 1) - 32) * width + 32) * 4
        return tuple(data[offset : offset + 4])

    def _renderer(self):
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.perspective import PerspectiveRenderer

        camera = Camera3D(
            eye_position=(128.0, 118.0, 30.0), target=(128.0, 128.0, 30.0)
        )
        camera.set_mode("free")
        camera.set_screen_size(self.FBO_SIZE)
        return PerspectiveRenderer(camera, ctx=self.ctx)

    def test_the_camera_comes_round_in_front_of_the_wall(self) -> None:
        from vibestorm.viewer3d import perspective

        renderer = self._renderer()
        blind = perspective.sight_blocked_by
        try:
            # What the viewer did before: nothing tells the camera the wall
            # is there, so it stays behind it and the wall fills the frame.
            perspective.sight_blocked_by = lambda scene: (lambda target, eye: None)
            behind = self._draw(renderer, self._scene())
            perspective.sight_blocked_by = blind
            in_front = self._draw(renderer, self._scene())
        finally:
            perspective.sight_blocked_by = blind
            renderer.clear_caches()

        # The wall is green and the marker where the avatar stands is red,
        # so the centre pixel says which of the two the camera is looking at
        # rather than merely that the frame changed.
        self.assertGreater(behind[1], behind[0], "the wall was not in the way to start")
        self.assertGreater(
            in_front[0], in_front[1], "the camera never came round the wall"
        )

    def test_the_camera_it_actually_drew_from_is_in_front_of_the_wall(self) -> None:
        renderer = self._renderer()
        try:
            self._draw(renderer, self._scene())
            held = renderer.camera.eye()
        finally:
            renderer.clear_caches()

        # The wall stands at y = 124, two metres thick. In front of it means
        # north of its near face, between the wall and the avatar.
        self.assertGreater(held[1], 125.0, "the camera is still inside or behind it")
        self.assertLess(held[1], 128.0, "the camera was pulled past the avatar")


def _scene_with_wall():
    """A red marker where the avatar stands, and a green wall in front of it.

    Two colours because both prims are drawn untextured: with one tint the
    centre pixel is the same grey whether the camera is looking at the wall
    or at what is behind it, and the test agrees with the bug.
    """
    return _scene(
        _entity(1, (128.0, 124.0, 30.0), scale=(20.0, 2.0, 20.0), tint=(0, 220, 0)),
        _entity(2, (128.0, 128.0, 30.0), scale=(1.0, 1.0, 1.0), tint=(220, 0, 0)),
    )

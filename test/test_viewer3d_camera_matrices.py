"""Tests for the Camera3D 3D matrix helpers (step 6).

The view + projection matrices are pure-Python column-major mat4
tuples — no GL context required. These tests verify the standard
mathematical identities (eye maps to camera origin, near plane lands
at NDC depth -1) so renderers can rely on the matrices without
re-deriving the math.
"""

import math
import unittest


def _mat4_times_vec4(
    m: tuple[float, ...], v: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Multiply a column-major 4x4 matrix with a column vector."""
    out = [0.0, 0.0, 0.0, 0.0]
    for row in range(4):
        s = 0.0
        for col in range(4):
            s += m[col * 4 + row] * v[col]
        out[row] = s
    return tuple(out)


class LookAtTests(unittest.TestCase):
    def test_eye_maps_to_camera_origin(self) -> None:
        from vibestorm.viewer3d.camera import look_at

        eye = (10.0, 5.0, 3.0)
        target = (0.0, 0.0, 0.0)
        view = look_at(eye, target)

        x, y, z, w = _mat4_times_vec4(view, (*eye, 1.0))
        self.assertAlmostEqual(x, 0.0, places=5)
        self.assertAlmostEqual(y, 0.0, places=5)
        self.assertAlmostEqual(z, 0.0, places=5)
        self.assertAlmostEqual(w, 1.0, places=5)

    def test_target_is_in_front_of_camera_negative_z(self) -> None:
        # Camera looks down -Z in camera space; target should land at
        # z < 0 after the view transform.
        from vibestorm.viewer3d.camera import look_at

        view = look_at((0.0, 0.0, 5.0), (0.0, 0.0, 0.0))
        _x, _y, z, _w = _mat4_times_vec4(view, (0.0, 0.0, 0.0, 1.0))

        self.assertLess(z, 0.0)

    def test_up_axis_lands_along_positive_y_in_camera_space(self) -> None:
        # SL convention: world Z is up. After look_at, world +Z should
        # project onto camera +Y.
        from vibestorm.viewer3d.camera import look_at

        view = look_at((10.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        # Direction (0,0,1) in world: w=0 so it's a direction, not point.
        _x, y, _z, _w = _mat4_times_vec4(view, (0.0, 0.0, 1.0, 0.0))

        self.assertAlmostEqual(y, 1.0, places=5)


class PerspectiveTests(unittest.TestCase):
    def test_near_plane_maps_to_ndc_minus_one(self) -> None:
        from vibestorm.viewer3d.camera import perspective

        proj = perspective(math.radians(60.0), 16.0 / 9.0, 0.1, 100.0)
        # A point on the near plane at the camera-space origin axis:
        # camera-space (0, 0, -near, 1).
        _x, _y, clip_z, clip_w = _mat4_times_vec4(proj, (0.0, 0.0, -0.1, 1.0))
        ndc_z = clip_z / clip_w

        self.assertAlmostEqual(ndc_z, -1.0, places=4)

    def test_far_plane_maps_to_ndc_plus_one(self) -> None:
        from vibestorm.viewer3d.camera import perspective

        proj = perspective(math.radians(60.0), 1.0, 0.1, 100.0)
        _x, _y, clip_z, clip_w = _mat4_times_vec4(proj, (0.0, 0.0, -100.0, 1.0))
        ndc_z = clip_z / clip_w

        self.assertAlmostEqual(ndc_z, 1.0, places=4)

    def test_invalid_args_reject(self) -> None:
        from vibestorm.viewer3d.camera import perspective

        with self.assertRaises(ValueError):
            perspective(0.0, 1.0, 0.1, 100.0)
        with self.assertRaises(ValueError):
            perspective(1.0, 0.0, 0.1, 100.0)
        with self.assertRaises(ValueError):
            perspective(1.0, 1.0, 0.5, 0.5)
        with self.assertRaises(ValueError):
            perspective(1.0, 1.0, -0.1, 100.0)


class OrbitEyeTests(unittest.TestCase):
    def test_yaw_zero_pitch_zero_places_eye_along_positive_x(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=5.0, yaw=0.0, pitch=0.0)

        eye = camera.orbit_eye()

        self.assertAlmostEqual(eye[0], 5.0, places=5)
        self.assertAlmostEqual(eye[1], 0.0, places=5)
        self.assertAlmostEqual(eye[2], 0.0, places=5)

    def test_yaw_quarter_turn_places_eye_along_positive_y(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            target=(0.0, 0.0, 0.0), distance=5.0, yaw=math.pi / 2, pitch=0.0
        )

        eye = camera.orbit_eye()

        self.assertAlmostEqual(eye[0], 0.0, places=5)
        self.assertAlmostEqual(eye[1], 5.0, places=5)

    def test_pitch_half_pi_places_eye_directly_above(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            target=(10.0, 20.0, 1.0), distance=8.0, yaw=0.0, pitch=math.pi / 2
        )

        eye = camera.orbit_eye()

        self.assertAlmostEqual(eye[0], 10.0, places=5)
        self.assertAlmostEqual(eye[1], 20.0, places=5)
        self.assertAlmostEqual(eye[2], 9.0, places=5)


class ViewMatrixModeTests(unittest.TestCase):
    def test_orbit_uses_orbit_eye(self) -> None:
        # In orbit mode, eye_position is ignored — the view matrix must
        # use the computed orbit eye, not the eye_position field.
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            target=(0.0, 0.0, 0.0),
            distance=5.0,
            yaw=0.0,
            pitch=0.0,
            eye_position=(999.0, 999.0, 999.0),
        )
        camera.set_mode("orbit")

        view = camera.view_matrix()

        # Orbit eye is (5, 0, 0); applying view to it should give origin.
        x, y, z, _ = _mat4_times_vec4(view, (5.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(x, 0.0, places=5)
        self.assertAlmostEqual(y, 0.0, places=5)
        self.assertAlmostEqual(z, 0.0, places=5)

    def test_eye_mode_uses_eye_position(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(
            target=(0.0, 0.0, 0.0), eye_position=(7.0, 0.0, 0.0)
        )
        camera.set_mode("eye")

        view = camera.view_matrix()

        x, y, z, _ = _mat4_times_vec4(view, (7.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(x, 0.0, places=5)
        self.assertAlmostEqual(y, 0.0, places=5)
        self.assertAlmostEqual(z, 0.0, places=5)


class WorldUpIsScreenUpTests(unittest.TestCase):
    """End-to-end orientation: world +Z (sky) must land in upper half of NDC.

    These compose view * projection — if either matrix has a sign error
    on the up axis, the whole 3D scene renders upside-down. Catching it
    here is much cheaper than chasing it via GL.
    """

    def test_point_above_target_lands_in_upper_ndc_in_orbit_mode(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=10.0, yaw=0.0, pitch=0.0)
        camera.set_mode("orbit")

        view = camera.view_matrix()
        proj = camera.projection_matrix(1.0)

        # A point one metre above the target (world Z=+1) must project
        # to ndc_y > 0 (top half of the GL framebuffer).
        vx, vy, vz, vw = _mat4_times_vec4(view, (0.0, 0.0, 1.0, 1.0))
        cx, cy, cz, cw = _mat4_times_vec4(proj, (vx, vy, vz, vw))
        ndc_y = cy / cw

        self.assertGreater(
            ndc_y, 0.0,
            f"world +Z (sky) must map to upper half of NDC; got ndc_y={ndc_y}",
        )

    def test_point_below_target_lands_in_lower_ndc(self) -> None:
        from vibestorm.viewer3d.camera import Camera3D

        camera = Camera3D(target=(0.0, 0.0, 0.0), distance=10.0, yaw=0.0, pitch=0.0)
        camera.set_mode("orbit")

        view = camera.view_matrix()
        proj = camera.projection_matrix(1.0)

        vx, vy, vz, vw = _mat4_times_vec4(view, (0.0, 0.0, -1.0, 1.0))
        _cx, cy, _cz, cw = _mat4_times_vec4(proj, (vx, vy, vz, vw))
        ndc_y = cy / cw

        self.assertLess(
            ndc_y, 0.0,
            f"world -Z (ground) must map to lower half of NDC; got ndc_y={ndc_y}",
        )


class ModelMatrixTests(unittest.TestCase):
    def test_identity_quaternion_unit_scale_translates_only(self) -> None:
        from vibestorm.viewer3d.perspective import model_matrix

        m = model_matrix((10.0, 20.0, 30.0), (1.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0))

        x, y, z, w = _mat4_times_vec4(m, (0.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(x, 10.0, places=5)
        self.assertAlmostEqual(y, 20.0, places=5)
        self.assertAlmostEqual(z, 30.0, places=5)
        self.assertAlmostEqual(w, 1.0, places=5)

    def test_scale_applied_before_translation(self) -> None:
        from vibestorm.viewer3d.perspective import model_matrix

        m = model_matrix((0.0, 0.0, 0.0), (2.0, 3.0, 4.0), (0.0, 0.0, 0.0, 1.0))

        x, y, z, _ = _mat4_times_vec4(m, (1.0, 1.0, 1.0, 1.0))
        self.assertAlmostEqual(x, 2.0, places=5)
        self.assertAlmostEqual(y, 3.0, places=5)
        self.assertAlmostEqual(z, 4.0, places=5)

    def test_quaternion_z_rotation(self) -> None:
        # 90° rotation around Z: world (1, 0, 0) -> (0, 1, 0).
        from vibestorm.viewer3d.perspective import model_matrix

        s = math.sin(math.pi / 4)
        c = math.cos(math.pi / 4)
        # Quat for rotation θ around Z: (0, 0, sin(θ/2), cos(θ/2))
        # θ = π/2 -> sin(π/4) = √2/2, cos(π/4) = √2/2
        m = model_matrix((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (0.0, 0.0, s, c))

        x, y, z, _ = _mat4_times_vec4(m, (1.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(x, 0.0, places=5)
        self.assertAlmostEqual(y, 1.0, places=5)
        self.assertAlmostEqual(z, 0.0, places=5)


if __name__ == "__main__":
    unittest.main()

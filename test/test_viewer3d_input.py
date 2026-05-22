import unittest


class Viewer3DInputTests(unittest.TestCase):
    def test_function_keys_emit_camera_presets(self) -> None:
        import pygame

        from vibestorm.bus import Bus
        from vibestorm.viewer3d.camera import Camera3D
        from vibestorm.viewer3d.input import handle_event

        pygame.init()
        try:
            camera = Camera3D()
            bus = Bus()
            cases = (
                (pygame.K_F1, "sim"),
                (pygame.K_F2, "avatar_behind"),
                (pygame.K_F3, "avatar_eye"),
            )

            for key, preset in cases:
                with self.subTest(key=key):
                    event = pygame.event.Event(pygame.KEYDOWN, key=key)
                    intent = handle_event(event, camera, bus)
                    self.assertEqual(intent.camera_preset, preset)
        finally:
            pygame.quit()


if __name__ == "__main__":
    unittest.main()

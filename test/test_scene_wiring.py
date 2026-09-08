"""Which bus event reaches which `Scene` method, in both viewers.

`_wire_scene` is nineteen lines of `bus.subscribe(SomeEvent, scene.apply_x)`
in the 3D viewer and seven in the 2D one, and it had no tests at all. That is
the shape this project has now been caught by six times: the appliers have
tests, the bus has tests, and nothing said that publishing `LayerDataReceived`
reaches the terrain. A subscription deleted, or pointed one line off at its
neighbour, is a feature that silently stops happening -- no exception, no
red test, just a world that never updates that one thing.

Two claims, and they fail differently:

- **Coverage.** Every `apply_*` on the scene is subscribed. Read off the class
  at run time, so an applier added later is covered without anyone
  remembering to come back here.
- **Routing.** Each event type reaches *its own* applier. The pairs are
  written out below rather than read from `_wire_scene`, because a pairing
  taken from the code under test agrees with that code by construction --
  which is exactly how the login screen's tests managed to pass on a bug.

The events are made with `object.__new__` and published unfilled. Nothing
here looks at a field: the question is only which method the bus picks, and
building nineteen fully-populated events would put nineteen constructors
between this file and the thing it is trying to say.
"""

import unittest
from types import SimpleNamespace

from vibestorm.bus import Bus
from vibestorm.bus.events import (
    AttachedSoundGainChanged,
    AttachedSoundReceived,
    AvatarAnimationReceived,
    ChatAlert,
    ChatIM,
    ChatLocal,
    ChatOutbound,
    EventQueueEventReceived,
    InventorySnapshotReady,
    LayerDataReceived,
    MeshAssetReady,
    ObjectAnimationReceived,
    ObjectInventorySnapshotReady,
    ParcelOverlayReceived,
    ParcelPropertiesReceived,
    RegionChanged,
    RegionMapTileReady,
    SoundTriggered,
    TextureAssetReady,
)


class _WiringCase(unittest.TestCase):
    """Wire a scene whose appliers are recorders, then publish at it."""

    #: Not a case of its own: it has no viewer to wire. `__test__` rather than
    #: a skip, so the run does not report five skipped tests that were never
    #: meant to exist.
    __test__ = False

    #: Subclasses set these.
    module = None
    scene_class = None
    pairs: tuple[tuple[type, str], ...] = ()

    def setUp(self) -> None:
        if self.module is None:
            self.skipTest("base class")
        self.calls: list[str] = []
        self.scene = self._recording_scene()
        self.bus = Bus()
        self.module._wire_scene(SimpleNamespace(bus=self.bus), self.scene)

    def appliers(self) -> set[str]:
        """The scene's applier names, read off the real class at run time."""
        return {name for name in dir(self.scene_class) if name.startswith("apply_")}

    def _recording_scene(self):
        """A stand-in carrying the real scene's applier names and nothing else.

        A stand-in rather than a real `Scene` with its methods replaced,
        because `Scene` has `slots=True` and an instance cannot shadow its own
        method -- and because a wiring test that needed a working scene would
        be testing the scene. The names still come from the real class, so an
        applier added there appears here without being typed twice.
        """
        calls = self.calls

        def recorder(name: str):
            # `staticmethod`, because a plain function in a class body becomes
            # a method and arrives with `self` in front of the event -- which
            # records the event instead of the name, and then fails while
            # printing it rather than while comparing it.
            return staticmethod(lambda _event=None, _name=name: calls.append(_name))

        return type("_RecordingScene", (), {name: recorder(name) for name in self.appliers()})()

    def publish(self, event_type: type) -> list[str]:
        self.calls.clear()
        self.bus.publish(object.__new__(event_type))
        return list(self.calls)

    # -- the two claims ----------------------------------------------------

    def test_every_applier_on_the_scene_is_subscribed(self) -> None:
        """Derived from the class, so a new applier is covered by default.

        An applier nothing publishes to is dead weight that reads like a
        feature. If one is deliberately not wired -- a scene method called
        from somewhere other than the bus -- this test is the place to say so
        out loud, not the place to be quietly wrong.
        """
        self.assertEqual(self.appliers(), {applier for _event, applier in self.pairs})

    def test_each_event_reaches_its_own_applier(self) -> None:
        for event_type, applier in self.pairs:
            with self.subTest(event_type.__name__):
                self.assertEqual(self.publish(event_type), [applier])

    def test_no_event_is_subscribed_twice(self) -> None:
        """Two subscriptions to one event is how a handler gets moved rather
        than replaced: the old one keeps running and the symptom is doubled
        work rather than none."""
        for event_type, _applier in self.pairs:
            with self.subTest(event_type.__name__):
                self.assertEqual(len(self.publish(event_type)), 1)

    def test_an_event_nobody_wired_reaches_nothing(self) -> None:
        """The control: `publish` is not calling everything in sight."""

        class _Unwired:
            pass

        self.calls.clear()
        self.bus.publish(_Unwired())
        self.assertEqual(self.calls, [])

    def test_a_region_change_clears_the_render_tile_cache(self) -> None:
        """The one subscription that is not a bare method reference.

        Tiles are keyed on nothing that mentions the region, so carrying them
        across a teleport draws the old region's ground under the new one.
        The wrapper is a single call in a single line and reads like
        decoration; this says it is not.
        """
        cleared: list[bool] = []
        original = self.module.clear_tile_cache
        self.module.clear_tile_cache = lambda: cleared.append(True)
        try:
            self.bus.publish(object.__new__(RegionChanged))
        finally:
            self.module.clear_tile_cache = original
        self.assertEqual(cleared, [True])
        self.assertEqual(self.calls, ["apply_region_changed"])


class Viewer3DSceneWiringTests(_WiringCase):
    """The 3D viewer, which wires all nineteen."""

    __test__ = True

    from vibestorm.viewer3d import app as module  # noqa: PLC0415
    from vibestorm.viewer3d.scene import Scene as scene_class  # noqa: PLC0415

    pairs = (
        (RegionChanged, "apply_region_changed"),
        (RegionMapTileReady, "apply_map_tile_ready"),
        (TextureAssetReady, "apply_texture_asset_ready"),
        (MeshAssetReady, "apply_mesh_asset_ready"),
        (ChatLocal, "apply_chat_local"),
        (ChatIM, "apply_chat_im"),
        (ChatAlert, "apply_chat_alert"),
        (ChatOutbound, "apply_chat_outbound"),
        (InventorySnapshotReady, "apply_inventory_snapshot_ready"),
        (ObjectInventorySnapshotReady, "apply_object_inventory_snapshot_ready"),
        (LayerDataReceived, "apply_layer_data_received"),
        (ParcelPropertiesReceived, "apply_parcel_properties"),
        (ParcelOverlayReceived, "apply_parcel_overlay"),
        (EventQueueEventReceived, "apply_event_queue_event"),
        (AvatarAnimationReceived, "apply_avatar_animation"),
        (ObjectAnimationReceived, "apply_object_animation"),
        (AttachedSoundReceived, "apply_attached_sound"),
        (AttachedSoundGainChanged, "apply_attached_sound_gain_change"),
        (SoundTriggered, "apply_sound_trigger"),
    )


class Viewer2DSceneWiringTests(_WiringCase):
    """The 2D viewer, whose scene has seven appliers and wires all seven.

    Worth having its own case rather than being folded into the one above:
    the two `_wire_scene` functions are separate copies with the same name in
    two modules, and the interesting failure is one of them drifting. The
    coverage test is what makes that visible -- it compares against *this*
    scene class, so an applier added here and wired only in the 3D viewer is
    a failure here.
    """

    __test__ = True

    from vibestorm.viewer import app as module  # noqa: PLC0415
    from vibestorm.viewer.scene import Scene as scene_class  # noqa: PLC0415

    pairs = (
        (RegionChanged, "apply_region_changed"),
        (RegionMapTileReady, "apply_map_tile_ready"),
        (ChatLocal, "apply_chat_local"),
        (ChatIM, "apply_chat_im"),
        (ChatAlert, "apply_chat_alert"),
        (ChatOutbound, "apply_chat_outbound"),
        (InventorySnapshotReady, "apply_inventory_snapshot_ready"),
    )


if __name__ == "__main__":
    unittest.main()

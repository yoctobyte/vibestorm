"""Tests for live sound and animation state in the Scene.

These six bus events were published from the session but had no consumer
anywhere — the "on the bus but no consumer" gap. Nothing in the test region
emits them, so this is the only coverage they will get until someone rezzes a
sound emitter or an animated object.

The design point being tested is that these are *current state*, not a log: a
new AvatarAnimation or AttachedSound replaces what was there, because that is
how a sim stops an animation or clears a sound. A trailing log would show a
stopped animation forever.
"""

import unittest
from uuid import UUID

from vibestorm.viewer3d.scene import Scene

OBJECT_A = UUID("aaaa0000-0000-0000-0000-000000000001")
OBJECT_B = UUID("aaaa0000-0000-0000-0000-000000000002")
SOUND_A = UUID("bbbb0000-0000-0000-0000-000000000001")
SOUND_B = UUID("bbbb0000-0000-0000-0000-000000000002")
ANIM_A = UUID("cccc0000-0000-0000-0000-000000000001")
ANIM_B = UUID("cccc0000-0000-0000-0000-000000000002")
OWNER = UUID("dddd0000-0000-0000-0000-000000000001")


class _AnimEntry:
    def __init__(self, animation_id):
        self.animation_id = animation_id
        self.sequence_id = 1
        self.source_object_id = None


class _Anim:
    def __init__(self, sender_id, *animation_ids):
        self.sender_id = sender_id
        self.animations = tuple(_AnimEntry(a) for a in animation_ids)


class _AnimEvent:
    def __init__(self, animation):
        self.region_handle = 1
        self.animation = animation


class _Sound:
    def __init__(self, object_id, sound_id, gain=1.0, flags=0):
        self.object_id = object_id
        self.sound_id = sound_id
        self.owner_id = OWNER
        self.gain = gain
        self.flags = flags


class _SoundEvent:
    def __init__(self, sound):
        self.region_handle = 1
        self.sound = sound


class _GainChange:
    def __init__(self, object_id, gain):
        self.object_id = object_id
        self.gain = gain


class _GainEvent:
    def __init__(self, change):
        self.region_handle = 1
        self.change = change


class AnimationStateTests(unittest.TestCase):
    def test_object_animations_are_recorded(self) -> None:
        scene = Scene()
        scene.apply_object_animation(_AnimEvent(_Anim(OBJECT_A, ANIM_A, ANIM_B)))

        self.assertEqual(scene.object_animations[OBJECT_A], (ANIM_A, ANIM_B))

    def test_a_new_message_replaces_rather_than_accumulates(self) -> None:
        # The sim stops an animation by sending a list that omits it.
        scene = Scene()
        scene.apply_object_animation(_AnimEvent(_Anim(OBJECT_A, ANIM_A, ANIM_B)))
        scene.apply_object_animation(_AnimEvent(_Anim(OBJECT_A, ANIM_B)))

        self.assertEqual(scene.object_animations[OBJECT_A], (ANIM_B,))

    def test_an_empty_list_clears_the_object(self) -> None:
        scene = Scene()
        scene.apply_object_animation(_AnimEvent(_Anim(OBJECT_A, ANIM_A)))
        scene.apply_object_animation(_AnimEvent(_Anim(OBJECT_A)))

        self.assertEqual(scene.object_animations[OBJECT_A], ())

    def test_avatar_and_object_animations_are_separate(self) -> None:
        scene = Scene()
        scene.apply_avatar_animation(_AnimEvent(_Anim(OBJECT_A, ANIM_A)))

        self.assertIn(OBJECT_A, scene.avatar_animations)
        self.assertNotIn(OBJECT_A, scene.object_animations)


class AttachedSoundStateTests(unittest.TestCase):
    def test_sound_is_bound_to_its_object(self) -> None:
        scene = Scene()
        scene.apply_attached_sound(_SoundEvent(_Sound(OBJECT_A, SOUND_A, gain=0.5)))

        state = scene.attached_sounds[OBJECT_A]
        self.assertEqual(state.sound_id, SOUND_A)
        self.assertAlmostEqual(state.gain, 0.5)

    def test_null_sound_id_clears_the_entry(self) -> None:
        # A null id is how a sim stops an object's looping sound. Storing it as
        # a zero UUID would make "silent" and "playing asset 0" identical.
        scene = Scene()
        scene.apply_attached_sound(_SoundEvent(_Sound(OBJECT_A, SOUND_A)))
        scene.apply_attached_sound(_SoundEvent(_Sound(OBJECT_A, UUID(int=0))))

        self.assertNotIn(OBJECT_A, scene.attached_sounds)

    def test_gain_change_updates_without_losing_the_sound_id(self) -> None:
        scene = Scene()
        scene.apply_attached_sound(_SoundEvent(_Sound(OBJECT_A, SOUND_A, gain=1.0)))
        scene.apply_attached_sound_gain_change(_GainEvent(_GainChange(OBJECT_A, 0.25)))

        state = scene.attached_sounds[OBJECT_A]
        self.assertEqual(state.sound_id, SOUND_A)
        self.assertAlmostEqual(state.gain, 0.25)

    def test_gain_change_for_an_unknown_object_is_ignored(self) -> None:
        # Inventing an entry would claim a sound whose id was never seen.
        scene = Scene()
        scene.apply_attached_sound_gain_change(_GainEvent(_GainChange(OBJECT_B, 0.5)))

        self.assertEqual(scene.attached_sounds, {})

    def test_a_second_sound_replaces_the_first_on_the_same_object(self) -> None:
        scene = Scene()
        scene.apply_attached_sound(_SoundEvent(_Sound(OBJECT_A, SOUND_A)))
        scene.apply_attached_sound(_SoundEvent(_Sound(OBJECT_A, SOUND_B)))

        self.assertEqual(scene.attached_sounds[OBJECT_A].sound_id, SOUND_B)


class SoundTriggerHistoryTests(unittest.TestCase):
    def test_triggers_accumulate(self) -> None:
        scene = Scene()
        for _ in range(3):
            scene.apply_sound_trigger(_SoundEvent(_Sound(OBJECT_A, SOUND_A)))

        self.assertEqual(len(scene.recent_sound_triggers), 3)

    def test_history_is_bounded(self) -> None:
        # One-shot sounds are unbounded in a busy region; the tail must not be.
        from vibestorm.viewer3d.scene import SOUND_TRIGGER_HISTORY

        scene = Scene()
        for _ in range(SOUND_TRIGGER_HISTORY + 20):
            scene.apply_sound_trigger(_SoundEvent(_Sound(OBJECT_A, SOUND_A)))

        self.assertEqual(len(scene.recent_sound_triggers), SOUND_TRIGGER_HISTORY)


class InspectorLiveActivityTests(unittest.TestCase):
    class _WorldObject:
        def __init__(self, full_id):
            self.full_id = full_id

    def test_playing_animations_and_live_sound_appear(self) -> None:
        from vibestorm.viewer3d.hud import _live_activity_lines

        scene = Scene()
        scene.apply_object_animation(_AnimEvent(_Anim(OBJECT_A, ANIM_A)))
        scene.apply_attached_sound(_SoundEvent(_Sound(OBJECT_A, SOUND_A, gain=0.75)))

        rows = _live_activity_lines(self._WorldObject(OBJECT_A), scene)

        self.assertIn(f"Playing Animations: {ANIM_A}", rows)
        self.assertIn(f"Attached Sound (live): {SOUND_A} gain 0.75", rows)

    def test_a_quiet_object_adds_no_rows(self) -> None:
        from vibestorm.viewer3d.hud import _live_activity_lines

        self.assertEqual(_live_activity_lines(self._WorldObject(OBJECT_B), Scene()), [])

    def test_missing_world_object_or_scene_is_tolerated(self) -> None:
        from vibestorm.viewer3d.hud import _live_activity_lines

        self.assertEqual(_live_activity_lines(None, Scene()), [])
        self.assertEqual(_live_activity_lines(self._WorldObject(OBJECT_A), None), [])


if __name__ == "__main__":
    unittest.main()

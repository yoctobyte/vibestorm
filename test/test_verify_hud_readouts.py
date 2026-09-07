"""The readout verifier's own arithmetic, checked before it is believed.

`tools/verify_hud_readouts.py` exists to be a *second opinion*: it recomputes
what the HUD claims, from the `WorldView`, by a different route, so that a
disagreement means the viewer is wrong. That only works if the second opinion
is right. A checker that miscounts reports a failure in the thing it is
checking, and whoever reads it goes looking in the wrong place -- which is the
one bug a verifier must not have.

So the counting rule gets a table, and the awkward case is in it: an avatar is
not a prim and is not counted, but an attachment is a prim whose parent *is*
an avatar. The scene's own transform pass has no pcode filter -- every object
with a position goes in, so attachments resolve and are drawn. The first
version of `_placeable_prims` dropped avatars from the parent lookup as well
as from the count, which would have made every attachment in a region read as
unplaceable and had the tool blame the viewer for drawing them.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


def _placeable():
    """The function under test, without importing the tool's whole stack.

    `verify_hud_readouts` pulls in the login client, the dispatcher and pygame
    at import time, none of which this needs. The source is read and the one
    function executed on its own -- which also means this test fails loudly if
    the function is renamed or moved rather than quietly testing nothing.
    """
    source = (
        Path(__file__).resolve().parents[1] / "tools" / "verify_hud_readouts.py"
    ).read_text()
    start = source.index("def _placeable_prims")
    end = source.index("async def main")
    namespace: dict = {}
    exec(compile(source[start:end], "verify_hud_readouts.py", "exec"), namespace)
    return namespace["_placeable_prims"]


class _Object:
    def __init__(self, local_id: int, pcode: int, parent_id: int = 0, position=(0.0, 0.0, 0.0)):
        self.local_id = local_id
        self.pcode = pcode
        self.parent_id = parent_id
        self.position = position


class _World:
    def __init__(self, objects):
        self.objects = {obj.local_id: obj for obj in objects}


class PlaceablePrimTests(unittest.TestCase):
    #: pcode 47 is an avatar; 9 is a primitive.
    CASES = (
        ("two roots are two prims", [_Object(1, 9), _Object(2, 9)], 2),
        ("a child of a present prim counts", [_Object(1, 9), _Object(2, 9, parent_id=1)], 2),
        ("a child of an absent parent does not", [_Object(2, 9, parent_id=99)], 0),
        ("an avatar on its own is not a prim", [_Object(1, 47)], 0),
        (
            "but an attachment hanging off one is",
            [_Object(1, 47), _Object(2, 9, parent_id=1)],
            1,
        ),
        ("an attachment whose avatar is missing is not", [_Object(2, 9, parent_id=1)], 0),
        ("a prim with no position is not placeable", [_Object(1, 9, position=None)], 0),
        (
            "and a parent cycle counts nothing rather than looping",
            [_Object(1, 9, parent_id=2), _Object(2, 9, parent_id=1)],
            0,
        ),
    )

    def test_the_count_matches_the_rule_the_scene_applies(self) -> None:
        placeable = _placeable()
        for name, objects, expected in self.CASES:
            with self.subTest(name):
                self.assertEqual(placeable(_World(objects)), expected)

    def test_a_deep_linkset_is_still_counted_in_full(self) -> None:
        """The depth limit exists for cycles and must not clip real linksets.

        A linkset of 32 is ordinary in-world; the guard sits at 64.
        """
        placeable = _placeable()
        chain = [_Object(1, 9)] + [_Object(i, 9, parent_id=i - 1) for i in range(2, 33)]
        self.assertEqual(placeable(_World(chain)), 32)

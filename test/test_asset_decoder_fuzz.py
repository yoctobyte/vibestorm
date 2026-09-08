"""Mutated real assets into every asset decoder, watching what class comes out.

`test_decoder_fuzz.py` does this for the wire decoders with random bytes.
Random bytes are the wrong corpus here: an asset format has a header, and
rubbish bounces off it without the body ever running. A sweep like that
reports no failures and means nothing -- the first attempt at this file had
five of seven decoders accepting *zero* inputs, which measured their length
checks and nothing else.

So each decoder starts from an asset it accepts and the bytes are mutated
from there: a flipped byte, a truncation, a tail of junk. That puts the
corpus where the interesting inputs are, one edit away from valid, which is
also where a grid's own slightly-wrong output lives.

The question is not whether a decoder rejects a mutant. It is **which class
it rejects with**. Four times in this client something underneath the code
has raised a class the code was not written to expect and it has left
through every handler untouched -- `DecompressionBombError` past
`decode_j2k`, `ExpatError` past `_login_sync`, `ParseError` past every cap
client, an `AttributeError` off a renamed wire field. Each decoder here
documents one error type, its callers catch that, and anything else is a
viewer that goes down because a grid sent an asset this client has not seen.

The acceptance floors matter as much as the assertion, for the reason
above: they say the body ran.
"""

from __future__ import annotations

import io
import random
import sys
import unittest
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_assets_animation import _fixture as _animation_fixture  # noqa: E402
from test_decoder_fuzz import _non_finite_floats  # noqa: E402
from test_assets_gesture import _gesture  # noqa: E402
from test_assets_wearable import _build as _wearable  # noqa: E402
from test_sl_mesh import _mesh_asset  # noqa: E402

from vibestorm.assets.animation import AnimationDecodeError, decode_animation  # noqa: E402
from vibestorm.assets.gesture import GestureDecodeError, decode_gesture  # noqa: E402
from vibestorm.assets.j2k import J2KDecodeError, decode_j2k  # noqa: E402
from vibestorm.assets.notecard import (  # noqa: E402
    NotecardDecodeError,
    decode_notecard,
    encode_notecard,
)
from vibestorm.assets.sl_mesh import (  # noqa: E402
    SLMeshDecodeError,
    decode_sl_mesh_asset,
    parse_binary_llsd,
)
from vibestorm.assets.wearable import WearableDecodeError, decode_wearable  # noqa: E402

#: Mutants per decoder. Enough that the floors below are comfortably cleared
#: and the whole file still runs in a couple of seconds.
ROUNDS = 500

#: Seeded, so a failure is reproducible and the suite does not shimmer.
SEED = 20260907


def _valid_j2k() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (30, 60, 90)).save(buffer, format="JPEG2000")
    return buffer.getvalue()


def _gesture_asset() -> bytes:
    """One animation step, which is the four-line kind."""
    return _gesture(steps="0\nanim\nb906c4ba-703b-1940-32a3-0c7f7d791510\n0")


class _Case:
    def __init__(self, name, decode, error, seed, floor: int):
        self.name = name
        self.decode = decode
        self.error = error
        self.seed = seed
        #: Mutants this decoder must have *decoded* rather than rejected.
        #: Set well under what a run reaches, so the number says "this ran"
        #: and not "this ran exactly this often".
        self.floor = floor


def _cases() -> tuple[_Case, ...]:
    return (
        _Case("decode_gesture", decode_gesture, GestureDecodeError, _gesture_asset(), 25),
        _Case(
            "decode_notecard",
            decode_notecard,
            NotecardDecodeError,
            encode_notecard("hello\nworld"),
            150,
        ),
        _Case(
            "decode_animation",
            decode_animation,
            AnimationDecodeError,
            _animation_fixture(),
            60,
        ),
        _Case(
            "decode_wearable",
            decode_wearable,
            WearableDecodeError,
            _wearable("parameters 0\ntextures 0\n"),
            60,
        ),
        _Case(
            "decode_sl_mesh_asset",
            decode_sl_mesh_asset,
            SLMeshDecodeError,
            _mesh_asset(),
            15,
        ),
        _Case("parse_binary_llsd", parse_binary_llsd, SLMeshDecodeError, _mesh_asset(), 120),
        _Case("decode_j2k", decode_j2k, J2KDecodeError, _valid_j2k(), 40),
    )


def _mutants(seed: bytes, rng: random.Random):
    """One to four edits: a flipped byte, a truncation, a tail of junk."""
    for _ in range(ROUNDS):
        data = bytearray(seed)
        for _ in range(rng.randint(1, 4)):
            roll = rng.random()
            if roll < 0.5 and data:
                data[rng.randrange(len(data))] = rng.randrange(256)
            elif roll < 0.8 and len(data) > 1:
                del data[rng.randrange(len(data)) :]
            else:
                data.extend(bytes(rng.randrange(256) for _ in range(rng.randint(1, 16))))
        yield bytes(data)


class AssetDecoderFuzzTests(unittest.TestCase):
    def test_every_seed_is_an_asset_its_decoder_accepts(self) -> None:
        """The floor under the floors.

        A seed the decoder rejects makes every mutant of it a rejection too,
        and the run reports nothing while looking like it did something. Five
        of these seven were in exactly that state when this file was written.
        """
        for case in _cases():
            with self.subTest(case.name):
                case.decode(case.seed)

    def test_no_decoder_raises_a_class_from_underneath_itself(self) -> None:
        rng = random.Random(SEED)
        for case in _cases():
            accepted = 0
            with warnings.catch_warnings():
                # Pillow warns about the pixel counts a mutated J2K header
                # claims. The warning is not the contract; the class is.
                warnings.simplefilter("ignore")
                for mutant in _mutants(case.seed, rng):
                    try:
                        case.decode(mutant)
                        accepted += 1
                    except case.error:
                        pass
                    except Exception as exc:  # noqa: BLE001 - the class is the assertion
                        self.fail(
                            f"{case.name} raised {type(exc).__module__}."
                            f"{type(exc).__name__} instead of {case.error.__name__}: {exc}"
                        )
            self.assertGreaterEqual(
                accepted,
                case.floor,
                f"{case.name} decoded only {accepted} of {ROUNDS} mutants; the "
                f"corpus is bouncing off a guard rather than running the body",
            )


    def test_no_decoder_hands_back_a_float_that_is_not_a_number(self) -> None:
        """The wire sweep's second question, asked of the asset formats.

        Seven wire decoders answer this one badly and each had to be followed
        to where its NaN lands. All seven asset decoders answer it cleanly.

        For the mesh that is by construction -- `_as_vec3` and `_as_vec2` fall
        back to the default bounding box -- but this sweep is not what shows
        it: both guards survive this corpus untouched, because flipping bytes
        rarely lands on the eight that make up a domain corner. The guards are
        pinned directly in `test_sl_mesh.py`. What this test is for is the
        rest: a decoder that starts letting one through without anybody having
        asked where it goes.

        There is deliberately no excuse list here. If one of these starts
        answering badly, that is a question about where the value lands, and
        the answer belongs in this file rather than in a set that silences it.
        """
        rng = random.Random(SEED)
        offenders: dict[str, tuple[str, float]] = {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for case in _cases():
                for mutant in _mutants(case.seed, rng):
                    if case.name in offenders:
                        break
                    try:
                        decoded = case.decode(mutant)
                    except Exception:  # noqa: BLE001 - the class is the other test's
                        continue
                    found = _non_finite_floats(decoded, case.name)
                    if found:
                        offenders[case.name] = found[0]
        self.assertEqual(offenders, {})


if __name__ == "__main__":
    unittest.main()

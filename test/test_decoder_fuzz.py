"""Rubbish into every decoder that reads bytes off the wire.

The question is not whether they reject bad input. It is *how* they reject
it. `ValueError` and the project's own decode errors are what the receive
loop catches; `IndexError`, `struct.error`, `OverflowError`,
`ZeroDivisionError` and friends are a viewer that goes down because a grid
sent a packet this client has not seen. The owner's first priority says
"without crashes", and the main grid is exactly where the unseen packet
lives -- so this runs before anyone logs in there, not after.

Seeded, so a failure is reproducible and the suite does not shimmer. The
corpus is random bytes and random bytes behind plausible headers, at the
lengths that break decoders: nothing, one byte, one short of a field, one
past the end of a table.

The floors matter as much as the assertion. A fuzz run in which every
input bounced off a length check would report no failures and mean
nothing, so each decoder has to have *accepted* a minimum number of the
random inputs -- to have run its body, not its guard.
"""

import random
import struct
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: How many of the random inputs each decoder has to have decoded rather
#: than rejected, out of the corpus below. Set well under what a run
#: actually reaches, so the numbers say "this ran" and not "this ran
#: exactly this often".
#: Where a decoder is fed a shortened copy of the input, and why.
#:
#: A 2 kB blob of random bytes is thirty-odd terrain patches, each of which
#: costs a 16x16 inverse cosine transform, and the two terrain decoders
#: between them were four of this file's five seconds. The cases worth
#: fuzzing are at the boundaries -- an empty blob, a header with no body, a
#: patch cut off in the middle -- and truncation makes more of those, not
#: fewer.
MAX_BYTES = {
    "decode_layer_blob": 256,
    "apply_layer_blob": 256,
}

FLOORS = {
    "apply_layer_blob": 6,
    "decode_compressed_object_data": 200,
    "decode_flexible_params": 200,
    "decode_layer_blob": 6,
    "decode_light_params": 200,
    "decode_mesh_flags_params": 200,
    "decode_message_number": 200,
    "decode_parcel_overlay": 100,
    "decode_projection_params": 200,
    "decode_reflection_probe_params": 200,
    "decode_render_materials_params": 200,
    "decode_texture_animation": 200,
    "decode_zerocode": 200,
    "dispatch_message": 10,
    "parse_shape_extra_params": 5,
    "parse_texture_entry": 15,
}


def _corpus(seed: int, count: int):
    rng = random.Random(seed)
    lengths = (0, 1, 2, 3, 4, 7, 8, 15, 16, 31, 64, 128, 255, 512, 2048)
    for _ in range(count):
        size = rng.choice(lengths)
        body = rng.randbytes(size)
        yield body
        yield b"\x00" * rng.randint(0, 4) + body
        yield struct.pack("<B", rng.randint(0, 255)) + body
        yield b"\xff\xff" + struct.pack(">H", rng.randint(0, 65535)) + body
        # Three shapes uniform noise almost never produces, and that any
        # decoder with a marker byte or a run length cares about: a packet
        # whose header flags are set and whose last byte starts a run that
        # has nothing after it, a field of nothing, and a field of all-ones.
        # Dropping zerocode's "marker at the end of the packet" guard passes
        # a run of pure noise and fails on the first of these.
        yield b"\x80" + body + b"\x00"
        yield bytes(size)
        yield b"\xff" * size


def _targets():
    from vibestorm.udp.messages import (
        decode_compressed_object_data,
        parse_shape_extra_params,
    )
    from vibestorm.udp.template import (
        build_template_index,
        decode_message_number,
        dispatch_message,
    )
    from vibestorm.udp.zerocode import decode_zerocode
    from vibestorm.world.extra_params import (
        decode_flexible_params,
        decode_light_params,
        decode_mesh_flags_params,
        decode_projection_params,
        decode_reflection_probe_params,
        decode_render_materials_params,
    )
    from vibestorm.world.parcel_overlay import decode_parcel_overlay
    from vibestorm.world.terrain import RegionHeightmap, decode_layer_blob
    from vibestorm.world.texture_anim import decode_texture_animation
    from vibestorm.world.texture_entry import parse_texture_entry

    index = build_template_index(
        REPO_ROOT / "third_party" / "secondlife" / "message_template.msg"
    )
    return {
        "dispatch_message": lambda b: dispatch_message(b, index),
        "decode_message_number": decode_message_number,
        "decode_zerocode": decode_zerocode,
        "decode_layer_blob": decode_layer_blob,
        "apply_layer_blob": lambda b: RegionHeightmap().apply_layer_blob(b),
        "parse_texture_entry": parse_texture_entry,
        "parse_shape_extra_params": parse_shape_extra_params,
        # Padded to the one length the overlay accepts, or every input is
        # thrown out on the cell count before a byte of it is read.
        "decode_parcel_overlay": lambda b: decode_parcel_overlay(
            [(0, (b * 64)[:4096] if b else b"\x00" * 4096)], region_size_meters=256
        ),
        "decode_compressed_object_data": lambda b: decode_compressed_object_data(
            b, 0, 0, 0xFFFFFFFF
        ),
        "decode_flexible_params": decode_flexible_params,
        "decode_light_params": decode_light_params,
        "decode_projection_params": decode_projection_params,
        "decode_reflection_probe_params": decode_reflection_probe_params,
        "decode_mesh_flags_params": decode_mesh_flags_params,
        "decode_render_materials_params": decode_render_materials_params,
        "decode_texture_animation": decode_texture_animation,
    }


class DecoderFuzzTests(unittest.TestCase):
    SEED = 20260906
    BODIES = 55  # times seven shapes, times sixteen decoders

    @classmethod
    def setUpClass(cls) -> None:
        # Built once: parsing the message template is most of the cost of
        # this file, and neither test mutates it.
        cls.targets = _targets()

    def test_no_decoder_raises_something_the_receive_loop_cannot_catch(self) -> None:
        from vibestorm.world.terrain import TerrainDecodeError
        from vibestorm.world.texture_entry import TextureEntryDecodeError

        allowed = (ValueError, TypeError, KeyError)
        # Both are ValueErrors already; named so a future split still passes.
        allowed += (TerrainDecodeError, TextureEntryDecodeError)

        decoded = dict.fromkeys(self.targets, 0)
        for body in _corpus(self.SEED, self.BODIES):
            for name, decode in self.targets.items():
                try:
                    decode(body[: MAX_BYTES.get(name, len(body))])
                except allowed:
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.fail(
                        f"{name} raised {type(exc).__name__}: {exc}\n"
                        f"  on {body[:64].hex()} ({len(body)} bytes)"
                    )
                decoded[name] += 1

        self.assertEqual(
            [name for name, floor in FLOORS.items() if decoded[name] < floor],
            [],
            f"decoders that mostly refused to run: {decoded}",
        )

    def test_the_floors_name_every_decoder_that_is_swept(self) -> None:
        # Otherwise a decoder added to the sweep with no floor beside it is
        # swept without ever being shown to run.
        self.assertEqual(sorted(FLOORS), sorted(self.targets))


if __name__ == "__main__":
    unittest.main()

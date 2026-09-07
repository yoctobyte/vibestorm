"""Preparing a file the owner made for upload, and refusing the ones we can't.

The trap this module exists to avoid is claiming more than it can do. A
suffix map that accepted `.wav` would create an inventory item with a RIFF
file inside it, typed as a sound, that nothing can play -- and the upload
would report success. "Not supported" is a better answer than an item that
looks right in a folder and is broken when opened.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vibestorm.assets.j2k import decode_j2k  # noqa: E402
from vibestorm.sync.naming import upload_kind_for_path  # noqa: E402
from vibestorm.sync.new_assets import (  # noqa: E402
    ENCODABLE_IMAGE_SUFFIXES,
    PASSTHROUGH_TEXTURE_SUFFIXES,
    NewAssetError,
    new_asset_kind_for_path,
    prepare_new_asset,
)


def _image(fmt: str, size=(100, 100), mode: str = "RGB") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, size, (200, 100, 50) if mode == "RGB" else (1, 2, 3, 4)).save(
        buffer, format=fmt
    )
    return buffer.getvalue()


class KindTests(unittest.TestCase):
    def test_every_image_suffix_maps_to_texture(self) -> None:
        for suffix in ENCODABLE_IMAGE_SUFFIXES | PASSTHROUGH_TEXTURE_SUFFIXES:
            with self.subTest(suffix):
                self.assertEqual(
                    new_asset_kind_for_path(Path(f"a{suffix}")), ("texture", "texture")
                )

    def test_the_case_of_the_suffix_does_not_matter(self) -> None:
        """`.PNG` is what a camera writes and what Windows preserves."""
        self.assertEqual(new_asset_kind_for_path(Path("A.PNG")), ("texture", "texture"))

    def test_formats_this_client_cannot_author_are_refused(self) -> None:
        """Each of these is something a grid stores happily. The refusal is
        about what *this* client can turn into a valid asset."""
        for suffix in (".wav", ".ogg", ".dae", ".bvh", ".anim", ".obj", ""):
            with self.subTest(suffix):
                self.assertIsNone(new_asset_kind_for_path(Path(f"a{suffix}")))

    def test_text_assets_are_not_handled_here(self) -> None:
        """They go through the task-inventory capability instead. A `.lsl`
        answering to both maps would be uploaded twice by a caller that
        checked both, which is the reason the two maps are separate."""
        for suffix in (".lsl", ".txt", ".nc"):
            with self.subTest(suffix):
                self.assertIsNone(new_asset_kind_for_path(Path(f"a{suffix}")))

    def test_the_two_suffix_maps_do_not_overlap(self) -> None:
        """Stated as a test rather than as a comment, because the failure is
        silent: a suffix in both maps is a file two upload paths both think
        they own."""
        for suffix in ENCODABLE_IMAGE_SUFFIXES | PASSTHROUGH_TEXTURE_SUFFIXES:
            with self.subTest(suffix):
                self.assertIsNone(upload_kind_for_path(Path(f"a{suffix}")))


class PrepareTests(unittest.TestCase):
    def test_a_png_becomes_a_jpeg2000_codestream(self) -> None:
        prepared = prepare_new_asset(Path("t.png"), _image("PNG"))
        self.assertEqual(prepared.asset_type, "texture")
        self.assertEqual(prepared.numeric_asset_type, 0)
        self.assertTrue(prepared.re_encoded)
        self.assertEqual(prepared.data[:4], b"\xff\x4f\xff\x51")

    def test_the_encoded_texture_is_readable_by_our_own_decoder(self) -> None:
        """Which is the closest thing to an end-to-end check available
        without a grid: the bytes that go up are the bytes GetTexture would
        hand back, and this client reads those every frame."""
        prepared = prepare_new_asset(Path("t.png"), _image("PNG", (100, 100)))
        decoded = decode_j2k(prepared.data)
        self.assertEqual((decoded.width, decoded.height), (64, 64))

    def test_the_formats_a_paint_program_writes_all_work(self) -> None:
        for fmt, suffix in (("PNG", ".png"), ("JPEG", ".jpg"), ("BMP", ".bmp"), ("TIFF", ".tif")):
            with self.subTest(fmt):
                prepared = prepare_new_asset(Path(f"t{suffix}"), _image(fmt))
                self.assertEqual(decode_j2k(prepared.data).width, 64)

    def test_an_alpha_channel_survives_preparation(self) -> None:
        prepared = prepare_new_asset(Path("t.png"), _image("PNG", (64, 64), "RGBA"))
        self.assertEqual(decode_j2k(prepared.data).mode, "RGBA")

    def test_a_j2k_file_is_passed_through_untouched(self) -> None:
        """A folder pulled out of world writes `.j2k`, so pushing that folder
        back must not re-encode -- a lossy codec applied twice loses twice,
        and the bytes were already the format."""
        original = prepare_new_asset(Path("t.png"), _image("PNG")).data
        prepared = prepare_new_asset(Path("t.j2k"), original)
        self.assertFalse(prepared.re_encoded)
        self.assertEqual(prepared.data, original)

    def test_a_suffix_this_client_will_not_upload_is_a_new_asset_error(self) -> None:
        with self.assertRaisesRegex(NewAssetError, "cannot upload"):
            prepare_new_asset(Path("sound.wav"), b"RIFF....WAVE")

    def test_a_file_that_is_not_an_image_is_a_new_asset_error(self) -> None:
        """Named `.png` and isn't -- a rename, a truncated download. One
        error class per file, so a caller walking a folder does not have to
        know which format each one was."""
        with self.assertRaisesRegex(NewAssetError, "t.png"):
            prepare_new_asset(Path("t.png"), b"not an image")

    def test_the_error_names_the_file(self) -> None:
        """A folder push reports per file, and "encode failed" without a name
        is a message the owner cannot act on."""
        try:
            prepare_new_asset(Path("holiday snap.png"), b"nope")
        except NewAssetError as exc:
            self.assertIn("holiday snap.png", str(exc))
        else:
            self.fail("expected NewAssetError")


if __name__ == "__main__":
    unittest.main()

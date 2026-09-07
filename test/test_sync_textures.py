"""Getting a texture into an object: what goes up, and what is asked of it.

There is no way to create a task-inventory row from nothing --
`UpdateTaskInventory` rejects a zero item id before doing anything else -- so
this is two hops, and the tests are mostly about hop one saying the right
things. A texture uploaded with the wrong `inventory_type` still creates an
item and still reports success; it is just the wrong kind of item, in the
owner's inventory, discovered later.
"""

from __future__ import annotations

import asyncio
import io
import sys
import unittest
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vibestorm.caps.asset_upload_client import AssetUploadResult  # noqa: E402
from vibestorm.sync import textures as module  # noqa: E402
from vibestorm.sync.new_assets import NewAssetError  # noqa: E402
from vibestorm.sync.textures import (  # noqa: E402
    INVENTORY_TEXTURE,
    TextureUploadError,
    create_task_texture,
    upload_agent_texture,
)

FOLDER = UUID("11111111-1111-1111-1111-111111111111")
URL = "http://example.invalid/caps/new-file"


def _png(size=(100, 100), mode: str = "RGB") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, size, (200, 100, 50)).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeUploader:
    """Records the request rather than performing it."""

    def __init__(self, item_id: UUID | None = None, state: str = "complete"):
        self.calls: list[tuple[str, object, bytes]] = []
        self._item_id = item_id if item_id is not None else uuid4()
        self._state = state

    async def upload_new_file(self, url, request, data, *, udp_listen_port=None, **kwargs):
        self.calls.append((url, request, data))
        return AssetUploadResult(
            state=self._state,
            new_asset_id=uuid4(),
            new_inventory_item_id=self._item_id if self._state == "complete" else None,
        )


class UploadAgentTextureTests(unittest.TestCase):
    def _upload(self, path: Path, data: bytes, uploader=None, **kwargs):
        uploader = uploader if uploader is not None else _FakeUploader()
        result = asyncio.run(
            upload_agent_texture(
                path, data, folder_id=FOLDER, upload_url=URL, client=uploader, **kwargs
            )
        )
        return result, uploader

    def test_the_bytes_sent_are_a_jpeg2000_codestream(self) -> None:
        """Not the PNG. The whole point of hop one is that the asset stored is
        the format a grid serves back."""
        _result, uploader = self._upload(Path("sunset.png"), _png())
        _url, _request, data = uploader.calls[0]
        self.assertEqual(data[:4], b"\xff\x4f\xff\x51")

    def test_both_type_strings_say_texture(self) -> None:
        """`asset_type` and `inventory_type` both. OpenSim reads the second
        and leaves both numbers at 0 when no branch matches, which for a
        texture happens to be right -- but the request is what a different
        grid would read, and it should say what it means."""
        _result, uploader = self._upload(Path("sunset.png"), _png())
        request = uploader.calls[0][1]
        self.assertEqual(request.asset_type, "texture")
        self.assertEqual(request.inventory_type, "texture")

    def test_the_item_is_named_after_the_file_without_its_suffix(self) -> None:
        """`sunset.png` in an inventory folder reads as a file. The suffix
        chose the encoder and has nothing left to say."""
        _result, uploader = self._upload(Path("/tmp/holiday/sunset.png"), _png())
        self.assertEqual(uploader.calls[0][1].name, "sunset")

    def test_an_explicit_name_wins(self) -> None:
        _result, uploader = self._upload(Path("sunset.png"), _png(), name="Sunset Over Water")
        self.assertEqual(uploader.calls[0][1].name, "Sunset Over Water")

    def test_the_upload_goes_to_the_folder_it_was_given(self) -> None:
        _result, uploader = self._upload(Path("sunset.png"), _png())
        self.assertEqual(uploader.calls[0][1].folder_id, FOLDER)

    def test_the_result_reports_the_encoded_size_not_the_file_size(self) -> None:
        """A folder push prints this, and the file's size would be a
        different number for the same upload."""
        png = _png()
        result, uploader = self._upload(Path("sunset.png"), png)
        self.assertEqual(result.bytes_sent, len(uploader.calls[0][2]))
        self.assertNotEqual(result.bytes_sent, len(png))
        self.assertTrue(result.re_encoded)

    def test_a_jpeg2000_file_is_sent_unchanged(self) -> None:
        """A folder pulled out of world writes `.j2k`. Pushing it back must
        not re-encode: a lossy codec applied twice loses twice."""
        _first, uploader = self._upload(Path("sunset.png"), _png())
        already = uploader.calls[0][2]
        result, second = self._upload(Path("sunset.j2k"), already)
        self.assertEqual(second.calls[0][2], already)
        self.assertFalse(result.re_encoded)

    def test_an_upload_that_creates_no_item_is_an_error(self) -> None:
        """The capability answered, so this is not a transport failure -- an
        insufficient-funds refusal reads exactly like this on a grid that
        charges for uploads. Returning a result with a None item id would put
        that None into `UpdateTaskInventory`, which rejects it with no
        explanation of why."""
        uploader = _FakeUploader(state="error")
        with self.assertRaisesRegex(TextureUploadError, "sunset"):
            self._upload(Path("sunset.png"), _png(), uploader=uploader)

    def test_a_file_this_client_cannot_encode_never_reaches_the_grid(self) -> None:
        uploader = _FakeUploader()
        with self.assertRaises(NewAssetError):
            self._upload(Path("sound.wav"), b"RIFF....WAVE", uploader=uploader)
        self.assertEqual(uploader.calls, [])


class CreateTaskTextureTests(unittest.TestCase):
    """Hop two, which is shared with notecards and only needs the right
    numbers handed to it."""

    def setUp(self) -> None:
        self.copies: list[dict] = []
        self._original = module.copy_item_into_object

        async def fake_copy(world, session, **kwargs):
            self.copies.append(kwargs)
            return (uuid4(), kwargs["name"])

        module.copy_item_into_object = fake_copy

    def tearDown(self) -> None:
        module.copy_item_into_object = self._original

    def _create(self, path: Path, data: bytes, **kwargs):
        return asyncio.run(
            create_task_texture(
                object(),
                object(),
                handle=1,
                local_id=42,
                folder_id=FOLDER,
                upload_url=URL,
                path=path,
                data=data,
                upload_client=_FakeUploader(),
                **kwargs,
            )
        )

    def test_the_copy_is_told_the_item_is_a_texture(self) -> None:
        """Zero for both. `UpdateTaskInventory` writes what it is told into
        the row, so a wrong number here is a texture that lands in the object
        typed as something else and is not drawn."""
        self._create(Path("sunset.png"), _png())
        self.assertEqual(self.copies[0]["asset_type"], INVENTORY_TEXTURE)
        self.assertEqual(self.copies[0]["inv_type"], INVENTORY_TEXTURE)
        self.assertEqual(INVENTORY_TEXTURE, 0)

    def test_the_copy_targets_the_prim_it_was_given(self) -> None:
        self._create(Path("sunset.png"), _png())
        self.assertEqual(self.copies[0]["local_id"], 42)

    def test_the_name_carries_through_both_hops(self) -> None:
        self._create(Path("sunset.png"), _png(), name="Sunset")
        self.assertEqual(self.copies[0]["name"], "Sunset")

    def test_the_name_the_object_chose_is_what_comes_back(self) -> None:
        """An object already holding `sunset` receives the copy as
        `sunset 1`, and the caller has to record that name rather than the
        one it asked for."""
        module.copy_item_into_object = self._renaming_copy
        result = self._create(Path("sunset.png"), _png())
        assert result is not None
        self.assertEqual(result[1], "sunset 1")

    @staticmethod
    async def _renaming_copy(world, session, **kwargs):
        return (uuid4(), kwargs["name"] + " 1")

    def test_a_failed_upload_never_reaches_the_copy(self) -> None:
        with self.assertRaises(NewAssetError):
            self._create(Path("sound.wav"), b"RIFF")
        self.assertEqual(self.copies, [])


if __name__ == "__main__":
    unittest.main()

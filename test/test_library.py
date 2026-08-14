"""Tests for the grid library constants.

Both ids are sourced, and both are the kind that fail quietly if wrong: an
inventory fetch with the wrong root or the wrong owner returns an empty tree,
not an error. So the pin tests matter more here than the usual.
"""

import re
import unittest
from pathlib import Path

from vibestorm.caps.library import LIBRARY_OWNER_ID, LIBRARY_ROOT_FOLDER_ID

_ROOT = Path(__file__).resolve().parents[1]
_LIBRARY_SERVICE = (
    _ROOT / "opensim-source" / "OpenSim" / "Services" / "InventoryService"
    / "LibraryService.cs"
)
_ASSET_LOADER = (
    _ROOT / "opensim-source" / "OpenSim" / "Framework" / "AssetLoader"
    / "Filesystem" / "AssetLoaderFileSystem.cs"
)
_HANDLER = (
    _ROOT / "opensim-source" / "OpenSim" / "Capabilities" / "Handlers"
    / "FetchInventory" / "FetchLibDescHandler.cs"
)


class SourcePinTests(unittest.TestCase):
    def test_the_root_folder_id_matches_library_service(self) -> None:
        if not _LIBRARY_SERVICE.exists():
            self.skipTest("opensim-source not present")
        text = _LIBRARY_SERVICE.read_text(encoding="utf-8", errors="replace")

        match = re.search(
            r'm_LibraryRootFolderIDstr\s*=\s*"([0-9a-f-]+)"', text
        )

        self.assertIsNotNone(match, "failed to find the library root folder id")
        self.assertEqual(str(LIBRARY_ROOT_FOLDER_ID), match.group(1))

    def test_the_owner_id_matches_the_asset_loader(self) -> None:
        if not _ASSET_LOADER.exists():
            self.skipTest("opensim-source not present")
        text = _ASSET_LOADER.read_text(encoding="utf-8", errors="replace")

        match = re.search(r'LIBRARY_OWNER_IDstr\s*=\s*"([0-9a-f-]+)"', text)

        self.assertIsNotNone(match, "failed to find the library owner id")
        self.assertEqual(str(LIBRARY_OWNER_ID), match.group(1))

    def test_the_two_ids_are_different(self) -> None:
        # They share a suffix and differ only in the first two groups, which is
        # exactly the shape of mistake a copy-paste makes.
        self.assertNotEqual(LIBRARY_ROOT_FOLDER_ID, LIBRARY_OWNER_ID)

    def test_the_handler_really_does_check_the_owner(self) -> None:
        # The reason the owner id is not simply our agent id. If this check
        # ever goes away the constant is harmless, but the comment explaining
        # it would become wrong.
        if not _HANDLER.exists():
            self.skipTest("opensim-source not present")
        text = _HANDLER.read_text(encoding="utf-8", errors="replace")

        self.assertRegex(text, r"owner_id\s*\.\s*Equals\s*\(\s*libOwner\s*\)")


if __name__ == "__main__":
    unittest.main()

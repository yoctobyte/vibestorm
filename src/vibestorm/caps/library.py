"""The grid library: a second inventory tree every account can read.

OpenSim ships a read-only library of sounds, animations, gestures, landmarks,
textures and body parts, served through ``FetchLibDescendents2`` and
``FetchLib2`` — the same LLSD shapes as the personal-inventory capabilities,
against a different root and a different owner.

That makes it the answer to a recurring problem. A long list of decoders in
this client are "blocked on region content": no sound emitter, no animation, no
real mesh. The library holds real assets of several of those types on every
OpenSim install, so a client that can read it can exercise the sound and
animation paths without anyone building anything in-world.

Both ids below are sourced. The handler is not lenient about the owner:
``FetchLibDescHandler`` compares the requested ``owner_id`` against the library
owner and treats a mismatch as a different tree, so passing our own agent id —
the obvious guess — quietly returns nothing rather than failing.
"""

from __future__ import annotations

from uuid import UUID

#: ``LibraryService.m_LibraryRootFolderIDstr``.
LIBRARY_ROOT_FOLDER_ID = UUID("00000112-000f-0000-0000-000100bba000")

#: ``AssetLoaderFileSystem.LIBRARY_OWNER_IDstr``, also OpenSim's
#: ``Constants.m_MrOpenSimIDString``. Every library folder and item is owned by
#: it, and ``FetchLibDescHandler`` checks for exactly this value.
LIBRARY_OWNER_ID = UUID("11111111-1111-0000-0000-000100bba000")

__all__ = ["LIBRARY_OWNER_ID", "LIBRARY_ROOT_FOLDER_ID"]

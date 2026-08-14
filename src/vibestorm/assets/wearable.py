"""Decoder for wearable assets — clothing (type 5) and body parts (type 13).

Both use the same ``LLWearable`` text format: three header lines (magic and
version, name, description), then whitespace-separated ``key value`` lines,
two of which introduce counted lists.

The layout is sourced from ``UuidGatherer.RecordWearableAssetUuids`` in
``Region/Framework/Scenes/UuidGatherer.cs``, which walks the same file to
collect the texture ids a wearable references. It pins the parts that are not
self-evident from a sample: exactly how many lines precede the key/value body,
that keys and values are split on spaces *or* tabs, that ``parameters`` and
``textures`` are each followed by a count and then that many lines, and that a
texture line is ``index uuid``.

Everything else the file describes itself. Counts precede their lists and every
key is named, so the structure needs no reconstruction — but the *meanings* of
the numbers mostly do, and those are not in this tree:

* ``type`` is sourceable. ``AvatarWearable.cs`` names all seventeen values, and
  they are reproduced in `WEARABLE_TYPE_NAMES`.
* Visual parameter ids are **not**. ``AvatarAppearance.VPElement`` looks like
  the table for this and is not: it is a 0-based *index* into the
  ``AgentSetAppearance`` VisualParams array, running 0..252 and generated from
  libomv's list, whereas a wearable file's ids are sparse and range past 1000
  (``Hair`` alone carries 16, 31, 112 and 1012). Where the two numbering
  schemes happen to collide is not evidence they agree, and the mapping
  between them lives in libomv, which ships in ``opensim-source/bin/`` as a
  DLL only. So parameters are reported as ``{id: value}`` with no names.
* Texture slot indices are not named either. ``AvatarAppearance`` pins only the
  count (``TEXTURE_COUNT = 45``) and which slots are bakes
  (``BAKE_INDICES``); it never names a slot. They are reported as raw indices,
  with `is_bake_slot` available where that one distinction is wanted.

Unrecognised ``key value`` lines are kept in `extra` rather than dropped, so a
wearable carrying something this decoder does not model says so instead of
quietly losing it.

Verified against real assets: the OpenSim library's ``Shirt`` (563 bytes, type
4, 10 parameters, 1 texture) and ``Hair`` (1093 bytes, type 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

#: What the first line starts with.
MAGIC = "LLWearable"

#: Lines consumed before the key/value body: the magic/version line, the name,
#: the description, and one more — `RecordWearableAssetUuids` skips four before
#: its read loop, and in practice the fourth is `permissions <n>`.
HEADER_LINE_COUNT = 4

#: Wearable types, from `AvatarWearable.cs`. Note it carries its own caveat:
#: "http://wiki.secondlife.com/wiki/Avatar_Appearance. We'll correct them over
#: time for when were are wrong."
WEARABLE_TYPE_NAMES = {
    0: "body",
    1: "skin",
    2: "hair",
    3: "eyes",
    4: "shirt",
    5: "pants",
    6: "shoes",
    7: "socks",
    8: "jacket",
    9: "gloves",
    10: "undershirt",
    11: "underpants",
    12: "skirt",
    13: "alpha",
    14: "tattoo",
    15: "physics",
    16: "universal",
}

#: `AvatarAppearance.BAKE_INDICES` — the only thing this tree says about what a
#: texture slot number means.
BAKE_TEXTURE_INDICES = frozenset({8, 9, 10, 11, 19, 20, 40, 41, 42, 43, 44})

#: `AvatarAppearance.TEXTURE_COUNT`.
TEXTURE_SLOT_COUNT = 45


class WearableDecodeError(ValueError):
    """Raised when wearable asset bytes cannot be decoded."""


@dataclass(slots=True, frozen=True)
class Wearable:
    version: int
    name: str
    description: str
    #: The `AvatarWearable` type number, or None if the file carried no `type`
    #: line. Not defaulted to 0, which is a real type (body).
    wearable_type: int | None
    #: Visual parameter id to value, as written. Ids are not resolvable to
    #: names from this tree; see the module docstring.
    parameters: dict[int, str] = field(default_factory=dict)
    #: Texture slot index to asset id.
    textures: dict[int, UUID] = field(default_factory=dict)
    #: Key/value lines this decoder does not model — permissions, sale info,
    #: and anything newer. Kept so nothing is silently lost.
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def type_name(self) -> str:
        if self.wearable_type is None:
            return "untyped"
        return WEARABLE_TYPE_NAMES.get(self.wearable_type, f"type{self.wearable_type}")

    def is_bake_slot(self, index: int) -> bool:
        return index in BAKE_TEXTURE_INDICES

    def describe(self) -> str:
        parts = [
            f"{self.type_name}",
            f"name={self.name or '(unnamed)'}",
            f"params={len(self.parameters)}",
            f"textures={len(self.textures)}",
        ]
        if self.extra:
            parts.append(f"extra={len(self.extra)}")
        return " ".join(parts)


def _split(line: str) -> list[str]:
    return line.replace("\t", " ").split()


def decode_wearable(data: bytes) -> Wearable:
    """Decode an ``LLWearable`` asset.

    Raises when a declared count runs past the end of the file. A short read
    there would present a wearable with fewer parameters or textures than it
    has, which renders as a different-looking avatar rather than as a failure.
    """
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if not lines or not lines[0].startswith(MAGIC):
        raise WearableDecodeError(
            f"wearable asset does not start with {MAGIC!r}: {lines[0][:40]!r}"
            if lines
            else "wearable asset is empty"
        )
    if len(lines) <= HEADER_LINE_COUNT:
        raise WearableDecodeError(
            f"wearable asset has {len(lines)} lines; the header alone needs "
            f"{HEADER_LINE_COUNT}"
        )

    header = _split(lines[0])
    if len(header) < 3 or header[1] != "version":
        raise WearableDecodeError(f"wearable header is malformed: {lines[0]!r}")
    try:
        version = int(header[2])
    except ValueError as exc:
        raise WearableDecodeError(
            f"wearable version is not a number: {header[2]!r}"
        ) from exc

    name = lines[1].strip()
    description = lines[2].strip()

    wearable_type: int | None = None
    parameters: dict[int, str] = {}
    textures: dict[int, UUID] = {}
    extra: dict[str, str] = {}

    cursor = HEADER_LINE_COUNT
    while cursor < len(lines):
        parts = _split(lines[cursor])
        cursor += 1
        if not parts:
            continue
        key = parts[0]
        value = parts[1] if len(parts) > 1 else ""

        if key == "parameters":
            count = _count(value, "parameters", lines[cursor - 1])
            if cursor + count > len(lines):
                raise WearableDecodeError(
                    f"wearable declares {count} parameters but only "
                    f"{len(lines) - cursor} lines remain"
                )
            for _ in range(count):
                entry = _split(lines[cursor])
                cursor += 1
                if len(entry) < 2:
                    continue
                try:
                    parameters[int(entry[0])] = entry[1]
                except ValueError:
                    continue
        elif key == "textures":
            count = _count(value, "textures", lines[cursor - 1])
            if cursor + count > len(lines):
                raise WearableDecodeError(
                    f"wearable declares {count} textures but only "
                    f"{len(lines) - cursor} lines remain"
                )
            for _ in range(count):
                entry = _split(lines[cursor])
                cursor += 1
                if len(entry) < 2:
                    continue
                try:
                    textures[int(entry[0])] = UUID(entry[1])
                except ValueError:
                    continue
        elif key == "type":
            try:
                wearable_type = int(value)
            except ValueError as exc:
                raise WearableDecodeError(
                    f"wearable type is not a number: {value!r}"
                ) from exc
        elif key in ("{", "}"):
            continue
        else:
            extra[key] = value

    return Wearable(
        version=version,
        name=name,
        description=description,
        wearable_type=wearable_type,
        parameters=parameters,
        textures=textures,
        extra=extra,
    )


def _count(value: str, field_name: str, line: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise WearableDecodeError(
            f"wearable {field_name} count is not a number: {line!r}"
        ) from exc
    if count < 0:
        raise WearableDecodeError(f"wearable declares {count} {field_name}")
    return count


__all__ = [
    "BAKE_TEXTURE_INDICES",
    "HEADER_LINE_COUNT",
    "MAGIC",
    "TEXTURE_SLOT_COUNT",
    "WEARABLE_TYPE_NAMES",
    "Wearable",
    "WearableDecodeError",
    "decode_wearable",
]

"""Decoder for gesture assets.

A gesture is a line-oriented text asset: five header lines, a step count, then
that many steps, each of which is a type line followed by a type-specific
number of lines.

The layout is sourced from ``UuidGatherer.RecordGestureAssetUuids`` in
``Region/Framework/Scenes/UuidGatherer.cs``, which walks the same file to
collect the animation and sound ids a gesture references. That walker is the
whole specification available in this tree, and it is enough: it names every
header line it skips (``version``, ``key``, ``mask``, ``trigger``,
``replace``), reads the step count, switches on the step type, and names each
step's fields (``name``, ``uuid``, ``flags`` for animation and sound; a single
value plus ``flags`` for chat and wait).

The four step types are pinned by that switch — ``0`` animation, ``1`` sound,
``2`` chat, ``3`` wait — and its ``default: return; // no idea`` is the reason
this decoder stops at an unrecognised type rather than guessing a field count.
Once the type is unknown the *length* of that step is unknown too, so every
line after it would be misaligned; carrying on would produce steps that look
real and are not.

What is deliberately not decoded: the meaning of ``key``, ``mask`` and the
per-step ``flags``. The walker skips all three without comment, so nothing here
can say what their bits mean. They are reported as raw integers.

Verified against a real asset: the OpenSim library's ``can_we_move_along``,
67 bytes, one animation step triggered by ``/bored``.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

#: The step types `RecordGestureAssetUuids` switches on. Anything else hits its
#: `default: return; // no idea`.
STEP_ANIMATION = 0
STEP_SOUND = 1
STEP_CHAT = 2
STEP_WAIT = 3

_STEP_TYPE_NAMES = {
    STEP_ANIMATION: "animation",
    STEP_SOUND: "sound",
    STEP_CHAT: "chat",
    STEP_WAIT: "wait",
}

#: Lines before the step count: version, key, mask, trigger, replace.
HEADER_LINE_COUNT = 5


class GestureDecodeError(ValueError):
    """Raised when gesture asset bytes cannot be decoded."""


@dataclass(slots=True, frozen=True)
class GestureStep:
    step_type: int
    #: The step's label — an animation or sound name. Empty for chat and wait,
    #: which carry their payload in `value` instead.
    name: str = ""
    #: The referenced asset, for animation and sound steps only.
    asset_id: UUID | None = None
    #: Chat text, or the wait time as written. Kept as text because the source
    #: only ever skips this line, so its type is not pinned for either.
    value: str = ""
    #: Reported rather than interpreted; the source skips it.
    flags: int = 0

    @property
    def type_name(self) -> str:
        return _STEP_TYPE_NAMES.get(self.step_type, f"type{self.step_type}")

    def describe(self) -> str:
        if self.asset_id is not None:
            return f"{self.type_name}:{self.name or self.asset_id}"
        if self.value:
            return f"{self.type_name}:{self.value}"
        return self.type_name


@dataclass(slots=True, frozen=True)
class Gesture:
    version: int
    #: The keyboard trigger's key and modifier mask. Skipped by the source, so
    #: reported as written.
    key: int
    mask: int
    #: The chat command that fires the gesture, e.g. ``/bored``.
    trigger: str
    #: What the trigger text is replaced with in chat. Commonly empty.
    replace_with: str
    steps: tuple[GestureStep, ...]

    @property
    def asset_ids(self) -> tuple[UUID, ...]:
        """Every asset this gesture plays — what OpenSim reads the file for."""
        return tuple(step.asset_id for step in self.steps if step.asset_id is not None)

    def describe(self) -> str:
        parts = [f"trigger={self.trigger or '(none)'}", f"steps={len(self.steps)}"]
        if self.replace_with:
            parts.append(f"replace={self.replace_with}")
        if self.steps:
            parts.append("[" + " ".join(step.describe() for step in self.steps) + "]")
        return " ".join(parts)


def _int(line: str, field: str) -> int:
    try:
        return int(line.strip())
    except ValueError as exc:
        raise GestureDecodeError(f"gesture {field} is not a number: {line!r}") from exc


def decode_gesture(data: bytes) -> Gesture:
    """Decode a gesture asset.

    Raises rather than returning the steps read so far. A gesture truncated
    mid-step reads as a shorter gesture, which is a specific wrong claim about
    what the avatar does rather than an obvious failure.
    """
    # The final newline terminates the last line rather than starting an empty
    # one. Keeping it would leave a phantom line for a truncated step to read,
    # turning "the file ends here" into "this field is blank".
    text = data.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        text = text[:-1]
    lines = text.split("\n")
    if len(lines) <= HEADER_LINE_COUNT:
        raise GestureDecodeError(
            f"gesture asset has {len(lines)} lines; the header alone needs "
            f"{HEADER_LINE_COUNT} plus a step count"
        )

    version = _int(lines[0], "version")
    key = _int(lines[1], "key")
    mask = _int(lines[2], "mask")
    trigger = lines[3].strip()
    replace_with = lines[4].strip()

    cursor = HEADER_LINE_COUNT
    step_count = _int(lines[cursor], "step count")
    cursor += 1
    if step_count < 0:
        raise GestureDecodeError(f"gesture declares {step_count} steps")

    def take(field: str) -> str:
        nonlocal cursor
        if cursor >= len(lines):
            raise GestureDecodeError(
                f"gesture asset truncated: step {field} line is missing"
            )
        line = lines[cursor]
        cursor += 1
        return line

    steps: list[GestureStep] = []
    for index in range(step_count):
        step_type = _int(take("type"), f"step {index} type")
        if step_type in (STEP_ANIMATION, STEP_SOUND):
            name = take("name").strip()
            raw_id = take("uuid").strip()
            try:
                asset_id = UUID(raw_id)
            except ValueError as exc:
                raise GestureDecodeError(
                    f"gesture step {index} has an unreadable asset id: {raw_id!r}"
                ) from exc
            flags = _int(take("flags"), f"step {index} flags")
            steps.append(
                GestureStep(
                    step_type=step_type, name=name, asset_id=asset_id, flags=flags
                )
            )
        elif step_type in (STEP_CHAT, STEP_WAIT):
            value = take("value").strip()
            flags = _int(take("flags"), f"step {index} flags")
            steps.append(GestureStep(step_type=step_type, value=value, flags=flags))
        else:
            # OpenSim's walker gives up here too, and for the same reason: an
            # unknown type has an unknown field count, so every following line
            # is unaligned. Stopping is the only honest option.
            raise GestureDecodeError(
                f"gesture step {index} has unknown type {step_type}; its field "
                f"count is unknown, so the rest of the asset cannot be read"
            )

    return Gesture(
        version=version,
        key=key,
        mask=mask,
        trigger=trigger,
        replace_with=replace_with,
        steps=tuple(steps),
    )


__all__ = [
    "Gesture",
    "GestureDecodeError",
    "GestureStep",
    "HEADER_LINE_COUNT",
    "STEP_ANIMATION",
    "STEP_CHAT",
    "STEP_SOUND",
    "STEP_WAIT",
    "decode_gesture",
]

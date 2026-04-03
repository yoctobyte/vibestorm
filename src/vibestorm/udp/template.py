"""Message template summary parsing and message-number dispatch support."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from struct import unpack_from

MESSAGE_NUMBER_BYTES = {
    "High": 1,
    "Medium": 2,
    "Low": 4,
    "Fixed": 4,
}


@dataclass(slots=True, frozen=True)
class MessageTemplateSummary:
    name: str
    frequency: str
    message_number: int
    trust: str
    encoding: str
    deprecation: str | None

    @property
    def message_number_bytes(self) -> int:
        return MESSAGE_NUMBER_BYTES[self.frequency]

    @property
    def wire_message_number(self) -> int:
        if self.frequency == "High":
            return self.message_number
        if self.frequency == "Medium":
            return 0x0000FF00 | self.message_number
        if self.frequency == "Low":
            return 0xFFFF0000 | self.message_number
        return self.message_number


@dataclass(slots=True, frozen=True)
class DecodedMessageNumber:
    frequency: str
    message_number: int
    encoded_length: int


@dataclass(slots=True, frozen=True)
class MessageTemplateIndex:
    by_name: dict[str, MessageTemplateSummary]
    by_number: dict[int, MessageTemplateSummary]


@dataclass(slots=True, frozen=True)
class MessageDispatch:
    summary: MessageTemplateSummary
    message_number: DecodedMessageNumber
    body: bytes


def template_path(root: Path) -> Path:
    return root / "third_party" / "secondlife" / "message_template.msg"


def load_template_summaries(path: Path) -> dict[str, MessageTemplateSummary]:
    """Load top-level message metadata from `message_template.msg`."""
    summaries: dict[str, MessageTemplateSummary] = {}
    brace_depth = 0
    pending_message = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue

        if line == "{":
            brace_depth += 1
            if brace_depth == 1:
                pending_message = True
            continue

        if line == "}":
            brace_depth -= 1
            if brace_depth < 0:
                raise ValueError("unbalanced closing brace while parsing message template")
            continue

        if pending_message and brace_depth == 1:
            parts = line.split()
            if len(parts) < 5:
                raise ValueError(f"invalid message declaration: {line}")

            name = parts[0]
            frequency = parts[1]
            message_number = int(parts[2], 0)
            trust = parts[3]
            encoding = parts[4]
            deprecation = parts[5] if len(parts) > 5 else None
            summaries[name] = MessageTemplateSummary(
                name=name,
                frequency=frequency,
                message_number=message_number,
                trust=trust,
                encoding=encoding,
                deprecation=deprecation,
            )
            pending_message = False

    if brace_depth != 0:
        raise ValueError("unbalanced braces while parsing message template")

    return summaries


def build_template_index(path: Path) -> MessageTemplateIndex:
    summaries = load_template_summaries(path)
    return MessageTemplateIndex(
        by_name=summaries,
        by_number={summary.wire_message_number: summary for summary in summaries.values()},
    )


def decode_message_number(data: bytes) -> DecodedMessageNumber:
    """Decode the variable-length message number prefix from packet payload bytes."""
    if not data:
        raise ValueError("message payload is empty")

    first = data[0]
    if first != 0xFF:
        return DecodedMessageNumber(
            frequency="High",
            message_number=first,
            encoded_length=1,
        )

    if len(data) < 2:
        raise ValueError("medium/low/fixed message number is truncated")

    second = data[1]
    if second != 0xFF:
        return DecodedMessageNumber(
            frequency="Medium",
            message_number=(first << 8) | second,
            encoded_length=2,
        )

    if len(data) < 4:
        raise ValueError("low/fixed message number is truncated")

    message_number = unpack_from(">I", data, 0)[0]
    frequency = "Fixed" if message_number >= 0xFFFFFFFA else "Low"
    return DecodedMessageNumber(
        frequency=frequency,
        message_number=message_number,
        encoded_length=4,
    )


def dispatch_message(payload: bytes, index: MessageTemplateIndex) -> MessageDispatch:
    """Resolve a raw packet payload to a message template summary and remaining body."""
    decoded = decode_message_number(payload)
    try:
        summary = index.by_number[decoded.message_number]
    except KeyError as exc:
        raise KeyError(f"unknown message number 0x{decoded.message_number:08X}") from exc

    body = payload[decoded.encoded_length :]
    return MessageDispatch(summary=summary, message_number=decoded, body=body)

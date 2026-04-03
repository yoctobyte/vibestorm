"""Packet header parsing for Second Life UDP transport."""

from __future__ import annotations

from dataclasses import dataclass
from struct import pack, unpack_from

LL_ZERO_CODE_FLAG = 0x80
LL_RELIABLE_FLAG = 0x40
LL_RESENT_FLAG = 0x20
LL_ACK_FLAG = 0x10

PACKET_HEADER_SIZE = 6
MINIMUM_VALID_PACKET_SIZE = PACKET_HEADER_SIZE + 1
ACK_SIZE = 4


class PacketError(ValueError):
    """Raised when a UDP packet is malformed."""


@dataclass(slots=True, frozen=True)
class PacketHeader:
    flags: int
    sequence: int
    extra_header_length: int

    @property
    def is_zero_coded(self) -> bool:
        return bool(self.flags & LL_ZERO_CODE_FLAG)

    @property
    def is_reliable(self) -> bool:
        return bool(self.flags & LL_RELIABLE_FLAG)

    @property
    def is_resent(self) -> bool:
        return bool(self.flags & LL_RESENT_FLAG)

    @property
    def has_acks(self) -> bool:
        return bool(self.flags & LL_ACK_FLAG)

    @property
    def message_offset(self) -> int:
        return PACKET_HEADER_SIZE + self.extra_header_length


@dataclass(slots=True, frozen=True)
class PacketView:
    header: PacketHeader
    message: bytes
    appended_acks: tuple[int, ...]


def parse_packet_header(data: bytes) -> PacketHeader:
    """Parse the fixed packet header."""
    if len(data) < MINIMUM_VALID_PACKET_SIZE:
        raise PacketError(
            f"packet too short: expected at least {MINIMUM_VALID_PACKET_SIZE} bytes, got {len(data)}",
        )

    flags = data[0]
    sequence = unpack_from(">I", data, 1)[0]
    extra_header_length = data[5]
    message_offset = PACKET_HEADER_SIZE + extra_header_length
    if message_offset >= len(data):
        raise PacketError(
            "packet extra header length points beyond available message payload",
        )

    return PacketHeader(
        flags=flags,
        sequence=sequence,
        extra_header_length=extra_header_length,
    )


def split_packet(data: bytes) -> PacketView:
    """Split a packet into header, message payload, and appended ACK trailer."""
    header = parse_packet_header(data)
    payload_end = len(data)
    appended_acks: tuple[int, ...] = ()

    if header.has_acks:
        ack_count = data[-1]
        ack_bytes = ack_count * ACK_SIZE
        trailer_size = ack_bytes + 1
        if len(data) - trailer_size < MINIMUM_VALID_PACKET_SIZE:
            raise PacketError(
                "packet ACK trailer is malformed or overlaps the message payload",
            )

        payload_end -= trailer_size
        start = payload_end
        acks = []
        for offset in range(start, start + ack_bytes, ACK_SIZE):
            acks.append(unpack_from(">I", data, offset)[0])
        appended_acks = tuple(acks)

    message = data[header.message_offset:payload_end]
    if not message:
        raise PacketError("packet does not contain a message payload")

    return PacketView(header=header, message=message, appended_acks=appended_acks)


def build_packet(
    message: bytes,
    sequence: int,
    *,
    flags: int = 0,
    extra_header: bytes = b"",
    appended_acks: tuple[int, ...] = (),
) -> bytes:
    """Build a raw UDP packet."""
    if not 0 <= sequence <= 0xFFFFFFFF:
        raise PacketError("sequence must fit in U32")
    if len(extra_header) > 0xFF:
        raise PacketError("extra header must fit in U8 length")

    packet = bytearray()
    packet.append(flags | (LL_ACK_FLAG if appended_acks else 0))
    packet.extend(pack(">I", sequence))
    packet.append(len(extra_header))
    packet.extend(extra_header)
    packet.extend(message)
    for ack in appended_acks:
        packet.extend(pack(">I", ack))
    if appended_acks:
        packet.append(len(appended_acks))
    return bytes(packet)

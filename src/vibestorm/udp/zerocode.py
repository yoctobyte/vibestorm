"""Zerocode expansion for Second Life UDP packets."""

from vibestorm.udp.packet import LL_ZERO_CODE_FLAG, MINIMUM_VALID_PACKET_SIZE, PACKET_HEADER_SIZE


class ZerocodeError(ValueError):
    """Raised when zerocode payloads are malformed."""


def encode_zerocode(data: bytes) -> bytes:
    """Compress a packet payload using Second Life zerocode rules."""
    if len(data) < MINIMUM_VALID_PACKET_SIZE:
        raise ZerocodeError(
            f"packet too short for zerocode encode: expected at least {MINIMUM_VALID_PACKET_SIZE} bytes",
        )

    if data[0] & LL_ZERO_CODE_FLAG:
        return data

    output = bytearray(data[:PACKET_HEADER_SIZE])
    output[0] |= LL_ZERO_CODE_FLAG

    zero_run_length = 0
    for value in data[PACKET_HEADER_SIZE:]:
        if value == 0:
            zero_run_length += 1
            if zero_run_length == 255:
                output.extend((0, 255))
                zero_run_length = 0
            continue

        if zero_run_length:
            output.extend((0, zero_run_length))
            zero_run_length = 0
        output.append(value)

    if zero_run_length:
        output.extend((0, zero_run_length))

    return bytes(output)


def decode_zerocode(data: bytes) -> bytes:
    """Expand a zero-coded packet.

    The first 6 header bytes are copied through unchanged except that the
    zerocode flag is cleared in the returned buffer.
    """
    if len(data) < MINIMUM_VALID_PACKET_SIZE:
        raise ZerocodeError(
            f"packet too short for zerocode decode: expected at least {MINIMUM_VALID_PACKET_SIZE} bytes",
        )

    if not (data[0] & LL_ZERO_CODE_FLAG):
        return data

    output = bytearray(data[:PACKET_HEADER_SIZE])
    output[0] &= ~LL_ZERO_CODE_FLAG

    index = PACKET_HEADER_SIZE
    limit = len(data)

    while index < limit:
        value = data[index]
        output.append(value)
        index += 1

        if value != 0:
            continue

        while index < limit and data[index] == 0:
            output.append(0)
            output.extend(b"\x00" * 255)
            index += 1

        if index >= limit:
            raise ZerocodeError("zerocode marker at end of packet is missing its count byte")

        zero_run_length = data[index]
        output.extend(b"\x00" * (zero_run_length - 1))
        index += 1

    return bytes(output)

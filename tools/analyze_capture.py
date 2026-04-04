#!/usr/bin/env python3
"""Analyze a pcap file for OpenSim/SL protocol content.

Usage:
    python tools/analyze_capture.py <file.pcap> [--extract-bakes DIR]

Outputs:
  - UDP packet census (src:port → dst:port, count)
  - HTTP request/response timeline (TCP streams, port 9000)
  - CAP name → URL table from seed cap LLSD response
  - AgentSetAppearance UDP packet summary (count, TE length, visual params length)
  - If --extract-bakes DIR: extract J2K baked texture blobs and appearance fixture

Requires only the standard library.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Pcap file reader (libpcap global header + packet records, no scapy needed)
# ---------------------------------------------------------------------------

PCAP_GLOBAL_MAGIC_LE = 0xA1B2C3D4
PCAP_GLOBAL_MAGIC_BE = 0xD4C3B2A1
PCAP_GLOBAL_MAGIC_NS_LE = 0xA1B23C4D


def _read_pcap_packets(path: Path) -> list[tuple[int, int, bytes]]:
    """Read pcap file, return list of (ts_sec, ts_usec, raw_data) for each packet."""
    data = path.read_bytes()
    pos = 0

    magic = struct.unpack_from("<I", data, pos)[0]
    if magic in (PCAP_GLOBAL_MAGIC_LE, PCAP_GLOBAL_MAGIC_NS_LE):
        endian = "<"
    elif magic == PCAP_GLOBAL_MAGIC_BE:
        endian = ">"
    else:
        raise ValueError(f"Not a pcap file (magic={magic:#010x})")

    pos += 24  # skip global header (24 bytes)
    packets = []
    while pos + 16 <= len(data):
        ts_sec, ts_usec, incl_len, orig_len = struct.unpack_from(f"{endian}IIII", data, pos)
        pos += 16
        pkt_data = data[pos : pos + incl_len]
        pos += incl_len
        packets.append((ts_sec, ts_usec, pkt_data))
    return packets


# ---------------------------------------------------------------------------
# Ethernet / IP / UDP / TCP frame decoding (minimal, no dependencies)
# ---------------------------------------------------------------------------

def _parse_eth_frame(raw: bytes) -> tuple[int, bytes] | None:
    """Return (ethertype, payload) or None."""
    if len(raw) < 14:
        return None
    ethertype = struct.unpack_from(">H", raw, 12)[0]
    if ethertype == 0x8100:  # 802.1Q VLAN tag
        if len(raw) < 18:
            return None
        ethertype = struct.unpack_from(">H", raw, 16)[0]
        return ethertype, raw[18:]
    return ethertype, raw[14:]


def _parse_ipv4(payload: bytes) -> tuple[str, str, int, bytes] | None:
    """Return (src_ip, dst_ip, protocol, transport_payload) or None."""
    if len(payload) < 20:
        return None
    ihl = (payload[0] & 0x0F) * 4
    proto = payload[9]
    src_ip = ".".join(str(b) for b in payload[12:16])
    dst_ip = ".".join(str(b) for b in payload[16:20])
    return src_ip, dst_ip, proto, payload[ihl:]


def _parse_udp(transport: bytes) -> tuple[int, int, bytes] | None:
    """Return (src_port, dst_port, payload) or None."""
    if len(transport) < 8:
        return None
    src_port, dst_port = struct.unpack_from(">HH", transport)
    return src_port, dst_port, transport[8:]


def _parse_tcp(transport: bytes) -> tuple[int, int, int, bytes] | None:
    """Return (src_port, dst_port, seq, payload) or None."""
    if len(transport) < 20:
        return None
    src_port, dst_port = struct.unpack_from(">HH", transport)
    seq = struct.unpack_from(">I", transport, 4)[0]
    data_offset = ((transport[12] >> 4) & 0xF) * 4
    return src_port, dst_port, seq, transport[data_offset:]


# ---------------------------------------------------------------------------
# TCP stream reassembly (naive: sort by seq, concatenate)
# ---------------------------------------------------------------------------

def _reassemble_tcp_streams(
    tcp_segments: dict[tuple, list[tuple[int, bytes]]],
) -> dict[tuple, bytes]:
    """Given {stream_key: [(seq, payload), ...]}, return {stream_key: reassembled_bytes}."""
    result = {}
    for key, segs in tcp_segments.items():
        segs.sort(key=lambda x: x[0])
        buf = b""
        for seq, payload in segs:
            if not payload:
                continue
            offset = seq - segs[0][0]
            if offset < 0:
                offset = 0
            if offset <= len(buf):
                buf += payload[len(buf) - offset :]
        result[key] = buf
    return result


# ---------------------------------------------------------------------------
# HTTP request/response extraction from reassembled TCP streams
# ---------------------------------------------------------------------------

def _extract_http_exchanges(stream_data: bytes) -> list[dict]:
    """Extract HTTP request/response pairs from reassembled TCP stream bytes."""
    exchanges = []
    pos = 0
    while pos < len(stream_data):
        # Find next HTTP request or response header block
        remaining = stream_data[pos:]
        cr = remaining.find(b"\r\n\r\n")
        if cr == -1:
            break
        header_block = remaining[:cr].decode("latin-1", errors="replace")
        body_start = pos + cr + 4

        # Parse first line
        first_line_end = header_block.find("\r\n")
        first_line = header_block[:first_line_end] if first_line_end != -1 else header_block

        # Parse Content-Length
        content_length = 0
        cl_match = re.search(r"Content-Length:\s*(\d+)", header_block, re.IGNORECASE)
        if cl_match:
            content_length = int(cl_match.group(1))

        body = stream_data[body_start : body_start + content_length]
        exchanges.append({
            "first_line": first_line,
            "headers": header_block,
            "body": body,
            "body_start": body_start,
        })
        pos = body_start + content_length
    return exchanges


# ---------------------------------------------------------------------------
# LLSD XML extraction (minimal — only needs key/string pairs for CAP map)
# ---------------------------------------------------------------------------

def _extract_llsd_string_map(xml_bytes: bytes) -> dict[str, str]:
    """Extract {key: string_value} from LLSD XML (sufficient for seed cap map)."""
    text = xml_bytes.decode("utf-8", errors="replace")
    keys = re.findall(r"<key>([^<]+)</key>", text)
    values = re.findall(r"<string>([^<]*)</string>", text)
    if len(keys) == len(values):
        return dict(zip(keys, values))
    return {}


# ---------------------------------------------------------------------------
# SL/OpenSim UDP packet helpers
# ---------------------------------------------------------------------------

_ZEROCODED_FLAG = 0x80
_RELIABLE_FLAG = 0x40


def _decode_zerocoded(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x00 and i + 1 < len(data):
            out.extend(b"\x00" * data[i + 1])
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def _decode_sl_message_number(payload: bytes) -> tuple[str, int]:
    """Return (frequency_prefix, message_number_int) from first bytes after header."""
    if len(payload) < 1:
        return ("?", 0)
    b0 = payload[0]
    if b0 != 0xFF:
        return ("High", b0)
    if len(payload) < 2:
        return ("?", 0)
    b1 = payload[1]
    if b1 != 0xFF:
        return ("Med", b1)
    if len(payload) < 4:
        return ("?", 0)
    num = struct.unpack_from(">H", payload, 2)[0]
    return ("Low", num)


def _parse_sl_udp_header(payload: bytes) -> tuple[int, int, bool, bool, bytes] | None:
    """Return (flags, sequence_number, reliable, zerocoded, body) or None."""
    if len(payload) < 6:
        return None
    flags = payload[0]
    seq = struct.unpack_from(">I", payload, 1)[0]
    extra_len = payload[5]
    offset = 6 + extra_len
    # Skip appended acks
    if flags & 0x10:
        if offset >= len(payload):
            return None
        ack_count = payload[-1]
        # body ends before ack block
        body = payload[offset : len(payload) - 1 - ack_count * 4]
    else:
        body = payload[offset:]
    reliable = bool(flags & _RELIABLE_FLAG)
    zerocoded = bool(flags & _ZEROCODED_FLAG)
    return flags, seq, reliable, zerocoded, body


# Low-frequency message numbers relevant to appearance
_LOW_MSG = {
    84: "AgentSetAppearance",
    154: "AgentWearablesUpdate",
    171: "AgentCachedTexture",
    172: "AgentCachedTextureResponse",
}


def _decode_message_name(body: bytes, zerocoded: bool) -> str:
    if zerocoded:
        body = _decode_zerocoded(body)
    freq, num = _decode_sl_message_number(body)
    if freq == "Low":
        return _LOW_MSG.get(num, f"Low#{num}")
    if freq == "High":
        _HIGH_MSG = {
            1: "StartPingCheck",
            2: "CompletePingCheck",
            3: "AgentUpdate",
            4: "AgentAnimation",
            12: "ObjectUpdate",
            13: "ImprovedTerseObjectUpdate",
            14: "ObjectUpdateCompressed",
            15: "ObjectUpdateCached",
        }
        return _HIGH_MSG.get(num, f"High#{num}")
    if freq == "Med":
        _MED_MSG = {
            6: "ObjectPropertiesFamily",
        }
        return _MED_MSG.get(num, f"Med#{num}")
    return f"?#{num}"


# ---------------------------------------------------------------------------
# AgentSetAppearance decoder (for TE blob and visual params extraction)
# ---------------------------------------------------------------------------

def _parse_agent_set_appearance(body: bytes) -> dict | None:
    """Parse AgentSetAppearance Low#84 body after zerodecoding.

    Layout (after 4-byte Low msg header):
    AgentData block:
      AgentID    16 bytes
      SessionID  16 bytes
      SerialNum   4 bytes (U32)
      Size        12 bytes (3×F32)
    WearableData block (Variable):
      count       1 byte
      per entry:  1 byte TextureIndex + 16 bytes CacheID
    ObjectData block:
      TE_len      2 bytes (U16)
      TE          TE_len bytes
    VisualParam block (Variable):
      count       1 byte
      per entry:  1 byte ParamValue
    """
    if len(body) < 4:
        return None
    freq, num = _decode_sl_message_number(body)
    if freq != "Low" or num != 84:
        return None
    # skip 4-byte message header
    pos = 4
    if pos + 16 + 16 + 4 + 12 > len(body):
        return None
    agent_id = body[pos:pos+16].hex()
    pos += 16
    session_id = body[pos:pos+16].hex()
    pos += 16
    serial_num = struct.unpack_from("<I", body, pos)[0]
    pos += 4
    size_x, size_y, size_z = struct.unpack_from("<fff", body, pos)
    pos += 12
    # WearableData Variable block
    if pos >= len(body):
        return None
    wd_count = body[pos]
    pos += 1
    wearable_data = []
    for _ in range(wd_count):
        if pos + 17 > len(body):
            break
        tex_idx = body[pos]
        cache_id_bytes = body[pos+1:pos+17]
        pos += 17
        # Format cache_id as UUID
        b = cache_id_bytes
        cache_id_str = (
            f"{b[3::-1].hex()}-{b[5:3:-1].hex()}-{b[7:5:-1].hex()}-"
            f"{b[8:10].hex()}-{b[10:16].hex()}"
        )
        wearable_data.append({"texture_index": tex_idx, "cache_id": cache_id_str})
    # ObjectData block (TextureEntry)
    if pos + 2 > len(body):
        return None
    te_len = struct.unpack_from("<H", body, pos)[0]
    pos += 2
    te_bytes = body[pos:pos+te_len]
    pos += te_len
    # VisualParam block
    if pos >= len(body):
        return None
    vp_count = body[pos]
    pos += 1
    visual_params = list(body[pos:pos+vp_count])
    return {
        "agent_id": agent_id,
        "serial_num": serial_num,
        "size": (size_x, size_y, size_z),
        "wearable_data": wearable_data,
        "te_hex": te_bytes.hex(),
        "te_len": te_len,
        "visual_params": visual_params,
        "vp_count": vp_count,
    }


# ---------------------------------------------------------------------------
# J2K blob extraction from HTTP stream bodies
# ---------------------------------------------------------------------------

_J2K_MAGIC = b"\xff\x4f"


def _find_j2k_blobs(stream_bytes: bytes) -> list[bytes]:
    """Find all JPEG2000 codestreams embedded in a byte blob."""
    blobs = []
    pos = 0
    while True:
        idx = stream_bytes.find(_J2K_MAGIC, pos)
        if idx == -1:
            break
        # Find next HTTP header boundary or J2K magic to delimit the blob
        next_http = stream_bytes.find(b"HTTP/", idx + 2)
        next_post = stream_bytes.find(b"POST ", idx + 2)
        next_j2k = stream_bytes.find(_J2K_MAGIC, idx + 2)
        candidates = [x for x in [next_http, next_post] if x > idx]
        if candidates:
            end = min(candidates)
        else:
            end = len(stream_bytes)
        blob = stream_bytes[idx:end].rstrip(b"\r\n ")
        if len(blob) > 100:
            blobs.append(blob)
        pos = idx + 2
    return blobs


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze(pcap_path: Path, extract_bakes_dir: Path | None) -> None:
    print(f"Reading: {pcap_path}")
    packets = _read_pcap_packets(pcap_path)
    print(f"  {len(packets)} packets total")

    udp_counter: Counter = Counter()
    tcp_segments: dict[tuple, list[tuple[int, bytes]]] = defaultdict(list)
    udp_payloads: list[tuple[str, str, int, int, bytes]] = []  # src_ip, dst_ip, src_p, dst_p, payload
    udp_total = tcp_total = 0

    for ts_sec, ts_usec, raw in packets:
        eth = _parse_eth_frame(raw)
        if eth is None:
            continue
        ethertype, ip_payload = eth
        if ethertype != 0x0800:
            continue
        ipv4 = _parse_ipv4(ip_payload)
        if ipv4 is None:
            continue
        src_ip, dst_ip, proto, transport = ipv4

        if proto == 17:  # UDP
            udp = _parse_udp(transport)
            if udp is None:
                continue
            src_port, dst_port, payload = udp
            udp_counter[(src_ip, src_port, dst_ip, dst_port)] += 1
            udp_payloads.append((src_ip, dst_ip, src_port, dst_port, payload))
            udp_total += 1

        elif proto == 6:  # TCP
            tcp = _parse_tcp(transport)
            if tcp is None:
                continue
            src_port, dst_port, seq, payload = tcp
            if payload:
                key = (src_ip, src_port, dst_ip, dst_port)
                tcp_segments[key].append((seq, payload))
            tcp_total += 1

    print(f"  {udp_total} UDP packets, {tcp_total} TCP packets\n")

    # --- UDP census ---
    print("=== UDP packet census ===")
    for (si, sp, di, dp), count in sorted(udp_counter.items(), key=lambda x: -x[1]):
        print(f"  {si}:{sp} → {di}:{dp}  {count} packets")

    # --- UDP message census ---
    print("\n=== UDP message census (SL protocol) ===")
    msg_counter: Counter = Counter()
    for src_ip, dst_ip, src_port, dst_port, payload in udp_payloads:
        parsed = _parse_sl_udp_header(payload)
        if parsed is None:
            continue
        flags, seq, reliable, zerocoded, body = parsed
        name = _decode_message_name(body, zerocoded)
        msg_counter[name] += 1
    for name, count in msg_counter.most_common(20):
        print(f"  {name:40s}  {count}")

    # --- TCP stream reassembly and HTTP analysis ---
    print("\n=== TCP stream reassembly ===")
    streams = _reassemble_tcp_streams(tcp_segments)
    print(f"  {len(streams)} unique TCP streams")

    # Find seed cap LLSD response
    cap_map: dict[str, str] = {}
    http_timeline: list[str] = []
    for (si, sp, di, dp), stream_bytes in streams.items():
        if dp != 9000 and sp != 9000:
            continue
        exchanges = _extract_http_exchanges(stream_bytes)
        for ex in exchanges:
            line = ex["first_line"]
            body = ex["body"]
            http_timeline.append(f"  {si}:{sp}→{di}:{dp}  {line[:100]}")
            # Check if this is a seed cap response (LLSD with lots of key/string pairs)
            if b"<llsd>" in body and b"UploadBakedTexture" in body:
                cap_map = _extract_llsd_string_map(body)

    print("\n=== HTTP timeline (port 9000 only) ===")
    for line in http_timeline[:50]:
        print(line)
    if len(http_timeline) > 50:
        print(f"  ... ({len(http_timeline) - 50} more)")

    print("\n=== Seed CAP map ===")
    if cap_map:
        for k, v in sorted(cap_map.items()):
            print(f"  {k:40s}  {v}")
    else:
        print("  (not found — seed cap LLSD not detected in TCP streams)")

    # --- AgentSetAppearance extraction ---
    print("\n=== AgentSetAppearance packets ===")
    appearance_packets = []
    for src_ip, dst_ip, src_port, dst_port, payload in udp_payloads:
        parsed = _parse_sl_udp_header(payload)
        if parsed is None:
            continue
        flags, seq, reliable, zerocoded, body = parsed
        if zerocoded:
            body = _decode_zerocoded(body)
        result = _parse_agent_set_appearance(body)
        if result is not None:
            appearance_packets.append(result)
            print(
                f"  serial={result['serial_num']} te_len={result['te_len']} "
                f"vp={result['vp_count']} "
                f"size=({result['size'][0]:.3f},{result['size'][1]:.3f},{result['size'][2]:.3f}) "
                f"wearables={len(result['wearable_data'])}"
            )
    if not appearance_packets:
        print("  (none found)")

    # --- J2K blob extraction ---
    if extract_bakes_dir is not None:
        print(f"\n=== Extracting J2K baked texture blobs to {extract_bakes_dir} ===")
        extract_bakes_dir.mkdir(parents=True, exist_ok=True)

        # Collect J2K blobs from all HTTP POST bodies to the baked texture uploader
        all_blobs: list[bytes] = []
        for (si, sp, di, dp), stream_bytes in streams.items():
            # Uploads go from viewer to server (dst_port=9000 for HTTP)
            if dp != 9000:
                continue
            exchanges = _extract_http_exchanges(stream_bytes)
            for ex in exchanges:
                line = ex["first_line"]
                body = ex["body"]
                if "POST" in line and body.startswith(_J2K_MAGIC):
                    all_blobs.append(body)

        # Deduplicate by content
        seen: set[int] = set()
        unique_blobs: list[bytes] = []
        for b in all_blobs:
            h = hash(b[:256])
            if h not in seen:
                seen.add(h)
                unique_blobs.append(b)

        print(f"  Found {len(unique_blobs)} unique J2K blobs")
        for i, blob in enumerate(unique_blobs):
            out_path = extract_bakes_dir / f"bake-{i}.j2k"
            out_path.write_bytes(blob)
            print(f"  bake-{i}.j2k  {len(blob):>8,} bytes → {out_path}")

        # Write appearance fixture if we have AgentSetAppearance data
        if appearance_packets:
            ap = appearance_packets[-1]  # use last/highest serial
            # Build te_uuid_offsets by scanning TE for known UUID patterns
            # (heuristic: report all 16-byte-aligned non-null UUID slots)
            te_bytes = bytes.fromhex(ap["te_hex"])
            te_uuid_offsets = []
            # Slot offsets are at 19, 37, 55, 73, 91 (from Firestorm pcap analysis)
            # We scan for non-null 16-byte runs as a heuristic
            for offset in range(0, len(te_bytes) - 15, 1):
                chunk = te_bytes[offset:offset+16]
                if chunk != b"\x00" * 16 and all(0x00 <= b <= 0xFF for b in chunk):
                    # Rough filter: check if it looks like a UUID (has some non-zero bytes)
                    if sum(1 for b in chunk if b != 0) >= 8:
                        u = chunk
                        uuid_str = (
                            f"{u[0:4].hex()}-{u[4:6].hex()}-{u[6:8].hex()}-"
                            f"{u[8:10].hex()}-{u[10:16].hex()}"
                        )
                        te_uuid_offsets.append({"te_offset": offset, "uuid": uuid_str})

            agent_id_hex = ap["agent_id"]
            agent_uuid = (
                f"{agent_id_hex[0:8]}-{agent_id_hex[8:12]}-{agent_id_hex[12:16]}-"
                f"{agent_id_hex[16:20]}-{agent_id_hex[20:32]}"
            )
            fixture = {
                "source": f"pcap:{pcap_path.name}",
                "agent_id": agent_uuid,
                "serial_num": ap["serial_num"],
                "size_vec": list(ap["size"]),
                "wearable_data": ap["wearable_data"],
                "te_hex": ap["te_hex"],
                "te_uuid_offsets_heuristic": te_uuid_offsets[:20],
                "visual_params": ap["visual_params"],
                "blob_count": len(unique_blobs),
            }
            fixture_path = extract_bakes_dir / "appearance-fixture.json"
            fixture_path.write_text(json.dumps(fixture, indent=2))
            print(f"\n  Wrote appearance fixture → {fixture_path}")
            print(f"  agent_id={agent_uuid} serial={ap['serial_num']} "
                  f"te={len(te_bytes)}B vp={ap['vp_count']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze OpenSim/SL pcap file")
    parser.add_argument("pcap", type=Path, help="Path to .pcap file")
    parser.add_argument(
        "--extract-bakes",
        metavar="DIR",
        type=Path,
        default=None,
        help="Extract J2K baked texture blobs and appearance fixture to DIR",
    )
    args = parser.parse_args()

    if not args.pcap.exists():
        print(f"Error: {args.pcap} not found", file=sys.stderr)
        sys.exit(1)

    analyze(args.pcap, args.extract_bakes)


if __name__ == "__main__":
    main()

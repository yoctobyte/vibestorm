"""Small synchronous UDP probe client."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class UdpProbeResult:
    source_ip: str
    source_port: int
    payload: bytes


@dataclass(slots=True, frozen=True)
class UdpCollectedPacket:
    source_ip: str
    source_port: int
    payload: bytes


class UdpSocketClient:
    async def send_and_receive_once(
        self,
        host: str,
        port: int,
        payload: bytes,
        *,
        timeout: float = 2.0,
    ) -> UdpProbeResult | None:
        return await asyncio.to_thread(self._send_and_receive_once_sync, host, port, payload, timeout)

    def _send_and_receive_once_sync(
        self,
        host: str,
        port: int,
        payload: bytes,
        timeout: float,
    ) -> UdpProbeResult | None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(payload, (host, port))
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                return None
        return UdpProbeResult(source_ip=addr[0], source_port=addr[1], payload=data)

    async def send_sequence_and_collect(
        self,
        host: str,
        port: int,
        payloads: list[bytes],
        *,
        timeout: float = 1.0,
        max_packets: int = 5,
    ) -> list[UdpCollectedPacket]:
        return await asyncio.to_thread(
            self._send_sequence_and_collect_sync,
            host,
            port,
            payloads,
            timeout,
            max_packets,
        )

    def _send_sequence_and_collect_sync(
        self,
        host: str,
        port: int,
        payloads: list[bytes],
        timeout: float,
        max_packets: int,
    ) -> list[UdpCollectedPacket]:
        collected: list[UdpCollectedPacket] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            for payload in payloads:
                sock.sendto(payload, (host, port))

            while len(collected) < max_packets:
                try:
                    data, addr = sock.recvfrom(65535)
                except socket.timeout:
                    break
                collected.append(
                    UdpCollectedPacket(
                        source_ip=addr[0],
                        source_port=addr[1],
                        payload=data,
                    ),
                )
        return collected

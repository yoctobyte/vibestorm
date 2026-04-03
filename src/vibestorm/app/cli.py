"""CLI entry point for Vibestorm."""

from __future__ import annotations

import argparse
import asyncio
import platform
from pathlib import Path

from vibestorm import __version__
from vibestorm.app.main import get_status
from vibestorm.caps.client import CapabilityClient
from vibestorm.event_queue.client import EventQueueClient
from vibestorm.login.client import LoginClient
from vibestorm.login.models import LoginCredentials, LoginRequest
from vibestorm.udp.dispatch import MessageDispatcher
from vibestorm.udp.messages import (
    encode_complete_agent_movement,
    encode_use_circuit_code,
    parse_agent_movement_complete,
    parse_region_handshake,
)
from vibestorm.udp.packet import LL_RELIABLE_FLAG, build_packet, split_packet
from vibestorm.udp.session import SessionConfig, run_live_session
from vibestorm.udp.socket_client import UdpSocketClient
from vibestorm.udp.zerocode import decode_zerocode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibestorm")
    parser.add_argument("--version", action="store_true", help="Print the package version and exit.")
    subparsers = parser.add_subparsers(dest="command")

    login_parser = subparsers.add_parser("login-bootstrap", help="Perform XML-RPC login bootstrap.")
    login_parser.add_argument("--login-uri", required=True)
    login_parser.add_argument("--first", required=True)
    login_parser.add_argument("--last", required=True)
    login_parser.add_argument("--password", required=True)
    login_parser.add_argument("--start", default="last")

    caps_parser = subparsers.add_parser(
        "resolve-seed-caps",
        help="Login and resolve named seed capabilities.",
    )
    caps_parser.add_argument("--login-uri", required=True)
    caps_parser.add_argument("--first", required=True)
    caps_parser.add_argument("--last", required=True)
    caps_parser.add_argument("--password", required=True)
    caps_parser.add_argument("--start", default="last")
    caps_parser.add_argument("capability", nargs="+")

    eq_parser = subparsers.add_parser(
        "event-queue-once",
        help="Login, resolve EventQueueGet, and poll it once.",
    )
    eq_parser.add_argument("--login-uri", required=True)
    eq_parser.add_argument("--first", required=True)
    eq_parser.add_argument("--last", required=True)
    eq_parser.add_argument("--password", required=True)
    eq_parser.add_argument("--start", default="last")

    udp_parser = subparsers.add_parser(
        "udp-probe",
        help="Login and send a UseCircuitCode UDP probe.",
    )
    udp_parser.add_argument("--login-uri", required=True)
    udp_parser.add_argument("--first", required=True)
    udp_parser.add_argument("--last", required=True)
    udp_parser.add_argument("--password", required=True)
    udp_parser.add_argument("--start", default="last")

    handshake_parser = subparsers.add_parser(
        "handshake-probe",
        help="Login, send UseCircuitCode and CompleteAgentMovement, and decode replies.",
    )
    handshake_parser.add_argument("--login-uri", required=True)
    handshake_parser.add_argument("--first", required=True)
    handshake_parser.add_argument("--last", required=True)
    handshake_parser.add_argument("--password", required=True)
    handshake_parser.add_argument("--start", default="last")

    session_parser = subparsers.add_parser(
        "session-run",
        help="Login and run a bounded live UDP session loop.",
    )
    session_parser.add_argument("--login-uri", required=True)
    session_parser.add_argument("--first", required=True)
    session_parser.add_argument("--last", required=True)
    session_parser.add_argument("--password", required=True)
    session_parser.add_argument("--start", default="last")
    session_parser.add_argument("--duration", type=float, default=15.0)
    session_parser.add_argument("--agent-update-interval", type=float, default=1.0)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        print(__version__)
        return 0

    if args.command == "login-bootstrap":
        request = LoginRequest(
            login_uri=args.login_uri,
            credentials=LoginCredentials(
                first=args.first,
                last=args.last,
                password=args.password,
            ),
            start=args.start,
            version=__version__,
            platform=platform.system(),
            platform_version=platform.platform(),
        )
        result = asyncio.run(LoginClient().login(request))
        print(f"login={result.message or 'ok'}")
        print(f"agent_id={result.agent_id}")
        print(f"session_id={result.session_id}")
        print(f"secure_session_id={result.secure_session_id}")
        print(f"circuit_code={result.circuit_code}")
        print(f"sim={result.sim_ip}:{result.sim_port}")
        print(f"seed_capability={result.seed_capability}")
        print(f"region=({result.region_x},{result.region_y})")
        return 0

    if args.command == "resolve-seed-caps":
        request = LoginRequest(
            login_uri=args.login_uri,
            credentials=LoginCredentials(
                first=args.first,
                last=args.last,
                password=args.password,
            ),
            start=args.start,
            version=__version__,
            platform=platform.system(),
            platform_version=platform.platform(),
        )
        bootstrap = asyncio.run(LoginClient().login(request))
        resolved = asyncio.run(CapabilityClient().resolve_seed_caps(bootstrap.seed_capability, args.capability))
        for name in args.capability:
            print(f"{name}={resolved.get(name, '')}")
        return 0

    if args.command == "event-queue-once":
        request = LoginRequest(
            login_uri=args.login_uri,
            credentials=LoginCredentials(
                first=args.first,
                last=args.last,
                password=args.password,
            ),
            start=args.start,
            version=__version__,
            platform=platform.system(),
            platform_version=platform.platform(),
        )
        bootstrap = asyncio.run(LoginClient().login(request))
        caps = asyncio.run(CapabilityClient().resolve_seed_caps(bootstrap.seed_capability, ["EventQueueGet"]))
        result = asyncio.run(EventQueueClient().poll_once(caps["EventQueueGet"]))
        print(f"status={result.status}")
        if result.payload is not None:
            print(result.payload)
        return 0

    if args.command == "udp-probe":
        request = LoginRequest(
            login_uri=args.login_uri,
            credentials=LoginCredentials(
                first=args.first,
                last=args.last,
                password=args.password,
            ),
            start=args.start,
            version=__version__,
            platform=platform.system(),
            platform_version=platform.platform(),
        )
        bootstrap = asyncio.run(LoginClient().login(request))
        message = encode_use_circuit_code(
            bootstrap.circuit_code,
            bootstrap.session_id,
            bootstrap.agent_id,
        )
        packet = build_packet(message, sequence=1, flags=LL_RELIABLE_FLAG)
        result = asyncio.run(
            UdpSocketClient().send_and_receive_once(
                bootstrap.sim_ip,
                bootstrap.sim_port,
                packet,
            ),
        )
        if result is None:
            print("status=timeout")
            return 0

        decoded_packet = decode_zerocode(result.payload)
        view = split_packet(decoded_packet)
        dispatch = MessageDispatcher.from_repo_root(Path.cwd()).dispatch(view.message)
        print("status=received")
        print(f"source={result.source_ip}:{result.source_port}")
        print(f"sequence={view.header.sequence}")
        print(f"message={dispatch.summary.name}")
        return 0

    if args.command == "handshake-probe":
        request = LoginRequest(
            login_uri=args.login_uri,
            credentials=LoginCredentials(
                first=args.first,
                last=args.last,
                password=args.password,
            ),
            start=args.start,
            version=__version__,
            platform=platform.system(),
            platform_version=platform.platform(),
        )
        bootstrap = asyncio.run(LoginClient().login(request))
        dispatcher = MessageDispatcher.from_repo_root(Path.cwd())
        packet1 = build_packet(
            encode_use_circuit_code(bootstrap.circuit_code, bootstrap.session_id, bootstrap.agent_id),
            sequence=1,
            flags=LL_RELIABLE_FLAG,
        )
        packet2 = build_packet(
            encode_complete_agent_movement(bootstrap.agent_id, bootstrap.session_id, bootstrap.circuit_code),
            sequence=2,
            flags=LL_RELIABLE_FLAG,
        )
        packets = asyncio.run(
            UdpSocketClient().send_sequence_and_collect(
                bootstrap.sim_ip,
                bootstrap.sim_port,
                [packet1, packet2],
            ),
        )
        print(f"received={len(packets)}")
        for item in packets:
            decoded_packet = decode_zerocode(item.payload)
            view = split_packet(decoded_packet)
            dispatch = dispatcher.dispatch(view.message)
            line = f"{item.source_ip}:{item.source_port} seq={view.header.sequence} msg={dispatch.summary.name}"
            if dispatch.summary.name == "RegionHandshake":
                parsed = parse_region_handshake(dispatch)
                line += f" sim_name={parsed.sim_name!r} flags={parsed.region_flags}"
            elif dispatch.summary.name == "AgentMovementComplete":
                parsed = parse_agent_movement_complete(dispatch)
                line += f" region_handle={parsed.region_handle} position={parsed.position}"
            print(line)
        return 0

    if args.command == "session-run":
        request = LoginRequest(
            login_uri=args.login_uri,
            credentials=LoginCredentials(
                first=args.first,
                last=args.last,
                password=args.password,
            ),
            start=args.start,
            version=__version__,
            platform=platform.system(),
            platform_version=platform.platform(),
        )
        bootstrap = asyncio.run(LoginClient().login(request))
        report = asyncio.run(
            run_live_session(
                bootstrap,
                MessageDispatcher.from_repo_root(Path.cwd()),
                config=SessionConfig(
                    duration_seconds=args.duration,
                    agent_update_interval_seconds=args.agent_update_interval,
                ),
            ),
        )
        print(f"status={'closed' if report.close_reason else 'completed'}")
        print(f"elapsed={report.elapsed_seconds:.2f}")
        print(f"received={report.total_received}")
        print(f"handshake_reply_sent={report.handshake_reply_sent}")
        print(f"agent_updates_sent={report.agent_update_count}")
        print(f"pending_reliable={len(report.pending_reliable_sequences)}")
        if report.last_region_name is not None:
            print(f"region_name={report.last_region_name}")
        if report.close_reason is not None:
            print(f"close_reason={report.close_reason}")
        for name in sorted(report.message_counts):
            print(f"message[{name}]={report.message_counts[name]}")
        return 0

    status = get_status()
    print(f"{status.phase}: {status.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

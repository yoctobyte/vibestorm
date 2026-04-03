"""CLI entry point for Vibestorm."""

from __future__ import annotations

import argparse
import asyncio
import platform
from pathlib import Path
from typing import Iterable

from vibestorm import __version__
from vibestorm.app.main import get_status
from vibestorm.caps.client import CapabilityClient
from vibestorm.event_queue.client import EventQueueClient
from vibestorm.fixtures.unknowns_db import DEFAULT_UNKNOWNS_DB_PATH, UnknownsDatabase
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
from vibestorm.udp.session import SessionConfig, SessionEvent, SessionReport, run_live_session
from vibestorm.udp.socket_client import UdpSocketClient
from vibestorm.udp.zerocode import decode_zerocode
from vibestorm.world.models import WorldView


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
    session_parser.add_argument("--spawn-cube", action="store_true")
    session_parser.add_argument(
        "--capture-dir",
        type=Path,
        help="Write selected inbound message fixtures under this directory.",
    )
    session_parser.add_argument(
        "--capture-message",
        action="append",
        default=[],
        help="Message name to capture. Repeatable. Defaults to ObjectUpdate when capture is enabled.",
    )
    session_parser.add_argument(
        "--capture-mode",
        choices=("smart", "all"),
        default="smart",
        help="Capture only unknown/violating messages or every matching message.",
    )
    session_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print live session events and transport diagnostics.",
    )

    unknowns_parser = subparsers.add_parser(
        "unknowns-report",
        help="Summarize collected interesting unknown payloads from the SQLite database.",
    )
    unknowns_parser.add_argument("--limit", type=int, default=20)
    return parser


def format_world_status(world_view: WorldView) -> list[str]:
    lines: list[str] = []
    if world_view.region is not None:
        lines.append(
            f"world[region]={world_view.region.name} "
            f"grid=({world_view.region.grid_x},{world_view.region.grid_y})",
        )
    if world_view.latest_sim_stats is not None:
        lines.append(
            f"world[sim_stats]=updates:{world_view.sim_stats_updates} "
            f"capacity:{world_view.latest_sim_stats.object_capacity} "
            f"stats:{world_view.latest_sim_stats.stats_count}",
        )
    if world_view.latest_time is not None:
        lines.append(
            f"world[time]=updates:{world_view.time_updates} "
            f"sun_phase:{world_view.latest_time.sun_phase:.3f} "
            f"sec_per_day:{world_view.latest_time.sec_per_day}",
        )
    if world_view.coarse_agents:
        lines.append(
            f"world[coarse_agents]=updates:{world_view.coarse_location_updates} "
            f"count:{len(world_view.coarse_agents)}",
        )
        for agent in world_view.coarse_agents:
            lines.append(
                f"world[coarse_agent]={agent.agent_id} "
                f"pos=({agent.x},{agent.y},{agent.z}) "
                f"you={agent.is_you} prey={agent.is_prey}",
            )
    if world_view.latest_object_update is not None:
        lines.append(
            f"world[object_update]=events:{world_view.object_update_events} "
            f"objects:{world_view.latest_object_update.object_count} "
            f"region_handle:{world_view.latest_object_update.region_handle}",
        )
    if world_view.objects:
        lines.append(f"world[objects]=tracked:{len(world_view.objects)}")
        for obj in sorted(world_view.objects.values(), key=lambda item: item.local_id)[:3]:
            line = (
                f"world[object]={obj.full_id} "
                f"local_id={obj.local_id} parent_id={obj.parent_id} "
                f"pcode={obj.pcode} variant={obj.variant} "
                f"scale=({obj.scale[0]:.2f},{obj.scale[1]:.2f},{obj.scale[2]:.2f})"
            )
            if obj.position is not None:
                line += f" pos=({obj.position[0]:.2f},{obj.position[1]:.2f},{obj.position[2]:.2f})"
            if "FirstName" in obj.name_values or "LastName" in obj.name_values:
                first = obj.name_values.get("FirstName", "").strip()
                last = obj.name_values.get("LastName", "").strip()
                full_name = " ".join(part for part in (first, last) if part)
                if full_name:
                    line += f" name={full_name}"
            if obj.default_texture_id is not None:
                line += f" texture={obj.default_texture_id}"
            lines.append(line)
    return lines


def format_session_report(report: SessionReport, *, verbose: bool = False) -> list[str]:
    lines = [
        f"status={'closed' if report.close_reason else 'completed'}",
        f"elapsed={report.elapsed_seconds:.2f}",
        f"received={report.total_received}",
        f"movement_completed={report.movement_completed}",
    ]
    if report.last_region_name is not None:
        lines.append(f"region_name={report.last_region_name}")
    if report.close_reason is not None:
        lines.append(f"close_reason={report.close_reason}")
    lines.extend(format_world_status(report.world_view))
    if verbose:
        lines.extend(
            [
                f"handshake_reply_sent={report.handshake_reply_sent}",
                f"ping_requests_handled={report.ping_requests_handled}",
                f"appended_acks_received={report.appended_acks_received}",
                f"packet_acks_received={report.packet_acks_received}",
                f"agent_updates_sent={report.agent_update_count}",
                f"pending_reliable={len(report.pending_reliable_sequences)}",
            ],
        )
        for name in sorted(report.message_counts):
            lines.append(f"message[{name}]={report.message_counts[name]}")
    return lines


def print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


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
        def print_session_event(event: SessionEvent) -> None:
            print(f"event[{event.at_seconds:.3f}]={event.kind} {event.detail}", flush=True)

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
        print(
            "session=starting "
            f"sim={bootstrap.sim_ip}:{bootstrap.sim_port} "
            f"duration={args.duration:.1f}s "
            f"spawn_cube={args.spawn_cube} "
            f"capture={args.capture_dir if args.capture_dir else 'off'} "
            f"capture_mode={args.capture_mode} "
            f"unknowns_db={DEFAULT_UNKNOWNS_DB_PATH} "
            f"verbose={args.verbose}",
            flush=True,
        )
        report = asyncio.run(
            run_live_session(
                bootstrap,
                MessageDispatcher.from_repo_root(Path.cwd()),
                config=SessionConfig(
                    duration_seconds=args.duration,
                    agent_update_interval_seconds=args.agent_update_interval,
                    spawn_test_cube=args.spawn_cube,
                    capture_dir=args.capture_dir,
                    capture_messages=tuple(args.capture_message),
                    capture_mode=args.capture_mode,
                ),
                on_event=print_session_event if args.verbose else None,
            ),
        )
        print_lines(format_session_report(report, verbose=args.verbose))
        return 0

    if args.command == "unknowns-report":
        database = UnknownsDatabase(DEFAULT_UNKNOWNS_DB_PATH)
        stats = database.read_stats()
        print(f"db={DEFAULT_UNKNOWNS_DB_PATH}")
        print(f"packets={stats.packet_count}")
        print(f"entities={stats.entity_count}")
        print(f"distinct_objects={stats.distinct_objects}")
        print(f"distinct_fingerprints={stats.distinct_fingerprints}")
        print(f"multi_object_packets={stats.multi_object_packets}")
        print(f"partial_packets={stats.partial_packets}")
        print(f"rich_entities={stats.rich_entities}")
        for item in database.summarize_object_update_packets(limit=args.limit):
            line = (
                f"packet_group[count]={item['seen_count']} "
                f"status={item['decode_status']} "
                f"reason={item['capture_reason']} "
                f"multi_object={item['multi_object_count']} "
                f"first={item['first_seen_at_seconds']:.3f} "
                f"last={item['last_seen_at_seconds']:.3f}"
            )
            print(line)
            if item["sample_decode_error"] is not None:
                print(f"decode_error={item['sample_decode_error']}")
        for item in database.summarize_payload_fingerprints(limit=args.limit):
            line = (
                f"fingerprint[count]={item['seen_count']} "
                f"variant={item['variant']} "
                f"label={item['label'] or '-'} "
                f"object={item['sample_full_id']} "
                f"first={item['first_seen_at_seconds']:.3f} "
                f"last={item['last_seen_at_seconds']:.3f}"
            )
            print(line)
            print(f"summary={item['sample_interest_summary']}")
        for item in reversed(database.recent_nearby_chat(limit=min(args.limit, 20))):
            print(
                f"chat[{item['observed_at_seconds']:.3f}]="
                f"{item['from_name']}: {item['message']} "
                f"(type={item['chat_type']} audible={item['audible']} "
                f"pos=({item['position'][0]:.2f},{item['position'][1]:.2f},{item['position'][2]:.2f}))",
            )
        return 0

    status = get_status()
    print(f"{status.phase}: {status.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

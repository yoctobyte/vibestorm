# Language Selection Record

Timestamp: 2026-04-02T16:43:11Z

Status: decided

## Purpose

Choose the implementation language for Vibestorm in a way that remains legible to multiple agents and contributors.

## Decision

Python is the implementation language for Vibestorm.

## Why Python Was Chosen

- Fastest path from protocol research to working login and transport code.
- Strong standard library support for binary parsing, sockets, and data modeling.
- Easy for multiple agents and human contributors to read and modify.
- Good fit for protocol exploration, fixture generation, and tooling.
- Keeps early-stage iteration cheap while the protocol surface is still being mapped.

## Risks Accepted

- Python makes it easier to blur transport, protocol, and world-state boundaries unless we enforce structure.
- Performance may become a constraint later for heavy decoding or 3D-facing workloads.
- Type guarantees are weaker than in Rust, so discipline in interfaces and tests matters more.

## Mitigations

- Keep transport, protocol, capabilities, and world models in separate modules.
- Use type hints broadly and run static checking.
- Prefer dataclasses or typed models over loose dictionaries in internal APIs.
- Add fixtures early for packet and LLSD payload decoding.
- Keep rendering and UI decoupled from the protocol core so performance-sensitive pieces can be replaced later if needed.

## Revisit Triggers

Revisit the language decision only if one of these becomes true:

- packet decode throughput becomes a proven bottleneck
- Python async/network ergonomics materially block progress
- a future renderer architecture requires a different core/runtime split

## Follow-Up After Decision

Once chosen, add:

- scaffold
- build/run/test commands
- module map
- first handoff log

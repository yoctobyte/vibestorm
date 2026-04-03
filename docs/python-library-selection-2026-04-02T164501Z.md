# Python Library Selection

Timestamp: 2026-04-02T16:45:01Z

This document records likely Python library choices for Vibestorm and the reasoning behind each.

## Selection Principles

- Prefer standard library first when it keeps complexity low.
- Avoid framework lock-in early.
- Prefer libraries with clear data boundaries and low hidden behavior.
- Avoid deep dependency trees for core protocol code.
- Keep choices legible to multiple agents and easy to replace later.

## Recommended Stack

### Runtime

- Python 3.13 target, 3.12 minimum if needed

Reason:

- modern typing
- strong async support
- good dataclass and standard-library ergonomics

### Packaging and Environment

Primary choice:

- `uv`

Fallback:

- `venv` plus `pip`

Reason:

- `uv` is fast, reproducible, and simple for multi-agent setup
- fallback remains standard Python tooling

### Project Metadata

Primary choice:

- `pyproject.toml`

Reason:

- standard packaging entry point
- tool configuration can live in one place

### CLI

Primary choice:

- `typer`

Fallback:

- `argparse`

Reason:

- `typer` gives readable command structure with little boilerplate
- `argparse` is acceptable if zero dependencies becomes more important

Recommendation:

- start with `argparse` if the CLI stays tiny
- switch to `typer` once multiple subcommands exist

### Configuration

Primary choice:

- `pydantic-settings`

Fallback:

- dataclasses plus `os.environ` plus small local parsing helpers

Reason:

- strong validation for credentials, endpoints, and runtime configuration
- explicit typed config objects help handoff between agents

Recommendation:

- use `pydantic-settings` if config grows beyond a handful of variables

### Data Models

Primary choice:

- standard-library `dataclasses`

Fallback:

- `pydantic`

Reason:

- dataclasses are light and stable for internal models
- `pydantic` is useful at IO boundaries where validation matters

Recommendation:

- dataclasses for internal transport and world models
- `pydantic` only for untrusted external payload parsing if needed

### Async Runtime

Primary choice:

- standard-library `asyncio`

Reason:

- enough for UDP, timers, queues, and HTTP clients
- avoids adding a runtime abstraction too early

### HTTP Client

Primary choice:

- `httpx`

Fallback:

- `aiohttp`

Reason:

- clean sync/async API
- good timeout and transport controls
- easy to test

Recommendation:

- use `httpx.AsyncClient` for login and capabilities

### UDP Networking

Primary choice:

- standard-library `asyncio` datagram transport or non-blocking sockets

Reason:

- the protocol is custom; a thin layer over standard sockets is preferable
- external networking frameworks add little value here

Recommendation:

- own the UDP circuit logic directly

### Binary Parsing

Primary choices:

- `struct`
- `memoryview`
- `enum`

Optional helper:

- `construct`

Reason:

- the message system is custom enough that direct parsing is likely clearer than a declarative binary DSL
- `construct` may help for isolated packet structures, but should not own the whole transport layer

Recommendation:

- start with standard library parsing
- only introduce `construct` if repetitive binary layouts become a real maintenance problem

### LLSD Support

Primary choice:

- local implementation for the specific XML and notation forms we need

Fallback:

- evaluate a small third-party LLSD library if it is clearly maintained and minimal

Reason:

- LLSD usage is protocol-specific and likely narrower than a generic library suggests
- owning the minimal supported subset may be safer than inheriting obscure behavior

Recommendation:

- implement the minimum LLSD needed for seed caps and `EventQueueGet`

### Logging

Primary choice:

- standard-library `logging`

Fallback:

- `structlog`

Reason:

- standard logging is sufficient if fields and redaction are designed properly
- `structlog` is only worth it if logs become heavily event-oriented

Recommendation:

- start with standard logging and a small redaction helper

### Testing

Primary choice:

- `pytest`

Companions:

- `pytest-asyncio`
- `pytest-cov`

Reason:

- strong ecosystem
- easy fixture management
- good async support

### Static Analysis and Formatting

Primary choices:

- `ruff`
- `mypy`

Reason:

- fast linting and formatting path
- static checks reduce boundary drift in a dynamic language

### Snapshot or Golden-File Testing

Primary choice:

- plain files under `test/fixtures/`

Fallback:

- a snapshot plugin if the fixture count grows

Reason:

- plain fixtures are easiest for cross-agent review and IDE portability

## Library Decisions By Area

### Core Protocol Layer

Use:

- `asyncio`
- `struct`
- `memoryview`
- `dataclasses`
- `enum`
- `logging`

Avoid:

- large framework abstractions
- opaque serializer stacks

### Config and App Layer

Use:

- `pyproject.toml`
- `uv`
- `argparse` or `typer`
- `pydantic-settings` if config grows

### Testing Layer

Use:

- `pytest`
- `pytest-asyncio`
- fixture files
- `mypy`
- `ruff`

## Deferred Decisions

These should stay open until needed:

- 2D UI library
- 3D rendering library
- persistence layer for captured packets or caches
- plugin architecture

## Recommendation Summary

Default stack:

- Python
- `uv`
- `pyproject.toml`
- `asyncio`
- `httpx`
- standard UDP sockets
- `dataclasses`
- `logging`
- `pytest`
- `ruff`
- `mypy`

Keep the core lean. The protocol work is already complex; adding heavy libraries early is more likely to hide bugs than remove work.

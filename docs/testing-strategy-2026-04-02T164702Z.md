# Testing Strategy

Timestamp: 2026-04-02T16:47:02Z

This document defines the testing approach for Vibestorm's protocol-first Python implementation.

## Testing Priorities

1. transport correctness
2. protocol parsing correctness
3. session/control-plane correctness
4. user-facing behavior

The highest risk early on is incorrect transport and protocol handling, not UI behavior.

## Test Layers

### Unit Tests

Target:

- packet headers
- zerocode encode/decode
- sequence tracking
- ack bookkeeping
- message-template parsing
- LLSD parsing/serialization
- login response parsing

Goal:

- fast, deterministic, fixture-heavy tests

### Replay Tests

Target:

- feed captured packets or payloads through decoders without real networking

Goal:

- prove parser and dispatcher correctness against realistic data

### Integration Tests

Target:

- login bootstrap against controlled configuration
- UDP circuit startup in a constrained session
- capability resolution
- `EventQueueGet` long-poll loop

Goal:

- verify milestone behavior without requiring full UI

### Manual Verification Scripts

Target:

- packet inspection
- seed-cap fetch and print
- login payload debug

Goal:

- give agents and humans a narrow way to validate one subsystem at a time

## Fixture Strategy

- Keep fixtures under `test/fixtures/`.
- Prefer plain binary payload files and plain text annotations.
- Record source and purpose for every non-trivial fixture.
- Store redacted live samples when available.
- Do not check in secrets or reusable session credentials.

## Verification Standard

The repo should distinguish:

- parses
- handles
- verified in tests
- verified in live session

A working parse is not the same as a working implementation.

## Early Required Tests

- packet header parse round-trips
- zerocode decode examples
- ACK append/extract behavior
- message-template loader reads official artifact
- login response model parses expected fields
- LLSD subset handles seed-cap request/response forms

## Tooling

Recommended tools:

- `pytest`
- `pytest-asyncio`
- `pytest-cov`
- `ruff`
- `mypy`

## CI Direction

Even before full CI exists, the expected local verification flow should be:

1. lint
2. type check
3. unit tests
4. targeted integration tests

## Anti-Patterns

- tests that only assert logs
- giant end-to-end tests as first coverage
- fixtures with unclear provenance
- integration tests that hide multiple failure sources
- protocol handlers without isolated fixture-based tests

## Next Test Planning Step

Once the scaffold exists, add:

- `test/fixtures/README.md`
- first packet-header tests
- first template-loader test

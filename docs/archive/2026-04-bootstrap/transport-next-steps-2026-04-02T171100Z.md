# Transport Next Steps

Timestamp: 2026-04-02T17:11:00Z

## Current State

The UDP core now has:

- packet header parsing
- ACK trailer extraction
- zerocode expansion
- template summary loading
- message-number decoding
- template-backed dispatch
- semantic handling for:
  - `StartPingCheck`
  - `CompletePingCheck`
  - `UseCircuitCode`

## Immediate Implementation Plan

1. add packet encoder support for header flags and sequence numbers
2. add reliable outbound bookkeeping and ACK collection
3. implement `UseCircuitCode` send path against a real UDP socket
4. implement `StartPingCheck` -> `CompletePingCheck` response behavior
5. add `CompleteAgentMovement` builder and parser
6. add first integration harness against the local OpenSim host

## Design Guardrails

- keep semantic message parsers separate from raw transport parsing
- keep message builders narrow and explicit
- do not mix socket IO into the pure parser/builder tests
- keep UUID and integer byte order behavior tested directly

## Handoff Note

The next agent should be able to work at either of two levels:

- pure transport: header encode, sequence tracking, reliable ACK state
- pure semantics: `CompleteAgentMovement`, `RegionHandshake`, and first state transitions

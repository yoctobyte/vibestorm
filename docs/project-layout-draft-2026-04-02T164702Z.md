# Project Layout Draft

Timestamp: 2026-04-02T16:47:02Z

This is the proposed Python project layout for Vibestorm.

## Proposed Layout

```text
vibestorm/
  docs/
  spec/
  test/
    fixtures/
  third_party/
    secondlife/
  tools/
  src/
    vibestorm/
      __init__.py
      app/
      login/
      udp/
      caps/
      event_queue/
      world/
      util/
```

## Directory Purposes

- `docs/`: plans, decisions, handoff notes, higher-level research
- `spec/`: protocol coverage and implementation-focused specifications
- `test/fixtures/`: captured or synthetic payloads and golden files
- `third_party/secondlife/`: fetched canonical protocol artifacts
- `tools/`: fetch, inspect, scaffold, and dev helper scripts
- `src/vibestorm/app/`: CLI and app wiring
- `src/vibestorm/login/`: login bootstrap code
- `src/vibestorm/udp/`: packet, circuit, ack, resend, zerocode, and transport logic
- `src/vibestorm/caps/`: seed capability resolution and cap clients
- `src/vibestorm/event_queue/`: long-poll event queue support
- `src/vibestorm/world/`: normalized session, region, agent, and object state
- `src/vibestorm/util/`: narrow shared helpers, not dumping ground code

## Layout Rules

- `udp/` should not depend on `world/`.
- `world/` may depend on normalized protocol events, not raw sockets.
- `app/` wires modules together but should not own protocol logic.
- `caps/` and `event_queue/` should share LLSD support through a small internal boundary.
- `util/` should remain minimal; if a helper becomes domain-specific, move it into the owning module.

## File Suggestions For Initial Scaffold

- `pyproject.toml`
- `README.md`
- `src/vibestorm/app/cli.py`
- `src/vibestorm/app/main.py`
- `src/vibestorm/login/models.py`
- `src/vibestorm/login/client.py`
- `src/vibestorm/udp/packet.py`
- `src/vibestorm/udp/zerocode.py`
- `src/vibestorm/udp/template.py`
- `src/vibestorm/caps/client.py`
- `src/vibestorm/event_queue/client.py`
- `src/vibestorm/world/models.py`
- `test/fixtures/README.md`

## Rule For New Files

New files should be added where their ownership is obvious. If ownership is not obvious, the design is probably still too vague.

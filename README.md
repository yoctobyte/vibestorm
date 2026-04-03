# Vibestorm

Vibestorm is a staged Second Life client project.

The current focus is protocol-core groundwork:

- login bootstrap
- UDP transport and message decoding
- capabilities and `EventQueueGet`
- normalized world/session models

## Status

Planning is in place. Phase 1 scaffolding exists, but protocol implementation is still pending.

## Layout

- `docs/`: plans, decisions, research, handoff templates
- `spec/`: message and capability coverage tracking
- `third_party/secondlife/`: fetched canonical protocol artifacts
- `tools/`: reproducible helper scripts
- `src/vibestorm/`: Python package
- `test/`: fixtures and tests

## Getting Started

Recommended:

```bash
uv sync
uv run vibestorm --help
```

Fallback:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
vibestorm --help
```

## Next Step

Implement the first protocol-core slice:

1. packet header parsing
2. zerocode decode
3. message-template loading
4. login/bootstrap models

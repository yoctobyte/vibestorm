#!/usr/bin/env python3
"""Build a structured inventory for captured live fixtures."""

from __future__ import annotations

from pathlib import Path

from vibestorm.fixtures.inventory import write_fixture_inventory


def main() -> int:
    root = Path("test/fixtures/live")
    path = write_fixture_inventory(root)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

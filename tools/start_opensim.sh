#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$ROOT_DIR/local/opensim/runtime/bin"

if [[ ! -x "$BIN_DIR/OpenSim" ]]; then
  printf 'OpenSim binary not found at %s\n' "$BIN_DIR/OpenSim" >&2
  exit 1
fi

cd "$BIN_DIR"
exec ./OpenSim

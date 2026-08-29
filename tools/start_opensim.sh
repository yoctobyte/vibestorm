#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="$ROOT_DIR/local/opensim/runtime/bin"

# OpenSim is a framework-dependent net8.0 build (LastDotNetBuild.zip), so it
# needs a .NET 8 runtime. Ubuntu 26.04 ships only .NET 10, and rolling forward
# is not safe here: BinaryFormatter was removed in .NET 9 and OpenSim still
# relies on it for FlotsamAssetCache, KeyframeMotion and YEngine script state.
DOTNET_ROOT="${DOTNET_ROOT:-$HOME/.dotnet}"

if ! compgen -G "$DOTNET_ROOT/shared/Microsoft.NETCore.App/8.*" > /dev/null; then
  printf 'No .NET 8 runtime under %s\n' "$DOTNET_ROOT" >&2
  printf 'Install it with:\n' >&2
  printf '  curl -fsSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 8.0 --runtime dotnet\n' >&2
  exit 1
fi

export DOTNET_ROOT
export PATH="$DOTNET_ROOT:$PATH"

if [[ ! -x "$BIN_DIR/OpenSim" ]]; then
  printf 'OpenSim binary not found at %s\n' "$BIN_DIR/OpenSim" >&2
  exit 1
fi

cd "$BIN_DIR"
exec ./OpenSim "$@"

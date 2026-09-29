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

# OpenSim.log had reached 2.3 GB on 2026-09-11, on a root filesystem with
# 12 GB free. Essentially all of it -- 2,875,948 copies -- was one stack trace:
#
#   ERROR Command error: System.InvalidOperationException: Cannot see if a key
#   has been pressed when either application does not have a console or when
#   console input has been redirected from a file.
#     at System.Console.get_KeyAvailable()
#     at OpenSim.Framework.Console.LocalConsole.ReadLine(...)
#     at OpenSim.Framework.Console.CommandConsole.Prompt()
#
# `Application.Main` prompts whether or not there is a terminal to prompt at,
# so a run started detached logs a four-line trace and goes round again. It
# does not throttle. Two copies per two milliseconds were measured.
#
# Rotated rather than prevented, deliberately: the loop needs a fix inside
# OpenSim's console handling, this script cannot make a terminal appear, and a
# sim that fills the disk is a much worse failure than one that starts with a
# rotated log. The threshold is well above anything a normal session writes.
LOG_FILE="$BIN_DIR/OpenSim.log"
MAX_LOG_BYTES=$((256 * 1024 * 1024))
# Disk usage, not apparent size, and the same measure in the test and in the
# message. The first version of this guard tested `stat -c %s` and reported
# `du -h`, which are the same number for every file except a sparse one -- and
# truncating this log on 2026-09-11 while the simulator held it open made it
# exactly that. The writer kept appending at its old offset, so the file has
# read 2.39 GB to `ls` and 76 KB to `du` ever since, a factor of thirty
# thousand. Against that file the old guard would have rotated, and printed
# "OpenSim.log is 76K" as its reason for doing so.
#
# `%b` is in units of `%B`, so the product is what the filesystem has actually
# allocated -- which is what a guard against filling the disk should be
# reading.
if [[ -f "$LOG_FILE" ]]; then
  log_disk_bytes=$(( $(stat -c %b "$LOG_FILE") * $(stat -c %B "$LOG_FILE") ))
  if (( log_disk_bytes > MAX_LOG_BYTES )); then
    printf 'OpenSim.log is using %s of disk; keeping the last 3000 lines and truncating.\n' \
      "$(numfmt --to=iec "$log_disk_bytes" 2>/dev/null || printf '%s bytes' "$log_disk_bytes")" >&2
    tail -n 3000 "$LOG_FILE" | gzip -9 > "$LOG_FILE.rotated-$(date +%Y-%m-%d).gz" || true
    # Truncated rather than removed: a running OpenSim holds this open, and an
    # unlink would free nothing until the process exits.
    : > "$LOG_FILE"
  fi
fi

cd "$BIN_DIR"
exec ./OpenSim "$@"

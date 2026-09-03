#!/usr/bin/env bash
#
# Launch the Vibestorm viewer window.
#
# This exists because `./local.sh` and friends default to the `session`
# command -- a headless 60-second protocol run -- so the obvious way to "just
# start it" gives you no window and exits on its own. This script only ever
# starts a viewer, and it stays open until you close it.
#
#   ./gui.sh                 3D viewer on the local OpenSim
#   ./gui.sh sl              3D viewer on Second Life, at your home location
#   ./gui.sh opengrid        3D viewer on OSgrid
#   ./gui.sh local --2d      the 2D bird's-eye viewer instead
#   ./gui.sh -- --camera-sweep --width 1600
#
# Anything after `--` is passed through to the viewer untouched.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*" >&2
}

# ---------------------------------------------------------------- arguments

GRID="local"
VIEWER_COMMAND="viewer3d"
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    local|localhost)
      GRID="local"; shift ;;
    sl|secondlife|second-life)
      GRID="sl"; shift ;;
    opengrid|osgrid)
      GRID="opengrid"; shift ;;
    --2d)
      VIEWER_COMMAND="viewer"; shift ;;
    --3d)
      VIEWER_COMMAND="viewer3d"; shift ;;
    -h|--help)
      # Print the header comment, stopping at the first line that is not one.
      # A fixed line range goes stale the moment the header is edited.
      awk 'NR>2 { if ($0 !~ /^#/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
      exit 0 ;;
    --)
      shift; PASSTHROUGH+=("$@"); break ;;
    *)
      # Unknown leading token: treat it as a viewer argument rather than
      # guessing at a grid name we do not recognise.
      PASSTHROUGH+=("$1"); shift ;;
  esac
done

# ------------------------------------------------------------ prerequisites

# `uv` lives in ~/.local/bin, which a non-login shell may not have on PATH.
# Without it run.sh silently falls back to the system python, which has none
# of the dependencies, and the failure looks like a missing module rather than
# a missing PATH entry.
if ! command -v uv >/dev/null 2>&1 && [[ -x "$HOME/.local/bin/uv" ]]; then
  PATH="$HOME/.local/bin:$PATH"
  export PATH
fi
command -v uv >/dev/null 2>&1 || note "warning: uv not found; falling back to system python3, which is unlikely to have the viewer dependencies"

if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  die "No DISPLAY or WAYLAND_DISPLAY set, so there is nowhere to open a window.
If you meant a headless protocol run, use:  ./local.sh session"
fi

case "$GRID" in
  local)
    PROFILE="tester"
    # Fail here with something actionable rather than inside a login timeout.
    if ! (exec 3<>/dev/tcp/127.0.0.1/9000) 2>/dev/null; then
      die "Nothing is listening on 127.0.0.1:9000, so the local sim is not running.
Start it with:  ./opensim.sh
Or in its own tmux session:  tmux new-session -d -s opensim './opensim.sh'
Then attach with:  tmux attach -t opensim"
    fi
    ;;
  opengrid)
    PROFILE="osgrid"
    ;;
  sl)
    PROFILE="sl"
    # Second Life defaults to the home location; see run.sh. Said out loud
    # because "last" is the more common viewer default and the difference
    # matters when you are testing where you land.
    note "Second Life: starting at your home location (set VIBESTORM_START_LOCATION to override)."
    ;;
esac

export VIBESTORM_GRID_MODE="${VIBESTORM_GRID_MODE:-$GRID}"

note "Launching ${VIEWER_COMMAND} on ${GRID} (profile: ${PROFILE}). Close the window to exit."

exec "$ROOT_DIR/run.sh" "$PROFILE" "$VIEWER_COMMAND" "${PASSTHROUGH[@]+"${PASSTHROUGH[@]}"}"

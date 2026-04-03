#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

LOGIN_URI="${VIBESTORM_LOGIN_URI:-http://127.0.0.1:9000/}"
FIRST_NAME="${VIBESTORM_FIRST_NAME:-Vibestorm}"
LAST_NAME="${VIBESTORM_LAST_NAME:-Admin}"
PASSWORD="${VIBESTORM_PASSWORD:-changeme123}"
START_LOCATION="${VIBESTORM_START_LOCATION:-uri:Vibestorm Test&128&128&25}"
SESSION_DURATION="${VIBESTORM_SESSION_DURATION:-60}"
AGENT_UPDATE_INTERVAL="${VIBESTORM_AGENT_UPDATE_INTERVAL:-1.0}"
SPAWN_CUBE="${VIBESTORM_SPAWN_CUBE:-0}"

usage() {
  cat <<EOF
Usage: ./run.sh [command] [extra args...]

Commands:
  help         Show this help text
  opensim      Launch the bundled local OpenSim host
  bootstrap    Run XML-RPC login bootstrap
  caps         Resolve EventQueueGet and SimulatorFeatures capabilities
  eventq       Poll EventQueueGet once
  udp          Send the one-shot UseCircuitCode UDP probe
  handshake    Run the handshake probe
  session      Run the bounded live UDP session loop
  test         Run the unit test suite

Defaults come from the local OpenSim notes and can be overridden with env vars:
  VIBESTORM_LOGIN_URI
  VIBESTORM_FIRST_NAME
  VIBESTORM_LAST_NAME
  VIBESTORM_PASSWORD
  VIBESTORM_START_LOCATION
  VIBESTORM_SESSION_DURATION
  VIBESTORM_AGENT_UPDATE_INTERVAL
  VIBESTORM_SPAWN_CUBE

Examples:
  ./run.sh opensim
  ./run.sh session
  ./run.sh bootstrap
  VIBESTORM_SESSION_DURATION=15 ./run.sh session
  VIBESTORM_SPAWN_CUBE=1 ./run.sh session
  VIBESTORM_PASSWORD=secret ./run.sh handshake
EOF
}

python_runner() {
  if command -v uv >/dev/null 2>&1; then
    uv run --python python3 "$@"
  else
    PYTHONPATH=src python3 "$@"
  fi
}

cli_base_args=(
  --login-uri "$LOGIN_URI"
  --first "$FIRST_NAME"
  --last "$LAST_NAME"
  --password "$PASSWORD"
  --start "$START_LOCATION"
)

command="${1:-session}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$command" in
  help|-h|--help)
    usage
    ;;
  opensim)
    cd "$ROOT_DIR"
    exec "$ROOT_DIR/tools/start_opensim.sh" "$@"
    ;;
  bootstrap)
    cd "$ROOT_DIR"
    python_runner -m vibestorm.app.cli login-bootstrap "${cli_base_args[@]}" "$@"
    ;;
  caps)
    cd "$ROOT_DIR"
    python_runner -m vibestorm.app.cli resolve-seed-caps "${cli_base_args[@]}" EventQueueGet SimulatorFeatures "$@"
    ;;
  eventq)
    cd "$ROOT_DIR"
    python_runner -m vibestorm.app.cli event-queue-once "${cli_base_args[@]}" "$@"
    ;;
  udp)
    cd "$ROOT_DIR"
    python_runner -m vibestorm.app.cli udp-probe "${cli_base_args[@]}" "$@"
    ;;
  handshake)
    cd "$ROOT_DIR"
    python_runner -m vibestorm.app.cli handshake-probe "${cli_base_args[@]}" "$@"
    ;;
  session)
    cd "$ROOT_DIR"
    session_args=()
    if [[ "$SPAWN_CUBE" == "1" ]]; then
      session_args+=(--spawn-cube)
    fi
    python_runner -m vibestorm.app.cli session-run \
      "${cli_base_args[@]}" \
      --duration "$SESSION_DURATION" \
      --agent-update-interval "$AGENT_UPDATE_INTERVAL" \
      "${session_args[@]}" \
      "$@"
    ;;
  test)
    cd "$ROOT_DIR"
    python_runner -m unittest discover -s test -v "$@"
    ;;
  *)
    printf 'Unknown command: %s\n\n' "$command" >&2
    usage >&2
    exit 2
    ;;
esac

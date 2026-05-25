#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

is_command() {
  case "$1" in
    help|-h|--help|login|login-show|login-reset|opensim|bootstrap|caps|eventq|udp|handshake|session|upload-smoke|console|viewer|viewer3d|test|fixtures|unknowns)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

command="${1:-session}"
if [[ $# -gt 0 ]]; then
  shift
fi

LOGIN_PROFILE_NAME="${VIBESTORM_LOGIN_PROFILE_NAME:-default}"
if ! is_command "$command"; then
  LOGIN_PROFILE_NAME="$command"
  command="${1:-session}"
  if [[ $# -gt 0 ]]; then
    shift
  fi
fi

if [[ "$LOGIN_PROFILE_NAME" == "default" ]]; then
  DEFAULT_LOGIN_PROFILE="$ROOT_DIR/local/vibestorm-login.env"
else
  DEFAULT_LOGIN_PROFILE="$ROOT_DIR/local/vibestorm-login-$LOGIN_PROFILE_NAME.env"
fi
LOGIN_PROFILE="${VIBESTORM_LOGIN_PROFILE:-$DEFAULT_LOGIN_PROFILE}"
export VIBESTORM_LOGIN_PROFILE_NAME="$LOGIN_PROFILE_NAME"
export VIBESTORM_LOGIN_PROFILE="$LOGIN_PROFILE"

ENV_LOGIN_URI="${VIBESTORM_LOGIN_URI:-}"
ENV_FIRST_NAME="${VIBESTORM_FIRST_NAME:-}"
ENV_LAST_NAME="${VIBESTORM_LAST_NAME:-}"
ENV_PASSWORD="${VIBESTORM_PASSWORD:-}"
ENV_START_LOCATION="${VIBESTORM_START_LOCATION:-}"
ENV_GRID_MODE="${VIBESTORM_GRID_MODE:-}"

if [[ -f "$LOGIN_PROFILE" ]]; then
  chmod go-rwx "$LOGIN_PROFILE" 2>/dev/null || true
  # shellcheck disable=SC1090
  source "$LOGIN_PROFILE"
fi

if [[ "$LOGIN_PROFILE_NAME" == "tester" && ! -f "$LOGIN_PROFILE" ]]; then
  VIBESTORM_LOGIN_URI="${VIBESTORM_LOGIN_URI:-http://127.0.0.1:9000/}"
  VIBESTORM_FIRST_NAME="${VIBESTORM_FIRST_NAME:-Vibestorm}"
  VIBESTORM_LAST_NAME="${VIBESTORM_LAST_NAME:-Tester}"
  VIBESTORM_START_LOCATION="${VIBESTORM_START_LOCATION:-uri:Vibestorm Test&128&128&25}"
fi

LOGIN_URI="${ENV_LOGIN_URI:-${VIBESTORM_LOGIN_URI:-}}"
FIRST_NAME="${ENV_FIRST_NAME:-${VIBESTORM_FIRST_NAME:-}}"
LAST_NAME="${ENV_LAST_NAME:-${VIBESTORM_LAST_NAME:-}}"
PASSWORD="${ENV_PASSWORD:-${VIBESTORM_PASSWORD:-}}"
START_LOCATION="${ENV_START_LOCATION:-${VIBESTORM_START_LOCATION:-}}"
GRID_MODE="${ENV_GRID_MODE:-${VIBESTORM_GRID_MODE:-}}"
if [[ -z "$GRID_MODE" ]]; then
  case "$LOGIN_PROFILE_NAME" in
    tester|local|localhost)
      GRID_MODE="local"
      ;;
    osgrid|opengrid)
      GRID_MODE="opengrid"
      ;;
    sl|secondlife|second-life)
      GRID_MODE="sl"
      ;;
  esac
fi
if [[ -z "$GRID_MODE" && -n "$LOGIN_URI" ]]; then
  case "$LOGIN_URI" in
    *login.agni.lindenlab.com*)
      GRID_MODE="sl"
      ;;
    *login.osgrid.org*)
      GRID_MODE="opengrid"
      ;;
    http://127.0.0.1:*|http://localhost:*)
      GRID_MODE="local"
      ;;
  esac
fi
case "$GRID_MODE" in
  localhost)
    GRID_MODE="local"
    ;;
  osgrid)
    GRID_MODE="opengrid"
    ;;
esac
case "$GRID_MODE" in
  local)
    LOGIN_URI="${LOGIN_URI:-http://127.0.0.1:9000/}"
    START_LOCATION="${START_LOCATION:-uri:Vibestorm Test&128&128&25}"
    ;;
  opengrid)
    LOGIN_URI="${LOGIN_URI:-http://login.osgrid.org/}"
    START_LOCATION="${START_LOCATION:-last}"
    ;;
  sl)
    LOGIN_URI="${LOGIN_URI:-https://login.agni.lindenlab.com/cgi-bin/login.cgi}"
    START_LOCATION="${START_LOCATION:-last}"
    ;;
esac
SESSION_DURATION="${VIBESTORM_SESSION_DURATION:-60}"
AGENT_UPDATE_INTERVAL="${VIBESTORM_AGENT_UPDATE_INTERVAL:-1.0}"
CAMERA_SWEEP="${VIBESTORM_CAMERA_SWEEP:-0}"
SPAWN_CUBE="${VIBESTORM_SPAWN_CUBE:-0}"
CAPTURE_DIR="${VIBESTORM_CAPTURE_DIR:-}"
CAPTURE_MODE="${VIBESTORM_CAPTURE_MODE:-smart}"

usage() {
  cat <<EOF
Usage: ./run.sh [profile] [command] [extra args...]

Commands:
  help         Show this help text
  login        Create or replace the ignored local login profile
  login-show   Show the current login profile path and non-secret fields
  login-reset  Delete the ignored local login profile
  opensim      Launch the bundled local OpenSim host
  bootstrap    Run XML-RPC login bootstrap
  caps         Resolve EventQueueGet and SimulatorFeatures capabilities
  eventq       Poll EventQueueGet once
  udp          Send the one-shot UseCircuitCode UDP probe
  handshake    Run the handshake probe
  session      Run the bounded live UDP session loop
  upload-smoke Upload a one-space text/notecard item and verify FetchInventory2 sees it
  console      Run an indefinite live session, streaming events to stdout (Ctrl+C to stop)
  viewer       Run the pygame 2D bird's-eye viewer
  viewer3d     Run the 3D viewer fork (currently identical to viewer; 3D work in progress)
  fixtures     Rebuild the structured fixture inventory/backlog
  test         Run the unit test suite

Login commands use env vars first, then an ignored local profile at:
  $LOGIN_PROFILE

Named profiles use ignored files like:
  local/vibestorm-login-tester.env

The built-in local `tester` profile uses the local OpenSim test account if no
profile file exists yet. Explicit env vars still override it.

If login details are still missing and stdin is interactive, this script prompts
and stores them there with mode 600. This is local-file storage, not OS keyring
encryption, so use it for dev/test credentials.

Relevant env vars:
  VIBESTORM_LOGIN_URI
  VIBESTORM_FIRST_NAME
  VIBESTORM_LAST_NAME
  VIBESTORM_PASSWORD
  VIBESTORM_START_LOCATION
  VIBESTORM_SESSION_DURATION
  VIBESTORM_AGENT_UPDATE_INTERVAL
  VIBESTORM_CAMERA_SWEEP
  VIBESTORM_SPAWN_CUBE
  VIBESTORM_CAPTURE_DIR
  VIBESTORM_CAPTURE_MODE
Examples:
  ./run.sh opensim
  ./run.sh login
  ./run.sh login-show
  ./run.sh login-reset
  ./run.sh session
  ./run.sh tester session
  ./run.sh tester upload-smoke
  ./run.sh tester login-show
  ./run.sh session 180 --verbose
  ./run.sh viewer
  ./run.sh bootstrap
  VIBESTORM_FIRST_NAME=... VIBESTORM_LAST_NAME=... VIBESTORM_PASSWORD=... ./run.sh session
  VIBESTORM_CAMERA_SWEEP=1 ./run.sh session
  VIBESTORM_SPAWN_CUBE=1 ./run.sh session
  VIBESTORM_CAPTURE_DIR=test/fixtures/live ./run.sh session --capture-message ObjectUpdate
  VIBESTORM_CAPTURE_DIR=test/fixtures/live VIBESTORM_CAPTURE_MODE=all ./run.sh session --capture-message ObjectUpdate
  ./run.sh unknowns
  ./run.sh handshake
EOF
}

shell_quote() {
  printf '%q' "$1"
}

clear_login_values() {
  LOGIN_URI=""
  FIRST_NAME=""
  LAST_NAME=""
  PASSWORD=""
  START_LOCATION=""
}

show_login_profile() {
  printf 'login_profile_name=%s\n' "$LOGIN_PROFILE_NAME"
  printf 'grid_mode=%s\n' "${GRID_MODE:-auto}"
  printf 'login_profile=%s\n' "$LOGIN_PROFILE"
  if [[ -f "$LOGIN_PROFILE" ]]; then
    printf 'login_profile_exists=1\n'
    printf 'login_profile_mode=%s\n' "$(stat -c '%a' "$LOGIN_PROFILE" 2>/dev/null || printf '?')"
  else
    printf 'login_profile_exists=0\n'
  fi
  printf 'login_uri=%s\n' "${LOGIN_URI:-}"
  printf 'first_name=%s\n' "${FIRST_NAME:-}"
  printf 'last_name=%s\n' "${LAST_NAME:-}"
  printf 'start_location=%s\n' "${START_LOCATION:-}"
  if [[ -n "$PASSWORD" ]]; then
    printf 'password=set\n'
  else
    printf 'password=missing\n'
  fi
}

write_login_profile() {
  mkdir -p "$(dirname "$LOGIN_PROFILE")"
  umask 077
  {
    printf '# Vibestorm local login profile. Ignored by git. Keep mode 600.\n'
    printf 'VIBESTORM_LOGIN_URI=%s\n' "$(shell_quote "$LOGIN_URI")"
    printf 'VIBESTORM_FIRST_NAME=%s\n' "$(shell_quote "$FIRST_NAME")"
    printf 'VIBESTORM_LAST_NAME=%s\n' "$(shell_quote "$LAST_NAME")"
    printf 'VIBESTORM_PASSWORD=%s\n' "$(shell_quote "$PASSWORD")"
    printf 'VIBESTORM_START_LOCATION=%s\n' "$(shell_quote "$START_LOCATION")"
  } > "$LOGIN_PROFILE"
  chmod 600 "$LOGIN_PROFILE"
}

apply_login_preset() {
  case "$1" in
    localhost|local|"")
      LOGIN_URI="${LOGIN_URI:-http://127.0.0.1:9000/}"
      START_LOCATION="${START_LOCATION:-uri:Vibestorm Test&128&128&25}"
      ;;
    opengrid|osgrid)
      LOGIN_URI="${LOGIN_URI:-http://login.osgrid.org/}"
      START_LOCATION="${START_LOCATION:-last}"
      ;;
    sl|secondlife|second-life)
      LOGIN_URI="${LOGIN_URI:-https://login.agni.lindenlab.com/cgi-bin/login.cgi}"
      START_LOCATION="${START_LOCATION:-last}"
      ;;
    custom)
      ;;
    *)
      printf 'Unknown sim location preset: %s\n' "$1" >&2
      printf 'Use localhost, opengrid, sl, or custom.\n' >&2
      exit 2
      ;;
  esac
}

prompt_login() {
  local force="${1:-0}"
  if [[ "$command" == "viewer" || "$command" == "viewer3d" ]]; then
    return
  fi
  if [[ "$force" != "1" && -n "$FIRST_NAME" && -n "$LAST_NAME" && -n "$PASSWORD" && -n "$LOGIN_URI" && -n "$START_LOCATION" ]]; then
    return
  fi
  if [[ ! -t 0 ]]; then
    cat >&2 <<EOF
Login details are required for '$command', and stdin is not interactive.
Set VIBESTORM_FIRST_NAME, VIBESTORM_LAST_NAME, VIBESTORM_PASSWORD, and optionally
VIBESTORM_LOGIN_URI / VIBESTORM_START_LOCATION, or create:
  $LOGIN_PROFILE
EOF
    exit 2
  fi

  if [[ "$force" == "1" ]]; then
    clear_login_values
  fi

  printf 'Vibestorm login setup\n'
  local default_sim="${GRID_MODE:-local}"
  if [[ "$default_sim" == "local" ]]; then
    default_sim="localhost"
  fi
  printf 'Sim location [localhost/opengrid/sl/custom] (default %s): ' "$default_sim"
  read -r sim_location
  sim_location="${sim_location:-$default_sim}"
  apply_login_preset "$sim_location"

  if [[ "$sim_location" == "custom" ]]; then
    printf 'Login URI: '
    read -r LOGIN_URI
    printf 'Start location (default last): '
    read -r START_LOCATION
    START_LOCATION="${START_LOCATION:-last}"
  fi

  if [[ -z "$FIRST_NAME" ]]; then
    printf 'First name: '
    read -r FIRST_NAME
  fi
  if [[ -z "$LAST_NAME" ]]; then
    printf 'Last name: '
    read -r LAST_NAME
  fi
  if [[ -z "$PASSWORD" ]]; then
    printf 'Password: '
    read -rs PASSWORD
    printf '\n'
  fi

  if [[ -z "$FIRST_NAME" || -z "$LAST_NAME" || -z "$PASSWORD" || -z "$LOGIN_URI" || -z "$START_LOCATION" ]]; then
    printf 'Missing login details; aborting.\n' >&2
    exit 2
  fi

  printf 'Store these login details in %s? [Y/n]: ' "$LOGIN_PROFILE"
  read -r save_login
  if [[ ! "$save_login" =~ ^[Nn]$ ]]; then
    write_login_profile
    printf 'Stored login profile at %s with mode 600.\n' "$LOGIN_PROFILE"
  fi
}

prepare_login() {
  prompt_login 0
  cli_base_args=(
    --login-uri "$LOGIN_URI"
    --first "$FIRST_NAME"
    --last "$LAST_NAME"
    --password "$PASSWORD"
    --start "$START_LOCATION"
  )
}

sl_confirmation_required() {
  [[ "$GRID_MODE" == "sl" ]] || return 1
  case "$command" in
    eventq|udp|handshake|session|upload-smoke|console|viewer|viewer3d)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

confirm_sl_command() {
  sl_confirmation_required || return 0
  if [[ "${VIBESTORM_SL_CONFIRM:-}" == "1" || "${VIBESTORM_SL_CONFIRM:-}" == "yes" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    cat >&2 <<EOF
Refusing to run '$command' against Second Life without explicit confirmation.
Set VIBESTORM_SL_CONFIRM=1 or use an interactive terminal.
EOF
    exit 2
  fi
  cat >&2 <<EOF

Second Life safety check
  profile: ${LOGIN_PROFILE_NAME}
  login_uri: ${LOGIN_URI}
  command: ${command}

SL mode disables automatic baked-texture uploads, but this command will still
connect a real account to Agni. Deliberate user actions such as chat, teleport,
or upload commands remain possible.

Type SL to continue:
EOF
  local answer
  read -r answer
  if [[ "$answer" != "SL" ]]; then
    printf 'Aborted.\n' >&2
    exit 2
  fi
}

run_login_command() {
  local runner="$1"
  local status=0
  shift
  prepare_login
  confirm_sl_command
  cd "$ROOT_DIR"
  if "$runner" "$@"; then
    return 0
  else
    status=$?
  fi
  if [[ ! -t 0 ]]; then
    return "$status"
  fi
  if [[ "$status" != "10" ]]; then
    return "$status"
  fi
  if [[ "$command" == "viewer" || "$command" == "viewer3d" ]]; then
    return "$status"
  fi
  printf '\nLogin command failed. Re-enter saved login details and retry once? [y/N]: ' >&2
  local retry_login
  read -r retry_login
  if [[ ! "$retry_login" =~ ^[Yy]$ ]]; then
    return "$status"
  fi
  prompt_login 1
  prepare_login
  "$runner" "$@"
}

python_runner() {
  if command -v uv >/dev/null 2>&1; then
    uv run --python python3 "$@"
  else
    PYTHONPATH=src python3 "$@"
  fi
}

viewer_python_runner() {
  if command -v uv >/dev/null 2>&1; then
    uv run --extra viewer --python python3 "$@"
  else
    PYTHONPATH=src python3 "$@"
  fi
}

cli_base_args=()

do_bootstrap() {
  python_runner -m vibestorm.app.cli login-bootstrap "${cli_base_args[@]}" "$@"
}

do_caps() {
  python_runner -m vibestorm.app.cli resolve-seed-caps "${cli_base_args[@]}" EventQueueGet SimulatorFeatures "$@"
}

do_eventq() {
  python_runner -m vibestorm.app.cli event-queue-once "${cli_base_args[@]}" "$@"
}

do_udp() {
  python_runner -m vibestorm.app.cli udp-probe "${cli_base_args[@]}" "$@"
}

do_handshake() {
  python_runner -m vibestorm.app.cli handshake-probe "${cli_base_args[@]}" "$@"
}

do_session() {
  python_runner -m vibestorm.app.cli session-run \
    "${cli_base_args[@]}" \
    --duration "$duration" \
    --agent-update-interval "$AGENT_UPDATE_INTERVAL" \
    "${session_args[@]}" \
    "$@"
}

do_upload_smoke() {
  python_runner -m vibestorm.app.cli upload-empty-text-smoke "${cli_base_args[@]}" "$@"
}

do_console() {
  python_runner -m vibestorm.app.cli console \
    "${cli_base_args[@]}" \
    --agent-update-interval "$AGENT_UPDATE_INTERVAL" \
    "${console_args[@]}" \
    "$@"
}

do_viewer() {
  viewer_python_runner -m vibestorm.viewer.app \
    "${cli_base_args[@]}" \
    --agent-update-interval "$AGENT_UPDATE_INTERVAL" \
    "${viewer_args[@]}" \
    "$@"
}

do_viewer3d() {
  viewer_python_runner -m vibestorm.viewer3d.app \
    "${cli_base_args[@]}" \
    --agent-update-interval "$AGENT_UPDATE_INTERVAL" \
    "${viewer_args[@]}" \
    "$@"
}

case "$command" in
  help|-h|--help)
    usage
    ;;
  login)
    prompt_login 1
    ;;
  login-show)
    show_login_profile
    ;;
  login-reset)
    if [[ -f "$LOGIN_PROFILE" ]]; then
      rm -f "$LOGIN_PROFILE"
      printf 'Removed login profile: %s\n' "$LOGIN_PROFILE"
    else
      printf 'No login profile found at: %s\n' "$LOGIN_PROFILE"
    fi
    ;;
  opensim)
    cd "$ROOT_DIR"
    exec "$ROOT_DIR/tools/start_opensim.sh" "$@"
    ;;
  bootstrap)
    run_login_command do_bootstrap "$@"
    ;;
  caps)
    run_login_command do_caps "$@"
    ;;
  eventq)
    run_login_command do_eventq "$@"
    ;;
  udp)
    run_login_command do_udp "$@"
    ;;
  handshake)
    run_login_command do_handshake "$@"
    ;;
  session)
    prepare_login
    cd "$ROOT_DIR"
    duration="$SESSION_DURATION"
    if [[ $# -gt 0 && "${1:-}" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
      duration="$1"
      shift
    fi
    session_args=()
    if [[ "$CAMERA_SWEEP" == "1" ]]; then
      session_args+=(--camera-sweep)
    fi
    if [[ "$SPAWN_CUBE" == "1" ]]; then
      session_args+=(--spawn-cube)
    fi
    if [[ "$GRID_MODE" == "sl" ]]; then
      session_args+=(--no-auto-bake-upload)
    fi
    if [[ -n "$CAPTURE_DIR" ]]; then
      session_args+=(--capture-dir "$CAPTURE_DIR")
    fi
    session_args+=(--capture-mode "$CAPTURE_MODE")
    run_login_command do_session "$@"
    ;;
  upload-smoke)
    run_login_command do_upload_smoke "$@"
    ;;
  console)
    prepare_login
    cd "$ROOT_DIR"
    console_args=()
    if [[ "$CAMERA_SWEEP" == "1" ]]; then
      console_args+=(--camera-sweep)
    fi
    if [[ "$GRID_MODE" == "sl" ]]; then
      console_args+=(--no-auto-bake-upload)
    fi
    run_login_command do_console "$@"
    ;;
  viewer)
    prepare_login
    cd "$ROOT_DIR"
    viewer_args=()
    if [[ "$CAMERA_SWEEP" == "1" ]]; then
      viewer_args+=(--camera-sweep)
    fi
    if [[ "$GRID_MODE" == "sl" ]]; then
      viewer_args+=(--no-auto-bake-upload)
    fi
    run_login_command do_viewer "$@"
    ;;
  viewer3d)
    prepare_login
    cd "$ROOT_DIR"
    viewer_args=()
    if [[ "$CAMERA_SWEEP" == "1" ]]; then
      viewer_args+=(--camera-sweep)
    fi
    if [[ "$GRID_MODE" == "sl" ]]; then
      viewer_args+=(--no-auto-bake-upload)
    fi
    run_login_command do_viewer3d "$@"
    ;;
  test)
    cd "$ROOT_DIR"
    python_runner -m unittest discover -s test -v "$@"
    ;;
  fixtures)
    cd "$ROOT_DIR"
    python_runner tools/build_fixture_inventory.py "$@"
    ;;
  unknowns)
    cd "$ROOT_DIR"
    python_runner -m vibestorm.app.cli unknowns-report "$@"
    ;;
  *)
    printf 'Unknown command: %s\n\n' "$command" >&2
    usage >&2
    exit 2
    ;;
esac

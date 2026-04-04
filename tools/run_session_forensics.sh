#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DURATION="${1:-180}"
if [[ "$DURATION" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  shift || true
else
  DURATION="180"
fi

CAPTURE_DIR="${VIBESTORM_CAPTURE_DIR:-test/fixtures/live}"
CAPTURE_MODE="${VIBESTORM_CAPTURE_MODE:-all}"
CAMERA_SWEEP="${VIBESTORM_CAMERA_SWEEP:-1}"
OUTPUT_DIR="${VIBESTORM_FORENSICS_OUTPUT_DIR:-local/session-reports}"

mkdir -p "$OUTPUT_DIR"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_path="$OUTPUT_DIR/session-forensics-$timestamp.txt"

session_cmd=(
  ./run.sh
  session
  "$DURATION"
  --capture-message
  ObjectUpdate
  "$@"
)

{
  echo "timestamp_utc=$timestamp"
  echo "cwd=$ROOT_DIR"
  echo "capture_dir=$CAPTURE_DIR"
  echo "capture_mode=$CAPTURE_MODE"
  echo "camera_sweep=$CAMERA_SWEEP"
  echo "output_file=$output_path"
  echo
  echo "\$ VIBESTORM_CAMERA_SWEEP=$CAMERA_SWEEP VIBESTORM_CAPTURE_DIR=$CAPTURE_DIR VIBESTORM_CAPTURE_MODE=$CAPTURE_MODE ${session_cmd[*]}"
  VIBESTORM_CAMERA_SWEEP="$CAMERA_SWEEP" \
  VIBESTORM_CAPTURE_DIR="$CAPTURE_DIR" \
  VIBESTORM_CAPTURE_MODE="$CAPTURE_MODE" \
  "${session_cmd[@]}"
  echo
  echo "\$ ./run.sh unknowns"
  ./run.sh unknowns
} | tee "$output_path"

echo
echo "wrote=$output_path"

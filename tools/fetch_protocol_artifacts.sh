#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT_DIR/third_party/secondlife"

mkdir -p "$OUT_DIR"

curl -L --fail --silent --show-error \
  "https://raw.githubusercontent.com/secondlife/master-message-template/master/message_template.msg" \
  -o "$OUT_DIR/message_template.msg"

curl -L --fail --silent --show-error \
  "https://raw.githubusercontent.com/secondlife/master-message-template/master/message_template.msg.sha1" \
  -o "$OUT_DIR/message_template.msg.sha1"

curl -L --fail --silent --show-error \
  "https://raw.githubusercontent.com/secondlife/viewer/develop/indra/newview/llviewerregion.cpp" \
  -o "$OUT_DIR/llviewerregion.cpp"

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$OUT_DIR/fetched_at_utc.txt"

printf 'Fetched protocol artifacts into %s\n' "$OUT_DIR"

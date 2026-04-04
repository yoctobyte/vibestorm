#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CAPTURE_HOST="${VIBESTORM_CAPTURE_HOST:-127.0.0.1}"
if [[ -n "${VIBESTORM_CAPTURE_INTERFACE:-}" ]]; then
    CAPTURE_INTERFACE="${VIBESTORM_CAPTURE_INTERFACE}"
elif [[ "$CAPTURE_HOST" == "127.0.0.1" || "$CAPTURE_HOST" == "localhost" ]]; then
    CAPTURE_INTERFACE="lo"
else
    CAPTURE_INTERFACE="any"
fi
CAPTURE_DIR="${VIBESTORM_VIEWER_CAPTURE_DIR:-local/viewer-captures}"
CAPTURE_LABEL="${1:-viewer-session}"
TIMESTAMP_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
SAFE_LABEL="$(printf '%s' "$CAPTURE_LABEL" | tr ' /:' '---')"
BASE_PATH="${CAPTURE_DIR}/${TIMESTAMP_UTC}-${SAFE_LABEL}"
PCAP_PATH="${BASE_PATH}.pcap"
META_PATH="${BASE_PATH}.meta.txt"
TEXT_PATH="${BASE_PATH}.tcpdump.txt"
PID_PATH="${BASE_PATH}.pid"

mkdir -p "$CAPTURE_DIR"

BPF_FILTER="host ${CAPTURE_HOST} and (udp or tcp)"

cat >"$META_PATH" <<EOF
timestamp_utc=${TIMESTAMP_UTC}
cwd=${ROOT_DIR}
capture_host=${CAPTURE_HOST}
capture_interface=${CAPTURE_INTERFACE}
pcap_path=${PCAP_PATH}
text_path=${TEXT_PATH}
bpf_filter=${BPF_FILTER}
EOF

echo "timestamp_utc=${TIMESTAMP_UTC}"
echo "capture_host=${CAPTURE_HOST}"
echo "capture_interface=${CAPTURE_INTERFACE}"
echo "pcap_path=${PCAP_PATH}"
echo "text_path=${TEXT_PATH}"
echo "bpf_filter=${BPF_FILTER}"
echo
echo "starting tcpdump; press Ctrl-C after the viewer session is complete"

cleanup() {
    local status=0
    if [[ -f "$PID_PATH" ]]; then
        local pid
        pid="$(cat "$PID_PATH")"
        if kill -0 "$pid" 2>/dev/null; then
            kill -INT "$pid" 2>/dev/null || true
            wait "$pid" || status=$?
        fi
        rm -f "$PID_PATH"
    fi

    if [[ -f "$PCAP_PATH" ]]; then
        tcpdump -nn -tttt -r "$PCAP_PATH" >"$TEXT_PATH" 2>/dev/null || true
    fi

    echo
    echo "capture_complete=1"
    echo "pcap_path=${PCAP_PATH}"
    echo "text_path=${TEXT_PATH}"
    echo "meta_path=${META_PATH}"
    exit "$status"
}

trap cleanup INT TERM

tcpdump -i "$CAPTURE_INTERFACE" -nn -s 0 -w "$PCAP_PATH" "$BPF_FILTER" &
TCPDUMP_PID=$!
echo "$TCPDUMP_PID" >"$PID_PATH"

wait "$TCPDUMP_PID"
rm -f "$PID_PATH"

tcpdump -nn -tttt -r "$PCAP_PATH" >"$TEXT_PATH" 2>/dev/null || true

echo
echo "capture_complete=1"
echo "pcap_path=${PCAP_PATH}"
echo "text_path=${TEXT_PATH}"
echo "meta_path=${META_PATH}"

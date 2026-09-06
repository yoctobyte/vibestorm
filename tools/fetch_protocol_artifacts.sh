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

# Deliberately not fetched: indra/newview/llviewerregion.cpp, or any other
# viewer implementation source.
#
# AGENTS.md says no viewer implementation source is ever consulted, for either
# repo, and that the claim "is retroactively destroyed the moment it stops
# being true -- including on work that never ships". A copy of it sitting in
# the tree is a claim guarded by nothing but everyone's memory of the rule, and
# it was here from the initial import, before the rule existed. It is gone, and
# this comment is here so a later pass does not helpfully put it back.
#
# It was also the one LGPL-2.1-only file in a repository that has no license of
# its own yet -- see docs/publishing-checklist.md. The message template above
# is a protocol *definition*, published by Linden Lab for exactly this purpose,
# and is a different kind of thing.

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$OUT_DIR/fetched_at_utc.txt"

printf 'Fetched protocol artifacts into %s\n' "$OUT_DIR"

#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SELF_DIR/agent-lib.sh"

force=false
case "${1:-}" in
  --force) force=true ;;
  "") ;;
  *) echo "Usage: agent-handoff.sh [--force]" >&2; exit 2 ;;
esac

runtime="$("$SELF_DIR/agent-runtime.sh" status)"
state="$(awk -F= '$1=="RUNTIME_STATE"{print $2}' <<<"$runtime")"
remaining="$(awk -F= '$1=="REMAINING_MINUTES"{print $2}' <<<"$runtime")"
threshold="$(awk -F= '$1=="AUTO_HANDOFF_MINUTES"{print $2}' <<<"$runtime")"

if [[ "$state" == "UNKNOWN" || -z "$remaining" ]]; then
  echo "Runner lifecycle timer is unavailable; refusing to request a blind restart." >&2
  exit 4
fi

threshold="${threshold:-20}"
if [[ "$force" != "true" ]] && (( remaining > threshold )); then
  echo "Runner is still SAFE: ${remaining}m remain; automatic restart window begins at ${threshold}m." >&2
  exit 3
fi

"$SELF_DIR/agent-checkpoint.sh" agent-restart-request >/dev/null || true
request_file="$(agent_cache_dir)/handoff.request"
tmp="${request_file}.tmp.$$"
{
  echo "REQUESTED_AT=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo "REQUESTED_BY=agent"
  echo "REMAINING_MINUTES=$remaining"
} > "$tmp"
mv "$tmp" "$request_file"

echo "HANDOFF_REQUESTED=true"
echo "REMAINING_MINUTES=$remaining"
echo "MESSAGE=Clean runner restart requested. Keepalive will queue/confirm a successor and end this runner."

#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SELF_DIR/agent-lib.sh"

LOG_FILE="$(agent_log_file)"
EXPECTED_VERSION="$(agent_toolchain_version)"
state="$(agent_status_value STATUS)"
state="${state:-UNKNOWN}"
version="$(agent_status_value TOOLCHAIN_VERSION)"
pid="$(agent_status_value PID)"

if [[ "$state" == "RUNNING" && -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
  state="STALE"
elif [[ "$state" == "READY" ]]; then
  if [[ "$version" != "$EXPECTED_VERSION" ]] || ! agent_full_toolchain_ready; then
    state="OUTDATED"
  fi
fi

echo "AGENT_PREWARM=$state"
echo "TOOLCHAIN_VERSION=${version:-unknown}"
echo "EXPECTED_TOOLCHAIN_VERSION=$EXPECTED_VERSION"
echo "STARTED_AT=$(agent_status_value STARTED_AT)"
echo "FINISHED_AT=$(agent_status_value FINISHED_AT)"
echo "PROFILE=$(agent_status_value PROFILE)"
echo "PID=${pid:-}"

for pair in   "git:git" "gh:gh" "node:node" "npm:npm" "python:python3"   "rg:rg" "fd:fd" "jq:jq" "cmake:cmake" "ninja:ninja"   "gcc:gcc" "clang:clang" "gdb:gdb" "git_lfs:git-lfs"   "docker:docker" "ffmpeg:ffmpeg" "imagemagick:convert" "sqlite:sqlite3"   "shellcheck:shellcheck"; do
  name="${pair%%:*}"
  cmd="${pair#*:}"
  if command -v "$cmd" >/dev/null 2>&1; then
    printf '%s=READY\n' "${name^^}"
  else
    printf '%s=MISSING\n' "${name^^}"
  fi
done

if [[ "$state" == "FAILED" || "$state" == "STALE" || "$state" == "OUTDATED" ]]; then
  echo "--- prewarm log tail ---"
  tail -n 40 "$LOG_FILE" 2>/dev/null || true
fi

echo "=== GITHUB AUTH ==="
"$SELF_DIR/agent-github.sh" status || true

recovery_file="$(agent_cache_dir)/recovery.env"
if [[ -f "$recovery_file" ]]; then
  echo "=== AGENT RECOVERY ==="
  cat "$recovery_file"
fi

echo "=== RUNNER RUNTIME ==="
"$SELF_DIR/agent-runtime.sh" status

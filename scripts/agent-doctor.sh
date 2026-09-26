#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SELF_DIR/agent-lib.sh"

TARGET="${1:-$PWD}"
OS_NAME="unknown"
if [[ -r /etc/os-release ]]; then
  OS_NAME="$(awk -F= '$1=="PRETTY_NAME"{gsub(/^"|"$/, "", $2); print $2; exit}' /etc/os-release)"
fi

echo "=== AGENT RUNNER CONTEXT ==="
printf 'host=%s\n' "$(hostname)"
printf 'os=%s\n' "${OS_NAME:-unknown}"
printf 'cwd=%s\n' "$TARGET"
printf 'disk=%s\n' "$(df -h "$HOME" | awk 'NR==2 {print $3 "/" $2 " used=" $5}')"
toolset_status="$(python3 "$SELF_DIR/lab_toolset.py" validate)"
printf 'toolset=%s\n' "${toolset_status#TOOLSET=}"
printf 'toolchain_version=%s\n' "$(agent_toolchain_version)"
if agent_full_toolchain_ready; then
  echo "toolchain_ready=yes"
else
  echo "toolchain_ready=no"
  printf 'toolchain_missing=%s\n' "$(agent_missing_full_commands | paste -sd, -)"
fi
while IFS= read -r cmd; do
  path="$(command -v "$cmd" 2>/dev/null || true)"
  printf 'tool.%s=%s\n' "$cmd" "${path:-missing}"
done < <(agent_required_full_commands)

if command -v docker >/dev/null 2>&1; then
  echo "--- docker storage ---"
  python3 "$SELF_DIR/lab_docker_storage.py" status || true
fi

PROJECT_HOOK_REL="${AGENT_PROJECT_RUNNER:-.github/agent-lab/runner.sh}"
PROJECT_HOOK="$TARGET/$PROJECT_HOOK_REL"
if [[ -f "$PROJECT_HOOK" ]]; then
  echo "--- project contract ---"
  AGENT_PROJECT_ROOT="$TARGET" "$SELF_DIR/agent-project.sh" status "$TARGET" || true
fi

if [[ -d "$TARGET/.git" ]] || git -C "$TARGET" rev-parse --git-dir >/dev/null 2>&1; then
  echo "--- git ---"
  printf 'remote=%s\n' "$(git -C "$TARGET" remote get-url origin 2>/dev/null || echo none)"
  printf 'branch=%s\n' "$(git -C "$TARGET" branch --show-current 2>/dev/null || true)"
  printf 'head=%s\n' "$(git -C "$TARGET" rev-parse --short=12 HEAD)"
  git -C "$TARGET" log -3 --oneline --decorate
  echo "--- status ---"
  git -C "$TARGET" status --short --branch
  echo "--- top-level ---"
  find "$TARGET" -maxdepth 1 -mindepth 1 -printf '%f\n' 2>/dev/null | sort | sed -n '1,80p'
  if [[ -f "$TARGET/package.json" ]]; then
    echo "--- package scripts ---"
    node -e 'const p=require(process.argv[1]); console.log(JSON.stringify(p.scripts||{},null,2))' "$TARGET/package.json" 2>/dev/null || true
  fi
fi

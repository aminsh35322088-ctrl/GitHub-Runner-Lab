#!/usr/bin/env bash
set -Eeuo pipefail

TARGET="${1:-$PWD}"
echo "=== AGENT RUNNER CONTEXT ==="
printf 'host=%s\n' "$(hostname)"
printf 'os=%s\n' "$(. /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}")"
printf 'cwd=%s\n' "$TARGET"
printf 'disk=%s\n' "$(df -h "$HOME" | awk 'NR==2 {print $3 "/" $2 " used=" $5}')"
printf 'tools: git=%s node=%s npm=%s python=%s rg=%s jq=%s\n'   "$(git --version 2>/dev/null | awk '{print $3}' || echo missing)"   "$(node --version 2>/dev/null || echo missing)"   "$(npm --version 2>/dev/null || echo missing)"   "$(python3 --version 2>/dev/null | awk '{print $2}' || echo missing)"   "$(rg --version 2>/dev/null | awk 'NR==1 {print $2}' || echo missing)"   "$(jq --version 2>/dev/null || echo missing)"

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

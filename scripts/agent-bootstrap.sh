#!/usr/bin/env bash
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive
CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
MARKER="$CACHE_DIR/bootstrap-v2"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$CACHE_DIR" "$LOCAL_BIN"

PACKAGES=(
  ca-certificates curl wget git gh jq yq unzip zip rsync
  ripgrep fd-find fzf tree file lsof netcat-openbsd dnsutils
  sqlite3 ffmpeg imagemagick shellcheck
  build-essential pkg-config python3 python3-pip python3-venv
)

missing=()
for pkg in "${PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
    missing+=("$pkg")
  fi
done

if (("${#missing[@]}" > 0)); then
  echo "[agent-bootstrap] Installing missing packages: ${missing[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends "${missing[@]}"
else
  echo "[agent-bootstrap] System prerequisites already present."
fi

if ! command -v fd >/dev/null 2>&1 && command -v fdfind >/dev/null 2>&1; then
  ln -sfn "$(command -v fdfind)" "$LOCAL_BIN/fd"
fi

# GitHub-hosted runners already carry Node in the hosted toolcache. Recover it
# without downloading another Node distribution if PATH was lost.
if ! command -v node >/dev/null 2>&1 && [[ -d /opt/hostedtoolcache/node ]]; then
  node_bin="$(find /opt/hostedtoolcache/node -type f -path '*/x64/bin/node' 2>/dev/null | sort -V | tail -n 1 || true)"
  if [[ -n "$node_bin" ]]; then
    node_dir="$(dirname "$node_bin")"
    for name in node npm npx corepack; do
      [[ -x "$node_dir/$name" ]] && ln -sfn "$node_dir/$name" "$LOCAL_BIN/$name"
    done
  fi
fi

profile="$HOME/.profile"
touch "$profile"
if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$profile"; then
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$profile"
fi
export PATH="$LOCAL_BIN:$PATH"

git config --global fetch.prune true
git config --global init.defaultBranch main
git config --global core.autocrlf false
git config --global advice.detachedHead false

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$MARKER"

echo "[agent-bootstrap] Ready."
printf '  git=%s\n' "$(git --version 2>/dev/null || true)"
printf '  node=%s npm=%s\n' "$(node --version 2>/dev/null || echo missing)" "$(npm --version 2>/dev/null || echo missing)"
printf '  python=%s\n' "$(python3 --version 2>/dev/null || true)"
printf '  rg=%s fd=%s jq=%s\n' "$(rg --version 2>/dev/null | head -n1 || echo missing)" "$(fd --version 2>/dev/null | head -n1 || echo missing)" "$(jq --version 2>/dev/null || echo missing)"

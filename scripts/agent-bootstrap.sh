#!/usr/bin/env bash
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive
CACHE_DIR="${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$CACHE_DIR" "$LOCAL_BIN"

CORE_PACKAGES=(
  ca-certificates curl wget git gh jq yq unzip zip rsync
  ripgrep fd-find fzf tree file lsof netcat-openbsd dnsutils
  sqlite3 shellcheck python3 python3-pip
)
BUILD_PACKAGES=(build-essential pkg-config python3-venv)
MEDIA_PACKAGES=(ffmpeg imagemagick)

PROFILE="core"
while (($#)); do
  case "$1" in
    --core) PROFILE="core"; shift ;;
    --build) PROFILE="build"; shift ;;
    --media) PROFILE="media"; shift ;;
    --full) PROFILE="full"; shift ;;
    -h|--help)
      echo "Usage: agent-bootstrap.sh [--core|--build|--media|--full]"
      echo "core is the default; media/build extras are installed only when requested."
      exit 0
      ;;
    *) echo "Unknown bootstrap option: $1" >&2; exit 2 ;;
  esac
done

PACKAGES=("${CORE_PACKAGES[@]}")
case "$PROFILE" in
  build) PACKAGES+=("${BUILD_PACKAGES[@]}") ;;
  media) PACKAGES+=("${MEDIA_PACKAGES[@]}") ;;
  full) PACKAGES+=("${BUILD_PACKAGES[@]}" "${MEDIA_PACKAGES[@]}") ;;
esac

missing=()
for pkg in "${PACKAGES[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "ok installed"; then
    missing+=("$pkg")
  fi
done

if (("${#missing[@]}" > 0)); then
  echo "[agent-bootstrap] profile=$PROFILE installing: ${missing[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y --no-install-recommends "${missing[@]}"
else
  echo "[agent-bootstrap] profile=$PROFILE already satisfied."
fi

if ! command -v fd >/dev/null 2>&1 && command -v fdfind >/dev/null 2>&1; then
  ln -sfn "$(command -v fdfind)" "$LOCAL_BIN/fd"
fi

if ! command -v node >/dev/null 2>&1 && [[ -d /opt/hostedtoolcache/node ]]; then
  node_bin="$(find /opt/hostedtoolcache/node -type f -path '*/x64/bin/node' 2>/dev/null | sort -V | tail -n 1 || true)"
  if [[ -n "$node_bin" ]]; then
    node_dir="$(dirname "$node_bin")"
    for name in node npm npx corepack; do
      [[ -x "$node_dir/$name" ]] && ln -sfn "$node_dir/$name" "$LOCAL_BIN/$name"
    done
  fi
fi

profile_file="$HOME/.profile"
touch "$profile_file"
if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$profile_file"; then
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$profile_file"
fi
export PATH="$LOCAL_BIN:$PATH"

git config --global fetch.prune true
git config --global init.defaultBranch main
git config --global core.autocrlf false
git config --global advice.detachedHead false

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$CACHE_DIR/bootstrap-$PROFILE"

echo "[agent-bootstrap] Ready."
printf '  git=%s\n' "$(git --version 2>/dev/null || true)"
printf '  node=%s npm=%s python=%s\n' "$(node --version 2>/dev/null || echo missing)" "$(npm --version 2>/dev/null || echo missing)" "$(python3 --version 2>/dev/null || true)"
printf '  rg=%s fd=%s jq=%s\n' "$(rg --version 2>/dev/null | head -n1 || echo missing)" "$(fd --version 2>/dev/null | head -n1 || echo missing)" "$(jq --version 2>/dev/null || echo missing)"

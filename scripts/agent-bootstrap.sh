#!/usr/bin/env bash
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"

PROFILE="core"
case "${1:-}" in
  ""|--core) PROFILE="core" ;;
  --build) PROFILE="build" ;;
  --media) PROFILE="media" ;;
  --full) PROFILE="full" ;;
  -h|--help)
    echo "Usage: agent-bootstrap.sh [--core|--build|--media|--full]"
    exit 0
    ;;
  *) echo "Unknown bootstrap option: $1" >&2; exit 2 ;;
esac

mapfile -t PACKAGES < <(python3 "$SELF_DIR/lab_toolset.py" packages "$PROFILE")

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

PKG_CONFIG_PATH_MANIFEST="$(python3 "$SELF_DIR/lab_toolset.py" pkg-config-path)"
if [[ "$PROFILE" == "build" || "$PROFILE" == "full" ]]; then
  if ! PKG_CONFIG_PATH="$PKG_CONFIG_PATH_MANIFEST" pkg-config --exists libyuv 2>/dev/null; then
    sudo mkdir -p /opt/rustdesk-pkgconfig
    sudo tee /opt/rustdesk-pkgconfig/libyuv.pc >/dev/null <<'PC'
prefix=/usr
exec_prefix=${prefix}
libdir=/usr/lib/x86_64-linux-gnu
includedir=/usr/include

Name: libyuv
Description: YUV conversion library
Version: 0
Libs: -L${libdir} -lyuv
Cflags: -I${includedir}
PC
  fi
fi
export PKG_CONFIG_PATH="$PKG_CONFIG_PATH_MANIFEST${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

if ! command -v fd >/dev/null 2>&1 && command -v fdfind >/dev/null 2>&1; then
  ln -sfn "$(command -v fdfind)" "$LOCAL_BIN/fd"
fi

# GitHub-hosted runners normally provide Node.js already. Recover it from the
# hosted toolcache instead of downloading another copy if PATH was lost.
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
# Keep the literal shell expression in the profile; do not expand it now.
# shellcheck disable=SC2016
if ! grep -Fq 'export PATH="$HOME/.local/bin:$PATH"' "$profile_file"; then
  # shellcheck disable=SC2016
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$profile_file"
fi
export PATH="$LOCAL_BIN:$PATH"

git config --global fetch.prune true
git config --global init.defaultBranch main
git config --global core.autocrlf false
git config --global advice.detachedHead false
if command -v git-lfs >/dev/null 2>&1; then
  git lfs install --skip-repo >/dev/null 2>&1 || true
fi

echo "[agent-bootstrap] Ready profile=$PROFILE"
printf 'git=%s node=%s npm=%s python=%s rg=%s cmake=%s clang=%s ffmpeg=%s\n'   "$(git --version 2>/dev/null | awk '{print $3}' || echo missing)"   "$(node --version 2>/dev/null || echo missing)"   "$(npm --version 2>/dev/null || echo missing)"   "$(python3 --version 2>/dev/null | awk '{print $2}' || echo missing)"   "$(rg --version 2>/dev/null | awk 'NR==1 {print $2}' || echo missing)"   "$(cmake --version 2>/dev/null | awk 'NR==1 {print $3}' || echo missing)"   "$(clang --version 2>/dev/null | awk 'NR==1 {print $4}' || echo missing)"   "$(ffmpeg -version 2>/dev/null | awk 'NR==1 {print $3}' || echo missing)"

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

echo "[agent-bootstrap] profile=$PROFILE updating installed tools to current distro packages."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends "${PACKAGES[@]}"

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
if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$LOCAL_BIN" >> "$GITHUB_PATH"
fi

install_actionlint() {
  local arch metadata version base_url filename sha256 installed_version tmpdir
  arch="$(uname -m)"
  metadata="$(python3 "$SELF_DIR/lab_toolset.py" actionlint "$arch")"
  version="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' <<< "$metadata")"
  base_url="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["base_url"])' <<< "$metadata")"
  filename="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["filename"])' <<< "$metadata")"
  sha256="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])' <<< "$metadata")"

  installed_version=""
  if [[ -x "$LOCAL_BIN/actionlint" ]]; then
    installed_version="$("$LOCAL_BIN/actionlint" -version 2>/dev/null | awk 'NR==1 {print $1; exit}')"
  fi
  if [[ "$installed_version" == "$version" ]]; then
    echo "[agent-bootstrap] actionlint=$version already installed."
    return
  fi

  tmpdir="$(mktemp -d)"
  curl -fsSL "$base_url/$filename" -o "$tmpdir/$filename"
  printf '%s  %s\n' "$sha256" "$tmpdir/$filename" | sha256sum -c -
  tar -xzf "$tmpdir/$filename" -C "$tmpdir" actionlint
  install -m 0755 "$tmpdir/actionlint" "$LOCAL_BIN/actionlint"
  rm -rf "$tmpdir"
  echo "[agent-bootstrap] actionlint=$("$LOCAL_BIN/actionlint" -version 2>/dev/null | awk 'NR==1 {print $1; exit}')"
}

if python3 "$SELF_DIR/lab_toolset.py" commands "$PROFILE" | grep -qx actionlint; then
  install_actionlint
fi

git config --global fetch.prune true
git config --global init.defaultBranch main
git config --global core.autocrlf false
git config --global advice.detachedHead false
if command -v git-lfs >/dev/null 2>&1; then
  git lfs install --skip-repo >/dev/null 2>&1 || true
fi

echo "[agent-bootstrap] Ready profile=$PROFILE"
printf 'git=%s node=%s npm=%s python=%s rg=%s cmake=%s clang=%s ffmpeg=%s\n'   "$(git --version 2>/dev/null | awk '{print $3}' || echo missing)"   "$(node --version 2>/dev/null || echo missing)"   "$(npm --version 2>/dev/null || echo missing)"   "$(python3 --version 2>/dev/null | awk '{print $2}' || echo missing)"   "$(rg --version 2>/dev/null | awk 'NR==1 {print $2}' || echo missing)"   "$(cmake --version 2>/dev/null | awk 'NR==1 {print $3}' || echo missing)"   "$(clang --version 2>/dev/null | awk 'NR==1 {print $4}' || echo missing)"   "$(ffmpeg -version 2>/dev/null | awk 'NR==1 {print $3}' || echo missing)"
printf 'go=%s rustc=%s cargo=%s java=%s javac=%s actionlint=%s\n' \
  "$(go version 2>/dev/null | awk '{print $3}' || echo missing)" \
  "$(rustc --version 2>/dev/null | awk '{print $2}' || echo missing)" \
  "$(cargo --version 2>/dev/null | awk '{print $2}' || echo missing)" \
  "$(java -version 2>&1 | awk -F '\"' 'NR==1 {print $2}' || echo missing)" \
  "$(javac -version 2>&1 | awk '{print $2}' || echo missing)" \
  "$(actionlint -version 2>/dev/null | awk 'NR==1 {print $1; exit}' || echo missing)"

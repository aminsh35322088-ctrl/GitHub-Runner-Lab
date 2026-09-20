#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH="${GOSS_ARCH:-$(uname -m)}"

meta="$(python3 "$SELF_DIR/lab_toolset.py" goss "$ARCH")"
readarray -t fields < <(python3 -c '
import json,sys
m=json.load(sys.stdin)
for k in ("version","base_url","filename","sha256"):
    print(m[k])
' <<<"$meta")
version="${fields[0]}"
base_url="${fields[1]}"
filename="${fields[2]}"
sha256="${fields[3]}"

cache_root="${GOSS_CACHE_DIR:-${AGENT_KIT_CACHE_DIR:-$HOME/.cache/agent-runner-kit}/tools/goss/$version}"
binary="$cache_root/goss"
if [[ -x "$binary" ]]; then
  printf '%s\n' "$binary"
  exit 0
fi

mkdir -p "$cache_root"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
archive="$tmp/$filename"

curl --fail --location --silent --show-error --retry 3 \
  --proto '=https' --tlsv1.2 \
  "$base_url/$filename" -o "$archive"

printf '%s  %s\n' "$sha256" "$archive" | sha256sum --check --status
tar -xzf "$archive" -C "$tmp"
[[ -x "$tmp/goss" ]] || {
  echo "Goss archive did not contain an executable goss binary" >&2
  exit 4
}
install -m 0755 "$tmp/goss" "$binary"
printf '%s\n' "$binary"

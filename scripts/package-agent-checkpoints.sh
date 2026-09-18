#!/usr/bin/env bash
set -Eeuo pipefail

: "${RDC_STATE_KEY:?RDC_STATE_KEY secret is required to encrypt checkpoints}"

SRC_ROOT="${AGENT_CHECKPOINT_DIR:-$HOME/agent-checkpoints}"
OUT_DIR="${AGENT_CHECKPOINT_ARTIFACT_DIR:-${GITHUB_WORKSPACE:-$PWD}/.agent-artifacts}"
mkdir -p "$OUT_DIR"

latest=""
if [[ -L "$SRC_ROOT/latest" ]]; then
  latest="$(readlink -f "$SRC_ROOT/latest" || true)"
fi
if [[ -z "$latest" || ! -d "$latest" ]]; then
  echo "No agent checkpoint exists; nothing to package."
  exit 0
fi

base="$(basename "$latest")"
tmp="$(mktemp --suffix=.tar.gz)"
trap 'rm -f "$tmp"' EXIT

tar -C "$SRC_ROOT" -czf "$tmp" "$base"
out="$OUT_DIR/$base.tar.gz.enc"
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
  -in "$tmp" -out "$out" -pass env:RDC_STATE_KEY
chmod 600 "$out"
echo "Encrypted checkpoint artifact prepared: $out"

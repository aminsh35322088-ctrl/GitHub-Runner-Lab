#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${AGENT_WORKSPACE_ROOT:-$HOME/agent-workspaces}"
OUT_ROOT="${AGENT_CHECKPOINT_DIR:-$HOME/agent-checkpoints}"
REASON="${1:-manual}"
STAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
RUN_ID="${GITHUB_RUN_ID:-local}"
OUT="$OUT_ROOT/$STAMP-run-$RUN_ID"
mkdir -p "$OUT"
chmod 700 "$OUT"

manifest="$OUT/manifest.txt"
{
  echo "created_at=$STAMP"
  echo "reason=$REASON"
  echo "run_id=$RUN_ID"
  echo "workspace_root=$ROOT"
} > "$manifest"

count=0
if [[ ! -d "$ROOT" ]]; then
  echo "No workspace root exists: $ROOT" >> "$manifest"
else
while IFS= read -r -d '' repo; do
  count=$((count + 1))
  name="$(basename "$repo")"
  safe="$(printf '%s' "$name" | tr -cs 'A-Za-z0-9._-' '_')"
  dir="$OUT/$safe"
  mkdir -p "$dir"

  {
    echo "path=$repo"
    echo "remote=$(git -C "$repo" remote get-url origin 2>/dev/null || echo none)"
    echo "branch=$(git -C "$repo" branch --show-current 2>/dev/null || true)"
    echo "head=$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)"
    echo
    git -C "$repo" status --short --branch 2>/dev/null || true
  } > "$dir/metadata.txt"

  git -C "$repo" diff --binary HEAD > "$dir/tracked-working-tree.patch" 2>/dev/null || true
  git -C "$repo" ls-files --others --exclude-standard > "$dir/untracked-files.txt" 2>/dev/null || true

  upstream="$(git -C "$repo" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
  if [[ -n "$upstream" ]]; then
    ahead="$(git -C "$repo" rev-list --count "$upstream"..HEAD 2>/dev/null || echo 0)"
    if [[ "$ahead" =~ ^[0-9]+$ ]] && ((ahead > 0)); then
      git -C "$repo" format-patch --stdout "$upstream"..HEAD > "$dir/unpushed-commits.patch" 2>/dev/null || true
    fi
  fi

  echo "repo_$count=$repo" >> "$manifest"
done < <(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -exec test -d '{}/.git' ';' -print0 2>/dev/null)
fi

echo "repository_count=$count" >> "$manifest"
ln -sfn "$OUT" "$OUT_ROOT/latest"
echo "CHECKPOINT_DIR=$OUT"
echo "CHECKPOINT_REPOSITORIES=$count"

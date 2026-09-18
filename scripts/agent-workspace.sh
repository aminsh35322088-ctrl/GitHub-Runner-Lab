#!/usr/bin/env bash
set -Eeuo pipefail

DEFAULT_REPO="https://github.com/aminsh35322088-ctrl/opencode-telegram-bot.git"
ROOT="${AGENT_WORKSPACE_ROOT:-$HOME/agent-workspaces}"
REPO="$DEFAULT_REPO"
REF="main"
PR=""
DEST=""
WITH_DEPS=0
FORCE=0

usage() {
  cat <<'EOF'
Usage: agent-workspace.sh [--repo URL] [--ref BRANCH_OR_SHA] [--pr NUMBER]
                          [--dir PATH] [--deps] [--force]

Defaults to aminsh35322088-ctrl/opencode-telegram-bot on main.
--deps is optional because full tests run on GitHub Actions.
EOF
}

while (($#)); do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --pr) PR="$2"; shift 2 ;;
    --dir) DEST="$2"; shift 2 ;;
    --deps) WITH_DEPS=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

mkdir -p "$ROOT"
repo_name="$(basename "${REPO%.git}")"
label="${PR:+pr-$PR}"
label="${label:-${REF//\//-}}"
DEST="${DEST:-$ROOT/$repo_name-$label}"

if [[ ! -d "$DEST/.git" ]]; then
  echo "[agent-workspace] Cloning $REPO -> $DEST"
  git clone --filter=blob:none --no-tags "$REPO" "$DEST"
fi

if [[ -n "$(git -C "$DEST" status --porcelain)" ]]; then
  if ((FORCE)); then
    echo "[agent-workspace] Resetting dirty disposable workspace."
    git -C "$DEST" reset --hard
    git -C "$DEST" clean -fd
  else
    echo "[agent-workspace] Refusing to overwrite dirty workspace: $DEST" >&2
    git -C "$DEST" status --short >&2
    exit 3
  fi
fi

git -C "$DEST" remote set-url origin "$REPO"
git -C "$DEST" fetch --prune origin

if [[ -n "$PR" ]]; then
  git -C "$DEST" fetch origin "+refs/pull/$PR/head:refs/remotes/origin/pr/$PR"
  git -C "$DEST" checkout -B "pr-$PR" "refs/remotes/origin/pr/$PR"
else
  if git -C "$DEST" show-ref --verify --quiet "refs/remotes/origin/$REF"; then
    git -C "$DEST" checkout -B "$REF" "refs/remotes/origin/$REF"
  else
    git -C "$DEST" fetch origin "$REF"
    git -C "$DEST" checkout --detach FETCH_HEAD
  fi
fi

if ((WITH_DEPS)); then
  if [[ -f "$DEST/package-lock.json" ]]; then
    echo "[agent-workspace] Installing npm dependencies (explicit --deps request)."
    (cd "$DEST" && npm_config_audit=false npm_config_fund=false npm ci)
  else
    echo "[agent-workspace] No package-lock.json; dependency install skipped."
  fi
fi

echo "AGENT_WORKSPACE=$DEST"
echo "AGENT_SHA=$(git -C "$DEST" rev-parse HEAD)"
echo "AGENT_BRANCH=$(git -C "$DEST" branch --show-current)"

#!/usr/bin/env bash
set -Eeuo pipefail

# Run once per ephemeral Actions runner before RDC is restored or launched.
# runner remains the trusted workflow executor; RDC/Agent and Vault broker get
# separate UIDs and neither receives the other's home or process credentials.
if [[ "$(id -u)" -ne 0 ]]; then
  echo 'setup-runner-identities.sh must run as root.' >&2
  exit 2
fi

AGENT_USER="${RDC_AGENT_USER:-rdc-agent}"
BROKER_USER="${VAULT_BROKER_USER:-runner-vault-broker}"
CLIENT_GROUP="${VAULT_BROKER_GROUP:-rdc-vault-client}"
AGENT_HOME="${RDC_AGENT_HOME:-/var/lib/rdc-agent}"
BROKER_HOME="${VAULT_BROKER_HOME:-/var/lib/runner-vault-broker}"

command -v useradd >/dev/null
command -v usermod >/dev/null
command -v groupadd >/dev/null
# Rootless Podman needs more than the `podman` binary. Check every host
# prerequisite so preinstalled but incomplete runner images are repaired too.
SYSTEM_PACKAGES=()
command -v setfacl >/dev/null || SYSTEM_PACKAGES+=(acl)
command -v podman >/dev/null || SYSTEM_PACKAGES+=(podman)
command -v newuidmap >/dev/null && command -v newgidmap >/dev/null || SYSTEM_PACKAGES+=(uidmap)
command -v slirp4netns >/dev/null || SYSTEM_PACKAGES+=(slirp4netns)
command -v fuse-overlayfs >/dev/null || SYSTEM_PACKAGES+=(fuse-overlayfs)
if ((${#SYSTEM_PACKAGES[@]})); then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${SYSTEM_PACKAGES[@]}"
fi

getent group "$CLIENT_GROUP" >/dev/null || groupadd --system "$CLIENT_GROUP"
getent group "$AGENT_USER" >/dev/null || groupadd --system "$AGENT_USER"
if ! id "$AGENT_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$AGENT_HOME" --shell /bin/bash \
    --gid "$AGENT_USER" --groups "$CLIENT_GROUP" "$AGENT_USER"
else
  usermod --home "$AGENT_HOME" --shell /bin/bash --gid "$AGENT_USER" --groups "$CLIENT_GROUP" "$AGENT_USER"
fi
if ! id "$BROKER_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$BROKER_HOME" --shell /usr/sbin/nologin \
    --gid "$CLIENT_GROUP" "$BROKER_USER"
else
  usermod --home "$BROKER_HOME" --shell /usr/sbin/nologin --gid "$CLIENT_GROUP" --groups '' "$BROKER_USER"
fi

install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 0700 "$AGENT_HOME"
install -d -o "$BROKER_USER" -g "$CLIENT_GROUP" -m 0700 "$BROKER_HOME"
install -d -o "$AGENT_USER" -g "$AGENT_USER" -m 0700 \
  "$AGENT_HOME/.local/bin" "$AGENT_HOME/.config" "$AGENT_HOME/.cache" \
  "$AGENT_HOME/.desktop-commander-device" "$AGENT_HOME/.npm" "$AGENT_HOME/.run"

# Give the rootless user a non-overlapping subordinate range. Never reuse any
# range already assigned to packer, runner or another account.
python3 - "$AGENT_USER" <<'PY'
from pathlib import Path
import sys
user = sys.argv[1]
for filename in (Path('/etc/subuid'), Path('/etc/subgid')):
    rows = []
    for raw in filename.read_text().splitlines():
        parts = raw.split(':')
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            rows.append((parts[0], int(parts[1]), int(parts[2])))
    if any(name == user for name, _, _ in rows):
        continue
    start = max((base + size for _, base, size in rows), default=100000)
    start = max(start, 100000)
    with filename.open('a') as handle:
        handle.write(f'{user}:{start}:65536\n')
PY

# Do not grant the Agent rootful Docker or sudo membership. The workflow runner
# keeps its existing privileges; only the long-lived RDC identity is isolated.
usermod -G "$CLIENT_GROUP" "$AGENT_USER"
if id -nG "$AGENT_USER" | tr ' ' '\n' | grep -Exq 'docker|sudo'; then
  echo 'RDC Agent unexpectedly has a privileged group.' >&2
  exit 4
fi
if id -nG "$BROKER_USER" | tr ' ' '\n' | grep -Exq 'docker|sudo'; then
  echo 'Vault broker unexpectedly has a privileged group.' >&2
  exit 4
fi

# Only the explicit shared workspaces/caches are accessible from the Agent UID.
RUNNER_HOME="${RUNNER_HOME:-/home/runner}"
WORKSPACE="${GITHUB_WORKSPACE:-}"
# Default-deny the Agent across the Runner home (tokens, SSH, npm config,
# checkout state, logs). Re-grant access only on the explicit shared roots below.
setfacl -R -m "u:${AGENT_USER}:---" "$RUNNER_HOME"
setfacl -m "u:${AGENT_USER}:--x,d:u:${AGENT_USER}:---" "$RUNNER_HOME"
install -d -o runner -g runner -m 0700 "$RUNNER_HOME/.cache"
setfacl -m "u:${AGENT_USER}:--x,d:u:${AGENT_USER}:---" "$RUNNER_HOME/.cache"
shared_paths=(
  "${AGENT_WORKSPACE_ROOT:-$RUNNER_HOME/agent-workspaces}"
  "${AGENT_RECOVERY_DIR:-$RUNNER_HOME/agent-recovery}"
  "${AGENT_JOBS_DIR:-$RUNNER_HOME/agent-jobs}"
  "${AGENT_CHECKPOINT_DIR:-$RUNNER_HOME/agent-checkpoints}"
  "${AGENT_KIT_CACHE_DIR:-$RUNNER_HOME/.cache/agent-runner-kit}"
  "${AGENT_PROJECT_CACHE_ROOT:-$RUNNER_HOME/.cache/agent-projects}"
  "${AGENT_NPM_CACHE_DIR:-$RUNNER_HOME/agent-npm-cache}"
)
for path in "${shared_paths[@]}"; do
  [[ -n "$path" ]] || continue
  install -d -o runner -g runner -m 0700 "$path"
  setfacl -R -m "u:${AGENT_USER}:rwX,u:runner:rwX" "$path"
  find "$path" -type d -exec setfacl -m "d:u:${AGENT_USER}:rwX,d:u:runner:rwX" {} +
done

# Copy Node and npm into a private, root-owned Agent runtime. The hosted
# toolcache is shared with trusted runner steps, so the Agent receives only
# read/execute access to that source and runs its own immutable copy.
NODE_BIN="$(command -v node)"
NPM_ROOT="$(npm root -g)"
NPM_MODULE="$NPM_ROOT/npm"
COREPACK_MODULE="$NPM_ROOT/corepack"
[[ -x "$NODE_BIN" && -f "$NPM_MODULE/bin/npm-cli.js" && -f "$NPM_MODULE/bin/npx-cli.js" ]] || {
  echo 'Node/npm runtime could not be located for the isolated Agent.' >&2
  exit 7
}
AGENT_NODE_HOME="$AGENT_HOME/.node-runtime"
install -d -o root -g "$AGENT_USER" -m 0750 \
  "$AGENT_NODE_HOME/bin" "$AGENT_NODE_HOME/lib/node_modules"
install -o root -g "$AGENT_USER" -m 0550 "$NODE_BIN" "$AGENT_NODE_HOME/bin/node"
cp -a "$NPM_MODULE" "$AGENT_NODE_HOME/lib/node_modules/npm"
if [[ -d "$COREPACK_MODULE" ]]; then
  cp -a "$COREPACK_MODULE" "$AGENT_NODE_HOME/lib/node_modules/corepack"
fi
chown -R "root:$AGENT_USER" "$AGENT_NODE_HOME"
find "$AGENT_NODE_HOME" -type d -exec chmod 0550 {} +
find "$AGENT_NODE_HOME" -type f -perm /111 -exec chmod 0550 {} +
find "$AGENT_NODE_HOME" -type f ! -perm /111 -exec chmod 0440 {} +
cat > "$AGENT_NODE_HOME/bin/npm" <<'SH_NPM'
#!/usr/bin/env bash
set -Eeuo pipefail
exec "$HOME/.node-runtime/bin/node" "$HOME/.node-runtime/lib/node_modules/npm/bin/npm-cli.js" "$@"
SH_NPM
cat > "$AGENT_NODE_HOME/bin/npx" <<'SH_NPX'
#!/usr/bin/env bash
set -Eeuo pipefail
exec "$HOME/.node-runtime/bin/node" "$HOME/.node-runtime/lib/node_modules/npm/bin/npx-cli.js" "$@"
SH_NPX
chmod 0550 "$AGENT_NODE_HOME/bin/npm" "$AGENT_NODE_HOME/bin/npx"
chown root:"$AGENT_USER" "$AGENT_NODE_HOME/bin/npm" "$AGENT_NODE_HOME/bin/npx"
if [[ -f "$AGENT_NODE_HOME/lib/node_modules/corepack/dist/corepack.js" ]]; then
  cat > "$AGENT_NODE_HOME/bin/corepack" <<'SH_COREPACK'
#!/usr/bin/env bash
set -Eeuo pipefail
exec "$HOME/.node-runtime/bin/node" "$HOME/.node-runtime/lib/node_modules/corepack/dist/corepack.js" "$@"
SH_COREPACK
  chmod 0550 "$AGENT_NODE_HOME/bin/corepack"
  chown root:"$AGENT_USER" "$AGENT_NODE_HOME/bin/corepack"
fi
# The Agent executes only its private Node/npm copy. Deny the shared hosted
# toolcache entirely, including sibling Node versions and future installations.
if [[ -d /opt/hostedtoolcache ]]; then
  setfacl -R -m "u:${AGENT_USER}:---" /opt/hostedtoolcache
  find /opt/hostedtoolcache -type d -exec setfacl -m "d:u:${AGENT_USER}:---" {} +
fi

# Create a sanitized, immutable source snapshot for the Agent. It intentionally
# excludes .git and all runner checkout credentials. Root owns the files so the
# Agent cannot alter scripts that trusted runner steps will execute later.
AGENT_LAB="$AGENT_HOME/lab"
install -d -o root -g "$AGENT_USER" -m 0750 "$AGENT_LAB"
if [[ -n "$WORKSPACE" && -d "$WORKSPACE" ]]; then
  tar -C "$WORKSPACE" --exclude='./.git' --exclude='./.agent-artifacts' -cf - . | tar -C "$AGENT_LAB" -xf -
  chown -R "root:$AGENT_USER" "$AGENT_LAB"
  find "$AGENT_LAB" -type d -exec chmod 0550 {} +
  find "$AGENT_LAB" -type f -perm /111 -exec chmod 0550 {} +
  find "$AGENT_LAB" -type f ! -perm /111 -exec chmod 0440 {} +
else
  echo 'GITHUB_WORKSPACE is unavailable; cannot prepare the sanitized Agent source tree.' >&2
  exit 6
fi

# A compatibility command name keeps existing scripts usable while routing all
# container work to a rootless Podman daemon owned by rdc-agent.
cat > "$AGENT_HOME/.local/bin/docker" <<'SH'
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ "${1:-}" == "run" ]]; then
  shift
  has_userns=false
  for arg in "$@"; do [[ "$arg" == --userns=* || "$arg" == --userns ]] && has_userns=true; done
  if [[ "$has_userns" == true ]]; then exec /usr/bin/podman run "$@"; fi
  exec /usr/bin/podman run --userns=keep-id "$@"
fi
exec /usr/bin/podman "$@"
SH
chmod 0755 "$AGENT_HOME/.local/bin/docker"
chown "$AGENT_USER:$AGENT_USER" "$AGENT_HOME/.local/bin/docker"

cat > "$AGENT_HOME/.profile" <<'SH'
export PATH="$HOME/.local/bin:$HOME/.node-runtime/bin:$PATH"
export NPM_CONFIG_PREFIX="$HOME/.local"
export npm_config_cache="$HOME/.npm"
export AGENT_CONTAINER_ENGINE=podman
export XDG_RUNTIME_DIR="$HOME/.run"
export AGENT_WORKSPACE_ROOT=/home/runner/agent-workspaces
export AGENT_RECOVERY_DIR=/home/runner/agent-recovery
export AGENT_JOBS_DIR=/home/runner/agent-jobs
export AGENT_KIT_CACHE_DIR=/home/runner/.cache/agent-runner-kit
export AGENT_CHECKPOINT_DIR=/home/runner/agent-checkpoints
export AGENT_PROJECT_CACHE_ROOT=/home/runner/.cache/agent-projects
export AGENT_NPM_CACHE_DIR=/home/runner/agent-npm-cache
export AGENT_CHECKPOINT_ARTIFACT_DIR=/home/runner/agent-checkpoints/artifacts
export npm_config_cache=/home/runner/agent-npm-cache
SH
chmod 0600 "$AGENT_HOME/.profile"
chown "$AGENT_USER:$AGENT_USER" "$AGENT_HOME/.profile"

# Linger creates a stable per-user runtime directory for rootless Podman.
if command -v loginctl >/dev/null 2>&1; then loginctl enable-linger "$AGENT_USER" || true; fi

AGENT_UID="$(id -u "$AGENT_USER")"
AGENT_GID="$(id -g "$AGENT_USER")"
PODMAN_ROOTLESS="$(runuser -u "$AGENT_USER" -- env HOME="$AGENT_HOME" \
  XDG_RUNTIME_DIR="$AGENT_HOME/.run" PATH="$AGENT_HOME/.local/bin:/usr/bin:/bin" \
  /usr/bin/podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)"
if [[ "$PODMAN_ROOTLESS" != true ]]; then
  echo 'Rootless Podman did not initialize for the RDC Agent account.' >&2
  exit 5
fi
if runuser -u "$AGENT_USER" -- test -x "$RUNNER_HOME/.npm" || \
   runuser -u "$AGENT_USER" -- test -x "$RUNNER_HOME/.ssh" || \
   runuser -u "$AGENT_USER" -- test -x "$RUNNER_HOME/.config"; then
  echo 'RDC Agent can still traverse a private Runner home directory.' >&2
  exit 8
fi
runuser -u "$AGENT_USER" -- test -w "${AGENT_WORKSPACE_ROOT:-$RUNNER_HOME/agent-workspaces}" || {
  echo 'RDC Agent cannot write its dedicated workspace root.' >&2; exit 9;
}
runuser -u "$AGENT_USER" -- test -r "$AGENT_LAB/scripts/start-rdc.sh" || {
  echo 'RDC Agent cannot read the immutable source snapshot.' >&2; exit 10;
}
if runuser -u "$AGENT_USER" -- test -w "$AGENT_LAB/scripts/start-rdc.sh"; then
  echo 'RDC Agent can modify trusted source scripts.' >&2; exit 11;
fi
runuser -u "$AGENT_USER" -- "$AGENT_NODE_HOME/bin/node" --version >/dev/null

install -d -o "$BROKER_USER" -g "$CLIENT_GROUP" -m 0750 /run/runner-vault
printf 'RDC_AGENT_UID=%s\nRDC_AGENT_GID=%s\nVAULT_BROKER_UID=%s\n' \
  "$AGENT_UID" "$AGENT_GID" "$(id -u "$BROKER_USER")"
echo 'RDC_AGENT_DOCKER_ENGINE=ROOTLESS_PODMAN'
echo 'RDC_AGENT_PRIVILEGED_GROUPS=NONE'
echo 'RDC_VAULT_CLIENT_GROUP=READY'

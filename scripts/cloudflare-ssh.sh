#!/usr/bin/env bash
set -Eeuo pipefail

CLOUDFLARED_VERSION="2026.5.1"
CLOUDFLARED_SHA256_AMD64="3c6a5ba995a258dbe90f98e5fdb2c2620b7be72c3ca761614f6eb52aee252cea"
CLOUDFLARED_SHA256_ARM64="7b7a8b9a2764acab0fecda633cb54a6c0df42d7f8ca1ec45c78333c2227d8d91"
BIN_DIR="${HOME}/.local/bin"
CLOUDFLARED_BIN="${BIN_DIR}/cloudflared"
STATE_DIR="${RUNNER_TEMP:-/tmp}/opencode-cloudflare-ssh"
PID_FILE="${STATE_DIR}/cloudflared.pid"
LOG_FILE="${STATE_DIR}/cloudflared.log"
SSHD_CONFIG="/etc/ssh/sshd_config.d/99-opencode-runner.conf"

log() { printf '[cloudflare-ssh] %s\n' "$*"; }
die() { printf '[cloudflare-ssh] ERROR: %s\n' "$*" >&2; exit 1; }

configured() {
  [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" && -n "${OPENCODE_BOT_SSH_PUBLIC_KEY:-}" ]]
}

set_output() {
  local key="$1" value="$2"
  [[ -n "${GITHUB_OUTPUT:-}" ]] && printf '%s=%s\n' "$key" "$value" >> "$GITHUB_OUTPUT"
}

install_cloudflared() {
  mkdir -p "$BIN_DIR" "$STATE_DIR"
  if [[ -x "$CLOUDFLARED_BIN" ]] && "$CLOUDFLARED_BIN" --version 2>/dev/null | grep -q "version ${CLOUDFLARED_VERSION}"; then
    return
  fi

  local arch asset sha tmp
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) asset="cloudflared-linux-amd64"; sha="$CLOUDFLARED_SHA256_AMD64" ;;
    aarch64|arm64) asset="cloudflared-linux-arm64"; sha="$CLOUDFLARED_SHA256_ARM64" ;;
    *) die "Unsupported architecture: $arch" ;;
  esac

  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' RETURN
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/${asset}" -o "$tmp"
  printf '%s  %s\n' "$sha" "$tmp" | sha256sum -c - >/dev/null
  install -m 0755 "$tmp" "$CLOUDFLARED_BIN"
  trap - RETURN
  rm -f "$tmp"
}

validate_public_key() {
  local key_file
  key_file="$(mktemp)"
  trap 'rm -f "$key_file"' RETURN
  printf '%s\n' "$OPENCODE_BOT_SSH_PUBLIC_KEY" > "$key_file"
  ssh-keygen -l -f "$key_file" >/dev/null 2>&1 || die "OPENCODE_BOT_SSH_PUBLIC_KEY is not a valid SSH public key"
  trap - RETURN
  rm -f "$key_file"
}

install_sshd() {
  if ! command -v sshd >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server
  fi

  validate_public_key
  install -d -m 0700 "$HOME/.ssh"
  touch "$HOME/.ssh/authorized_keys"
  chmod 0600 "$HOME/.ssh/authorized_keys"
  if ! grep -qxF "$OPENCODE_BOT_SSH_PUBLIC_KEY" "$HOME/.ssh/authorized_keys"; then
    printf '%s\n' "$OPENCODE_BOT_SSH_PUBLIC_KEY" >> "$HOME/.ssh/authorized_keys"
  fi

  sudo install -d -m 0755 /run/sshd /etc/ssh/sshd_config.d
  sudo tee "$SSHD_CONFIG" >/dev/null <<'EOF'
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers runner
X11Forwarding no
AllowAgentForwarding no
PermitTunnel no
EOF
  sudo sshd -t

  if ! pgrep -x sshd >/dev/null 2>&1; then
    sudo /usr/sbin/sshd
  fi
}

cloudflared_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

start_tunnel() {
  install_cloudflared
  mkdir -p "$STATE_DIR"
  if cloudflared_running; then
    log "cloudflared is already running (pid=$(cat "$PID_FILE"))."
    return
  fi

  : > "$LOG_FILE"
  (
    unset RUNNER_TRACKING_ID
    export TUNNEL_TOKEN="$CLOUDFLARE_TUNNEL_TOKEN"
    nohup "$CLOUDFLARED_BIN" tunnel --no-autoupdate run >>"$LOG_FILE" 2>&1 &
    echo "$!" > "$PID_FILE"
  )

  local deadline=$((SECONDS + 30))
  while (( SECONDS < deadline )); do
    cloudflared_running || {
      tail -n 80 "$LOG_FILE" >&2 || true
      die "cloudflared exited during startup"
    }
    if grep -q "Registered tunnel connection" "$LOG_FILE"; then
      log "Cloudflare Tunnel connected."
      return
    fi
    sleep 1
  done

  tail -n 80 "$LOG_FILE" >&2 || true
  die "Cloudflare Tunnel did not register within 30 seconds"
}

health() {
  configured || die "Cloudflare SSH is not configured"
  sudo sshd -t
  pgrep -x sshd >/dev/null 2>&1 || die "sshd is not running"
  cloudflared_running || die "cloudflared is not running"
  grep -q "Registered tunnel connection" "$LOG_FILE" || die "cloudflared has no registered tunnel connection"
  log "ready hostname=${CLOUDFLARE_SSH_HOSTNAME:-<configure-var>} user=$(id -un) cloudflared=$("$CLOUDFLARED_BIN" --version | head -1)"
}

start() {
  if ! configured; then
    log "Not configured; set CLOUDFLARE_TUNNEL_TOKEN and OPENCODE_BOT_SSH_PUBLIC_KEY to enable."
    set_output configured false
    return 0
  fi
  install_sshd
  start_tunnel
  health
  set_output configured true
  set_output ssh_user "$(id -un)"
}

stop() {
  if cloudflared_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  log "Cloudflare tunnel stopped."
}

status() {
  printf 'CONFIGURED=%s\n' "$(configured && echo true || echo false)"
  printf 'SSHD_RUNNING=%s\n' "$(pgrep -x sshd >/dev/null 2>&1 && echo true || echo false)"
  printf 'CLOUDFLARED_RUNNING=%s\n' "$(cloudflared_running && echo true || echo false)"
  printf 'SSH_USER=%s\n' "$(id -un)"
  printf 'SSH_HOSTNAME=%s\n' "${CLOUDFLARE_SSH_HOSTNAME:-}"
}

case "${1:-status}" in
  start) start ;;
  health) health ;;
  stop) stop ;;
  status) status ;;
  *) echo "Usage: $0 {start|health|status|stop}" >&2; exit 2 ;;
esac

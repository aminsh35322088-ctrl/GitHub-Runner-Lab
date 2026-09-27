#!/usr/bin/env bash
set -Eeuo pipefail

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale is not installed; nothing to disconnect."
  exit 0
fi

backend_state() {
  tailscale status --json 2>/dev/null | jq -r '.BackendState // "Unknown"' 2>/dev/null || echo "Unknown"
}

state="$(backend_state)"
if [[ "$state" != "Running" ]]; then
  echo "Tailscale is already disconnected (BackendState=$state)."
  exit 0
fi

for attempt in 1 2 3; do
  echo "Disconnecting ephemeral Tailscale node before runner handoff (attempt $attempt/3)."
  if sudo tailscale logout; then
    for _ in $(seq 1 10); do
      state="$(backend_state)"
      if [[ "$state" != "Running" ]]; then
        echo "TAILSCALE_LOGOUT=CONFIRMED"
        echo "Tailscale disconnected cleanly (BackendState=$state)."
        exit 0
      fi
      sleep 1
    done
  fi
  sleep 2
done

state="$(backend_state)"
echo "Tailscale logout was not confirmed; BackendState=$state." >&2
exit 1

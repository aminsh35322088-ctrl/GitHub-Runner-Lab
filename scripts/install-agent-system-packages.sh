#!/usr/bin/env bash
set -Eeuo pipefail

# This runs only in the trusted GitHub Actions runner context, never as Agent.
mapfile -t PACKAGES < <(python3 "$(dirname "$0")/lab_toolset.py" packages full)
(("${#PACKAGES[@]}" > 0)) || { echo 'Agent toolchain package manifest is empty.' >&2; exit 3; }

if [[ "$(id -u)" -eq 0 ]]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${PACKAGES[@]}"
else
  command -v sudo >/dev/null 2>&1 || { echo 'Trusted runner package installation requires root/sudo.' >&2; exit 2; }
  sudo -n apt-get update -qq
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${PACKAGES[@]}"
fi

echo 'AGENT_SYSTEM_PACKAGES=READY'

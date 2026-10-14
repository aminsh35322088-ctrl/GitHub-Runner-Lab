#!/usr/bin/env bash
set -Eeuo pipefail
# Keep latest stable, but tolerate a transient registry/connection reset.
for attempt in 1 2 3; do
  if timeout 240 npm install --global @wonderwhy-er/desktop-commander@latest; then
    exit 0
  fi
  echo "::warning::RDC installation attempt $attempt/3 failed."
  if (( attempt < 3 )); then sleep $((attempt * 5)); fi
done
echo '::error::RDC installation failed after three bounded attempts.'
exit 1

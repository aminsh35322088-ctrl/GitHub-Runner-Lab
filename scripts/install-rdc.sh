#!/usr/bin/env bash
set -Eeuo pipefail
# Pinned RDC release (issue #32): the exact version and its npm registry
# SHA-512 integrity are committed here so this privileged install no longer
# floats on @latest. Bump both together from the registry:
#   npm view @wonderwhy-er/desktop-commander dist-tags.latest dist.integrity
RDC_VERSION="0.3.1"
RDC_INTEGRITY="sha512-cx7KTscLF951adHmDLVCDIVTqJ/Bg1LtdRILZoo5wa/UMeyOfMAuOJOEBulNMKBKb5Xd3nHDQnB9aXroN9Fw5A=="
RDC_TARBALL_URL="https://registry.npmjs.org/@wonderwhy-er/desktop-commander/-/desktop-commander-${RDC_VERSION}.tgz"

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
tarball="$workdir/desktop-commander-${RDC_VERSION}.tgz"

install_rdc() {
  curl --fail --location --silent --show-error --retry 3 \
    --proto '=https' --tlsv1.2 "$RDC_TARBALL_URL" -o "$tarball"
  local actual
  actual="sha512-$(openssl dgst -sha512 -binary "$tarball" | openssl base64 -A)"
  if [[ "$actual" != "$RDC_INTEGRITY" ]]; then
    echo "::error::RDC tarball integrity mismatch for ${RDC_VERSION}." >&2
    return 1
  fi
  timeout 240 npm install --global "$tarball"
}

# Tolerate a transient registry/connection reset across bounded attempts.
for attempt in 1 2 3; do
  if install_rdc; then
    echo "Installed @wonderwhy-er/desktop-commander@${RDC_VERSION} (SHA-512 integrity verified)."
    exit 0
  fi
  echo "::warning::RDC installation attempt $attempt/3 failed."
  if (( attempt < 3 )); then sleep $((attempt * 5)); fi
done
echo '::error::RDC installation failed after three bounded attempts.'
exit 1

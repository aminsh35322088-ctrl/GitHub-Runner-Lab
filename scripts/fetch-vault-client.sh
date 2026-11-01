#!/usr/bin/env bash
# Download the pinned Runner Credential Vault client to $1.
#
# The Vault repository is public, so no credential is needed. VAULT_CLIENT_GITHUB_TOKEN
# is only a fallback (for example if the repository is made private again, or the
# anonymous GitHub API rate limit is hit). The Vault stays optional either way: this
# script exits non-zero on failure and callers treat that as "Vault unavailable".
set -Eeuo pipefail

dest="${1:?usage: fetch-vault-client.sh DEST}"
ref="${VAULT_CLIENT_REF:-}"
if [[ ! "$ref" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::warning::VAULT_CLIENT_REF must be an immutable 40-character commit SHA; Vault integration will be degraded."
  exit 1
fi

token="${VAULT_CLIENT_GITHUB_TOKEN:-}"
unset VAULT_CLIENT_GITHUB_TOKEN
api_path="repos/aminsh35322088-ctrl/GitHub-runner-vault/contents/client/vault.mjs?ref=$ref"
tmp="$(mktemp --suffix=.mjs)"
trap 'rm -f "$tmp"' EXIT

if curl --fail --silent --show-error --max-time 30 \
     --header 'Accept: application/vnd.github.raw+json' \
     --output "$tmp" "https://api.github.com/$api_path" 2>/dev/null && [[ -s "$tmp" ]]; then
  echo "VAULT_CLIENT_SOURCE=public"
elif [[ -n "$token" ]]; then
  GH_TOKEN="$token" gh api "$api_path" --header 'Accept: application/vnd.github.raw+json' > "$tmp"
  echo "VAULT_CLIENT_SOURCE=token-fallback"
else
  echo "::warning::Could not download the Vault client anonymously and no fallback token is available; Vault integration will be degraded."
  exit 1
fi

[[ -s "$tmp" ]] || { echo "::warning::Downloaded Vault client is empty."; exit 1; }
node --check "$tmp"
install -m 600 "$tmp" "$dest"

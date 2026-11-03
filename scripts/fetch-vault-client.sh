#!/usr/bin/env bash
# Download the pinned Runner Credential Vault client to $1.
#
# The Vault repository is public, so no credential is needed. VAULT_CLIENT_GITHUB_TOKEN
# is only a fallback (for example if the repository is made private again, or the
# anonymous GitHub API rate limit is hit). The Vault stays optional either way: this
# script exits non-zero on failure and callers treat that as "Vault unavailable".
#
# The fallback token is sent to curl through its stdin config stream. It is never
# placed in curl/timeout arguments or exported as GH_TOKEN to a child process.
set -Eeuo pipefail

dest="${1:?usage: fetch-vault-client.sh DEST}"
repository="${VAULT_CLIENT_REPOSITORY:-}"
if [[ ! "$repository" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "::warning::RUNNER_VAULT_REPOSITORY must be an owner/name repository; Vault integration will be degraded."
  exit 1
fi

ref="${VAULT_CLIENT_REF:-}"
if [[ ! "$ref" =~ ^[0-9a-f]{40}$ ]]; then
  echo "::warning::RUNNER_VAULT_CLIENT_REF must be an immutable 40-character commit SHA; Vault integration will be degraded."
  exit 1
fi

token="${VAULT_CLIENT_GITHUB_TOKEN:-}"
unset VAULT_CLIENT_GITHUB_TOKEN
api_path="repos/$repository/contents/client/vault.mjs?ref=$ref"
api_url="https://api.github.com/$api_path"
tmp="$(mktemp --suffix=.mjs)"
trap 'rm -f "$tmp"' EXIT

if curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
     --header 'Accept: application/vnd.github.raw+json' \
     --output "$tmp" "$api_url" 2>/dev/null && [[ -s "$tmp" ]]; then
  echo "VAULT_CLIENT_SOURCE=public"
elif [[ -n "$token" ]]; then
  # GitHub tokens cannot contain line breaks. Reject them rather than allowing
  # a malformed token to inject additional curl config directives.
  if [[ "$token" == *$'\n'* || "$token" == *$'\r'* ]]; then
    echo "::warning::Vault client fallback token has an invalid format; Vault integration will be degraded."
    exit 1
  fi

  # Escape curl's quoted config-string delimiters. printf is a Bash builtin, so
  # the token is written to the pipe, not passed as an external process argument.
  curl_token="${token//\\/\\\\}"
  curl_token="${curl_token//\"/\\\"}"
  if ! printf 'header = "Authorization: Bearer %s"\n' "$curl_token" |
      timeout --signal=TERM --kill-after=5s 20s \
        curl --fail --silent --show-error --connect-timeout 5 --max-time 15 \
          --config - \
          --header 'Accept: application/vnd.github.raw+json' \
          --output "$tmp" "$api_url" 2>/dev/null; then
    echo "::warning::Could not download the Vault client with the scoped fallback token; Vault integration will be degraded."
    exit 1
  fi
  unset token curl_token
  [[ -s "$tmp" ]] || { echo "::warning::Downloaded Vault client is empty."; exit 1; }
  echo "VAULT_CLIENT_SOURCE=token-fallback"
else
  echo "::warning::Could not download the Vault client anonymously and no fallback token is available; Vault integration will be degraded."
  exit 1
fi

[[ -s "$tmp" ]] || { echo "::warning::Downloaded Vault client is empty."; exit 1; }
node --check "$tmp"
install -m 600 "$tmp" "$dest"

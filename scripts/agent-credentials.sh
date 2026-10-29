#!/usr/bin/env bash
set -Eeuo pipefail

SELF_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
CONFIG_FILE="${CREDENTIAL_VAULT_CONFIG_FILE:-/dev/shm/agent-credential-vault.json}"

usage() {
  cat <<'USAGE'
Usage:
  agent-credentials.sh list
  agent-credentials.sh get CREDENTIAL_NAME
  agent-credentials.sh exec CREDENTIAL_NAME ENV_NAME -- COMMAND [ARG ...]

Use 'exec' to pass a credential to one child process without printing it.
'get' intentionally writes the raw credential to stdout.
USAGE
}

fail() {
  printf 'CREDENTIAL_VAULT_ERROR=%s\n' "$1" >&2
  exit "${2:-2}"
}

load_config() {
  VAULT_URL="${CREDENTIAL_VAULT_URL:-}"
  VAULT_TOKEN="${CREDENTIAL_VAULT_TOKEN:-}"
  # Do not pass the bearer token to curl via its environment or argv.
  unset CREDENTIAL_VAULT_URL CREDENTIAL_VAULT_TOKEN

  if [[ -z "$VAULT_URL" || -z "$VAULT_TOKEN" ]]; then
    [[ -r "$CONFIG_FILE" ]] || fail "not_configured" 3
    local config_json
    config_json="$(python3 - "$CONFIG_FILE" <<'PYCONF'
import json, pathlib, sys
try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text())
    url = value.get("url")
    token = value.get("token")
    if not isinstance(url, str) or not isinstance(token, str):
        raise ValueError()
    print(json.dumps([url, token], separators=(",", ":")))
except Exception:
    print("Invalid vault configuration.", file=sys.stderr)
    raise SystemExit(1)
PYCONF
    )" || fail "invalid_local_config" 3
    local -a values
    mapfile -t values < <(python3 -c 'import json,sys; print(json.loads(sys.stdin.read())[0]); print(json.loads(sys.stdin.read())[1])' <<<"$config_json" 2>/dev/null)
    # Parse the JSON in one process; the token is a single-line base64url value.
    if (("${#values[@]}" != 2)); then
      mapfile -t values < <(python3 -c 'import json,sys; x=json.load(sys.stdin); print(x[0]); print(x[1])' <<<"$config_json")
    fi
    (("${#values[@]}" == 2)) || fail "invalid_local_config" 3
    [[ -n "$VAULT_URL" ]] || VAULT_URL="${values[0]}"
    [[ -n "$VAULT_TOKEN" ]] || VAULT_TOKEN="${values[1]}"
    unset config_json values
  fi

  VAULT_URL="${VAULT_URL%/}"
  [[ "$VAULT_URL" =~ ^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$ ]] || fail "invalid_vault_url" 3
  [[ "$VAULT_TOKEN" =~ ^[A-Za-z0-9._~-]{32,256}$ ]] || fail "invalid_vault_token" 3
}

validate_name() {
  [[ "${1:-}" =~ ^[A-Z][A-Z0-9_]{0,79}$ ]] || fail "invalid_credential_name" 2
}

api_get() {
  local path="$1"
  # The bearer token is passed to curl through stdin config, never command args.
  curl --config - --url "${VAULT_URL}${path}" <<CURLCONF
silent
show-error
fail
connect-timeout = 5
max-time = 20
header = "Authorization: Bearer ${VAULT_TOKEN}"
header = "Accept: application/json"
CURLCONF
}

extract_names() {
  python3 -c '
import json, re, sys
try:
    data = json.load(sys.stdin)
    names = data["credentials"]
    if not isinstance(names, list) or any(not isinstance(n, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", n) for n in names):
        raise ValueError()
    for name in names:
        print(name)
except Exception:
    print("Credential vault returned an invalid response.", file=sys.stderr)
    raise SystemExit(1)
'
}

extract_value() {
  python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    if not isinstance(data.get("name"), str) or not isinstance(data.get("value"), str) or not data["value"]:
        raise ValueError()
    sys.stdout.write(data["value"])
except Exception:
    print("Credential vault returned an invalid response.", file=sys.stderr)
    raise SystemExit(1)
'
}

command_name="${1:-help}"
if (($# > 0)); then shift; fi
load_config

case "$command_name" in
  list)
    (($# == 0)) || fail "unexpected_arguments" 2
    api_get "/v1/credentials" | extract_names
    ;;
  get)
    (($# == 1)) || fail "usage" 2
    name="$1"
    validate_name "$name"
    api_get "/v1/credentials/$name" | extract_value
    printf '\n'
    ;;
  exec)
    (($# >= 4)) || fail "usage" 2
    name="$1"
    env_name="$2"
    shift 2
    [[ "${1:-}" == "--" ]] || fail "usage" 2
    shift
    (($# > 0)) || fail "missing_command" 2
    validate_name "$name"
    [[ "$env_name" =~ ^[A-Z_][A-Z0-9_]*$ ]] || fail "invalid_environment_name" 2

    credential_file="$(mktemp "${TMPDIR:-/dev/shm}/agent-vault-credential.XXXXXX")"
    chmod 600 "$credential_file"
    trap 'rm -f -- "$credential_file"' EXIT
    api_get "/v1/credentials/$name" | extract_value > "$credential_file"
    python3 - "$credential_file" "$env_name" "$@" <<'PYEXEC'
import os, pathlib, sys
path, env_name, *command = sys.argv[1:]
try:
    value = pathlib.Path(path).read_text()
    if not value or "\x00" in value:
        raise ValueError()
    os.environ[env_name] = value
    pathlib.Path(path).unlink(missing_ok=True)
    os.execvp(command[0], command)
except Exception:
    print("Could not start command with the requested credential.", file=sys.stderr)
    raise SystemExit(1)
PYEXEC
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

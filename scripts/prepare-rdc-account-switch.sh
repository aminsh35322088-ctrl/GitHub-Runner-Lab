#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"
: "${SELF_RUN_ID:?SELF_RUN_ID is required}"

WORKFLOW="${WORKFLOW:-rdc-lab.yml}"
RUNS_API="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/runs"
RUN_API="https://api.github.com/repos/${REPO}/actions/runs"
AUTH=(
  -H "Accept: application/vnd.github+json"
  -H "Authorization: Bearer ${GH_TOKEN}"
  -H "X-GitHub-Api-Version: 2022-11-28"
)

fetch_others() {
  curl --connect-timeout 10 --max-time 30 -fsSL "${AUTH[@]}" "${RUNS_API}?per_page=30" \
    | jq --arg self "$SELF_RUN_ID" \
      '[.workflow_runs[] | select((.id | tostring) != $self and .status != "completed")]'
}

cancel_run() {
  local id="$1" code
  code="$(curl --connect-timeout 10 --max-time 30 -sS -o /tmp/rdc-switch-cancel.json -w '%{http_code}' \
    -X POST "${AUTH[@]}" "${RUN_API}/${id}/cancel" || true)"
  case "$code" in
    202|409|404)
      echo "RDC Lab run ${id}: cancellation accepted/already settled (HTTP ${code})."
      ;;
    *)
      echo "Failed to cancel RDC Lab run ${id} (HTTP ${code})." >&2
      cat /tmp/rdc-switch-cancel.json 2>/dev/null || true
      return 1
      ;;
  esac
}

echo "Preparing a safe RDC account switch. Existing encrypted account state will not be modified yet."

# The previous long-lived run can queue a successor while it is shutting down.
# Require two consecutive empty polls so both the old run and any late successor
# are settled before interactive authorization begins.
empty_polls=0
for attempt in $(seq 1 24); do
  others="$(fetch_others)"
  count="$(jq 'length' <<<"$others")"

  if [ "$count" -eq 0 ]; then
    empty_polls=$((empty_polls + 1))
    if [ "$empty_polls" -ge 2 ]; then
      echo "No competing RDC Lab run remains. Safe to authorize the new account."
      exit 0
    fi
    sleep 5
    continue
  fi

  empty_polls=0
  echo "Stopping ${count} existing/queued RDC Lab run(s) before account switch."
  while read -r id; do
    [ -n "$id" ] && cancel_run "$id"
  done < <(jq -r '.[].id' <<<"$others")
  sleep 5
done

echo "Existing RDC Lab runs did not settle within 120 seconds; refusing to switch accounts to avoid a state race." >&2
exit 1

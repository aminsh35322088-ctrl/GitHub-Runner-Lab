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
      '[.workflow_runs[] | select((.id | tostring) != $self and .status != "completed") | {id, status}]'
}

request_cancel() {
  local id="$1" endpoint="$2" label="$3" code body
  body="$(mktemp)"
  code="$(curl --connect-timeout 10 --max-time 30 -sS -o "$body" -w '%{http_code}' \
    -X POST "${AUTH[@]}" "${RUN_API}/${id}/${endpoint}" || true)"
  case "$code" in
    202)
      echo "RDC Lab run ${id}: ${label} accepted (HTTP 202)."
      ;;
    409|404)
      echo "RDC Lab run ${id}: already settled while requesting ${label} (HTTP ${code})."
      ;;
    *)
      echo "Failed to request ${label} for RDC Lab run ${id} (HTTP ${code})." >&2
      cat "$body" >&2 2>/dev/null || true
      rm -f "$body"
      return 1
      ;;
  esac
  rm -f "$body"
}

echo "Preparing a safe RDC account switch. Existing encrypted account state will not be modified yet."

# Track cancellation state per run. A 202 response only means GitHub accepted
# the request; repeatedly POSTing /cancel does not make settlement faster and
# floods the log. If normal cancellation remains stuck, escalate once to the
# documented force-cancel endpoint.
declare -A cancel_requested_at=()
declare -A force_requested=()
empty_polls=0

for _ in $(seq 1 60); do
  others="$(fetch_others)"
  count="$(jq 'length' <<<"$others")"

  if [ "$count" -eq 0 ]; then
    empty_polls=$((empty_polls + 1))
    if [ "$empty_polls" -ge 2 ]; then
      echo "No competing RDC Lab run remains. Safe to authorize the new account."
      exit 0
    fi
    sleep 3
    continue
  fi

  empty_polls=0
  now="$(date +%s)"

  while IFS=$'\t' read -r id status; do
    [ -n "$id" ] || continue

    if [[ -z "${cancel_requested_at[$id]:-}" ]]; then
      echo "Stopping RDC Lab run ${id} before account switch (status=${status})."
      request_cancel "$id" cancel "cancellation"
      cancel_requested_at[$id]="$now"
      continue
    fi

    age=$((now - cancel_requested_at[$id]))
    if (( age >= 15 )) && [[ -z "${force_requested[$id]:-}" ]]; then
      echo "RDC Lab run ${id} is still ${status} after ${age}s; escalating to force-cancel once."
      request_cancel "$id" force-cancel "force-cancellation"
      force_requested[$id]=1
      continue
    fi

    if [[ -n "${force_requested[$id]:-}" ]]; then
      echo "Waiting for RDC Lab run ${id} to settle after force-cancel (status=${status})."
    else
      echo "Waiting for RDC Lab run ${id} to settle (status=${status}, ${age}s since cancel request)."
    fi
  done < <(jq -r '.[] | [.id, .status] | @tsv' <<<"$others")

  sleep 3
done

echo "Existing RDC Lab runs did not settle within the safety window; refusing to switch accounts to avoid a state race." >&2
exit 1

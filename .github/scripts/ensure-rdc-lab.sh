#!/usr/bin/env bash
set -Eeuo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"

WORKFLOW="${WORKFLOW:-rdc-lab.yml}"
REF="${REF:-main}"
SELF_RUN_ID="${SELF_RUN_ID:-}"
DISABLED="${DISABLED:-false}"
QUEUED_TIMEOUT_MINUTES="${QUEUED_TIMEOUT_MINUTES:-15}"
STALE_AGE_MINUTES="${STALE_AGE_MINUTES:-375}"
MIN_HEALTHY_MINUTES="${MIN_HEALTHY_MINUTES:-15}"
MAX_SHORT_FAILURES="${MAX_SHORT_FAILURES:-3}"

API="https://api.github.com/repos/${REPO}"
RUNS_API="${API}/actions/workflows/${WORKFLOW}/runs"
AUTH=(
  -H "Accept: application/vnd.github+json"
  -H "Authorization: Bearer ${GH_TOKEN}"
  -H "X-GitHub-Api-Version: 2022-11-28"
)

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
warn() { printf '::warning::%s\n' "$*"; }

get_runs() {
  local json attempt
  for attempt in 1 2 3 4 5; do
    json="$(curl -fsSL "${AUTH[@]}" "${RUNS_API}?per_page=30" 2>/dev/null || true)"
    if [ -n "$json" ] && jq -e '.workflow_runs' >/dev/null 2>&1 <<<"$json"; then
      printf '%s' "$json"
      return 0
    fi
    sleep $((attempt * 3))
  done
  return 1
}

others() {
  jq --arg self "$SELF_RUN_ID" '[.workflow_runs[] | select((.id | tostring) != $self)]' <<<"$1"
}

minutes_since() {
  local ts now
  ts="$(date -d "$1" +%s 2>/dev/null || echo 0)"
  [ "$ts" -gt 0 ] || { echo 0; return; }
  now="$(date +%s)"
  echo $(( (now - ts) / 60 ))
}

cancel_run() {
  local id="$1" code
  code="$(curl -sS -o /tmp/rdc-cancel.json -w '%{http_code}' -X POST \
    "${AUTH[@]}" "${API}/actions/runs/${id}/cancel" || true)"
  case "$code" in
    202|409|404) log "Cancel ${id}: HTTP ${code}" ;;
    *) warn "Failed to cancel run ${id} (HTTP ${code})"; return 1 ;;
  esac
}

dispatch_run() {
  local code
  code="$(curl -sS -o /tmp/rdc-dispatch.json -w '%{http_code}' -X POST \
    "${AUTH[@]}" -H 'Content-Type: application/json' \
    "${API}/actions/workflows/${WORKFLOW}/dispatches" \
    -d "$(jq -nc --arg ref "$REF" '{ref:$ref}')" || true)"
  case "$code" in
    200|204) log "Dispatch accepted (HTTP ${code})" ;;
    *) warn "Failed to dispatch ${WORKFLOW} (HTTP ${code})"; cat /tmp/rdc-dispatch.json 2>/dev/null || true; return 1 ;;
  esac
}

confirm_dispatch() {
  local attempt runs o count
  for attempt in 1 2 3 4 5 6; do
    sleep 5
    runs="$(get_runs)" || continue
    o="$(others "$runs")"
    count="$(jq '[.[] | select(.status != "completed")] | length' <<<"$o")"
    if [ "$count" -gt 0 ]; then
      log "Successor run is visible."
      return 0
    fi
  done
  warn "Dispatch was accepted but no successor became visible within 30s."
  return 0
}

short_failure_streak() {
  jq -r --argjson limit "$MAX_SHORT_FAILURES" --argjson min "$MIN_HEALTHY_MINUTES" '
    [ .[] | select(.status == "completed")
      | select(.conclusion == "failure" or .conclusion == "startup_failure" or .conclusion == "timed_out") ]
    | sort_by(.created_at) | reverse | .[0:$limit]
    | if length < $limit then 0
      else [ .[] | ((((.updated_at | fromdateiso8601) - ((.run_started_at // .created_at) | fromdateiso8601)) / 60) as $d | select($d < $min)) ] | length
      end
  ' <<<"$1"
}

main() {
  case "${DISABLED,,}" in
    true|1|yes) log "RDC_LAB_DISABLED is enabled; not dispatching."; return 0 ;;
  esac

  local runs o active pending active_count pending_count
  runs="$(get_runs)" || { warn "GitHub API unavailable; a later watchdog pass will retry."; return 0; }
  o="$(others "$runs")"
  active="$(jq '[.[] | select(.status == "in_progress")] | sort_by(.created_at) | reverse' <<<"$o")"
  pending="$(jq '[.[] | select(.status != "in_progress" and .status != "completed")] | sort_by(.created_at) | reverse' <<<"$o")"
  active_count="$(jq 'length' <<<"$active")"
  pending_count="$(jq 'length' <<<"$pending")"

  if [ "$active_count" -gt 0 ]; then
    local id started age
    id="$(jq -r '.[0].id' <<<"$active")"
    started="$(jq -r '.[0].run_started_at // .[0].created_at' <<<"$active")"
    age="$(minutes_since "$started")"
    log "RDC Lab run ${id} is active (age=${age}m)."
    if [ "$age" -ge "$STALE_AGE_MINUTES" ]; then
      warn "Run ${id} exceeded ${STALE_AGE_MINUTES}m; replacing it."
      cancel_run "$id" || return 1
      for _ in $(seq 1 12); do
        sleep 5
        runs="$(get_runs)" || continue
        if ! jq -e --argjson id "$id" '.workflow_runs[] | select(.id == $id and .status == "in_progress")' >/dev/null <<<"$runs"; then
          dispatch_run && confirm_dispatch
          return 0
        fi
      done
      warn "Cancelled run has not settled yet; not starting a duplicate."
    fi
    return 0
  fi

  if [ "$pending_count" -gt 0 ]; then
    local id age
    id="$(jq -r '.[0].id' <<<"$pending")"
    age="$(minutes_since "$(jq -r '.[0].created_at' <<<"$pending")")"
    log "Successor ${id} is pending (age=${age}m)."
    if [ -z "$SELF_RUN_ID" ] && [ "$age" -ge "$QUEUED_TIMEOUT_MINUTES" ]; then
      warn "Pending run ${id} is stuck with no active run; replacing it."
      cancel_run "$id" || return 1
      sleep 5
      dispatch_run && confirm_dispatch
    fi
    return 0
  fi

  local streak
  streak="$(short_failure_streak "$o")"
  if [ "$streak" -ge "$MAX_SHORT_FAILURES" ]; then
    warn "Last ${MAX_SHORT_FAILURES} runs failed quickly; crash-loop guard stopped automatic relaunch."
    return 0
  fi

  log "No RDC Lab run is active or queued; dispatching one."
  dispatch_run && confirm_dispatch
}

main "$@"

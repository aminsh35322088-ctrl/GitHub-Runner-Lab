#!/usr/bin/env bash
# Keep exactly one RDC Lab workflow either active or queued.
# This is intentionally modeled on the proven Tailscale Exit Node handover logic.

set -Eeuo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPO:?REPO is required}"

WORKFLOW="${WORKFLOW:-rdc-lab.yml}"
REF="${REF:-main}"
SELF_RUN_ID="${SELF_RUN_ID:-}"
QUEUED_TIMEOUT_MINUTES="${QUEUED_TIMEOUT_MINUTES:-15}"
STALE_AGE_MINUTES="${STALE_AGE_MINUTES:-375}"
STALE_UPDATE_MINUTES="${STALE_UPDATE_MINUTES:-20}"
MIN_HEALTHY_MINUTES="${MIN_HEALTHY_MINUTES:-15}"
MAX_SHORT_FAILURES="${MAX_SHORT_FAILURES:-3}"
DISABLED="${DISABLED:-false}"
DRY_RUN="${DRY_RUN:-false}"

RUNS_API="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/runs"
RUN_API="https://api.github.com/repos/${REPO}/actions/runs"
DISPATCH_API="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches"

AUTH=(
  -H "Accept: application/vnd.github+json"
  -H "Authorization: Bearer ${GH_TOKEN}"
  -H "X-GitHub-Api-Version: 2022-11-28"
)

log()  { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
warn() { printf '::warning::%s\n' "$*"; }

get_runs() {
  local json attempt
  for attempt in 1 2 3 4 5; do
    json="$(curl -fsSL "${AUTH[@]}" "${RUNS_API}?per_page=30" 2>/dev/null || true)"
    if [ -n "$json" ] && jq -e '.workflow_runs' >/dev/null 2>&1 <<<"$json"; then
      printf '%s' "$json"
      return 0
    fi
    log "GitHub API unreachable, retry ${attempt}/5..."
    sleep $((attempt * 3))
  done
  return 1
}

cancel_run() {
  local run_id="$1" code
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would cancel run ${run_id}"
    return 0
  fi
  code="$(curl -sS -o /tmp/ensure-rdc-cancel.json -w '%{http_code}' \
    -X POST "${AUTH[@]}" "${RUN_API}/${run_id}/cancel" || true)"
  case "$code" in
    202|409|404) log "Cancel of run ${run_id} accepted/already settled (HTTP ${code})." ;;
    *) warn "Failed to cancel run ${run_id} (HTTP ${code})."; return 1 ;;
  esac
}

others() {
  jq --arg self "$SELF_RUN_ID" \
    '[.workflow_runs[] | select((.id | tostring) != $self)]' <<<"$1"
}

active_runs() {
  jq '[.[] | select(.status == "in_progress")] | sort_by(.created_at) | reverse' <<<"$1"
}

pending_runs() {
  jq '[.[] | select(.status != "in_progress" and .status != "completed")] | sort_by(.created_at) | reverse' <<<"$1"
}

minutes_since() {
  local iso="$1" ts now
  ts="$(date -d "$iso" +%s 2>/dev/null || echo 0)"
  [ "$ts" -gt 0 ] || { echo 0; return; }
  now="$(date +%s)"
  echo $(( (now - ts) / 60 ))
}

short_failure_streak() {
  local others_json="$1"
  jq -r \
    --argjson limit "$MAX_SHORT_FAILURES" \
    --argjson min_healthy "$MIN_HEALTHY_MINUTES" '
      [ .[]
        | select(.status == "completed")
        | select(.conclusion == "success" or .conclusion == "failure"
                 or .conclusion == "startup_failure" or .conclusion == "timed_out")
      ]
      | sort_by(.created_at) | reverse
      | .[0:$limit] as $recent
      | if ($recent | length) < $limit then
          0
        else
          [ $recent[]
            | ((((.updated_at | fromdateiso8601)
                 - ((.run_started_at // .created_at) | fromdateiso8601)) / 60) as $dur
               | select(.conclusion != "success" and $dur < $min_healthy))
          ] | length
        end
    ' <<<"$others_json"
}

dispatch_run() {
  local code
  if [ "$DRY_RUN" = "true" ]; then
    log "DRY_RUN: would dispatch ${WORKFLOW} on ${REF}"
    return 0
  fi
  code="$(curl -sS -o /tmp/ensure-rdc-dispatch.json -w '%{http_code}' \
    -X POST "${AUTH[@]}" \
    -H 'Content-Type: application/json' \
    "$DISPATCH_API" \
    -d "$(jq -nc --arg ref "$REF" '{ref: $ref}')" || true)"
  case "$code" in
    204|200) log "Dispatch accepted (HTTP ${code})." ;;
    *) warn "Failed to dispatch ${WORKFLOW} (HTTP ${code})."; cat /tmp/ensure-rdc-dispatch.json 2>/dev/null || true; return 1 ;;
  esac
}

confirm_dispatch() {
  local attempt runs others_json count
  [ "$DRY_RUN" = "true" ] && return 0
  for attempt in 1 2 3 4 5 6; do
    sleep 5
    runs="$(get_runs)" || continue
    others_json="$(others "$runs")"
    count="$(jq '[.[] | select(.status != "completed")] | length' <<<"$others_json")"
    if [ "$count" -gt 0 ]; then
      log "Successor run is present:"
      jq -r '.[] | select(.status != "completed") | "  run \(.id) status=\(.status)"' <<<"$others_json"
      return 0
    fi
    log "Waiting for dispatched run to appear... ${attempt}/6"
  done
  warn "Dispatch accepted but no run became visible within 30s. A later backstop will retry."
  return 0
}

main() {
  log "Repository : ${REPO}"
  log "Workflow   : ${WORKFLOW}"
  [ -n "$SELF_RUN_ID" ] && log "Excluding  : run ${SELF_RUN_ID} (caller)"

  case "${DISABLED,,}" in
    true|1|yes)
      log "RDC_LAB_DISABLED is set. Not dispatching; relaunch chain stops here."
      return 0
      ;;
  esac

  local runs others_json active pending active_count pending_count
  runs="$(get_runs)" || {
    warn "GitHub API unreachable. Doing nothing; a later watchdog pass will retry."
    return 0
  }

  others_json="$(others "$runs")"
  active="$(active_runs "$others_json")"
  pending="$(pending_runs "$others_json")"
  active_count="$(jq 'length' <<<"$active")"
  pending_count="$(jq 'length' <<<"$pending")"

  log "Active runs  : ${active_count}"
  log "Pending runs : ${pending_count}"

  if [ "$active_count" -gt 0 ]; then
    local id created updated age silence
    id="$(jq -r '.[0].id' <<<"$active")"
    created="$(jq -r '.[0].run_started_at // .[0].created_at' <<<"$active")"
    updated="$(jq -r '.[0].updated_at' <<<"$active")"
    age="$(minutes_since "$created")"
    silence="$(minutes_since "$updated")"
    log "Run ${id}: age=${age}m, last update ${silence}m ago"

    # Same double guard as the Exit Node: a long-running job is only considered
    # stuck when it is both overdue and stale.
    if [ "$age" -ge "$STALE_AGE_MINUTES" ] && [ "$silence" -ge "$STALE_UPDATE_MINUTES" ]; then
      warn "Run ${id} is stuck (age ${age}m, silent ${silence}m). Cancelling it."
      cancel_run "$id" || return 1

      local attempt still
      for attempt in $(seq 1 12); do
        sleep 5
        runs="$(get_runs)" || continue
        still="$(jq --argjson id "$id" \
          '[.workflow_runs[] | select(.id == $id and .status == "in_progress")] | length' <<<"$runs")"
        if [ "$still" -eq 0 ]; then
          log "Run ${id} has left in_progress."
          dispatch_run || return 1
          confirm_dispatch
          return 0
        fi
        log "Waiting for cancellation to settle... ${attempt}/12"
      done
      warn "Run ${id} is still active after 60s. Not dispatching a duplicate."
      return 0
    fi

    log "RDC Lab is healthy. Nothing to do."
    return 0
  fi

  if [ "$pending_count" -gt 0 ]; then
    local pid page pstatus
    pid="$(jq -r '.[0].id' <<<"$pending")"
    pstatus="$(jq -r '.[0].status' <<<"$pending")"
    page="$(minutes_since "$(jq -r '.[0].created_at' <<<"$pending")")"
    log "Run ${pid} is waiting (status=${pstatus}, age=${page}m)."

    # A successor queued by the current run is expected to wait on concurrency.
    if [ -z "$SELF_RUN_ID" ] && [ "$page" -ge "$QUEUED_TIMEOUT_MINUTES" ]; then
      warn "Run ${pid} has waited ${page}m with nothing active. Cancelling and replacing it."
      cancel_run "$pid" || return 1
      sleep 5
      dispatch_run || return 1
      confirm_dispatch
      return 0
    fi

    log "Successor already queued. Nothing to do."
    return 0
  fi

  local streak
  streak="$(short_failure_streak "$others_json")"
  if [ "$streak" -ge "$MAX_SHORT_FAILURES" ]; then
    warn "The last ${MAX_SHORT_FAILURES} RDC Lab runs all failed in under ${MIN_HEALTHY_MINUTES}m."
    warn "Refusing to dispatch to avoid a crash loop."
    return 0
  fi

  log "No RDC Lab run is active or queued. Dispatching a new run."
  dispatch_run || return 1
  confirm_dispatch
}

main "$@"

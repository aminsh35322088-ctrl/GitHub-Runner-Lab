#!/usr/bin/env python3
"""Publish a GitHub Actions runtime snapshot for the RDC Runner Lab."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import statistics
import subprocess
import time
import urllib.request
from urllib.parse import urlencode

BEGIN = "<!-- RDC-LAB-STATUS:START -->"
END = "<!-- RDC-LAB-STATUS:END -->"
UTC = dt.timezone.utc
WORKFLOW = "rdc-lab.yml"
JOB_NAME = "runner-lab"
KEEP_STEPS = ("Prewarm Agent Toolchain and Keep RDC Lab Alive", "Keep RDC Lab Alive")
VERIFY_STEP = "Verify connection"
SAMPLE_RUNS = 20
HANDOFF_TARGET_MINUTES = 15


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def utc(value):
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "—"


def api(path):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "rdc-lab-status",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com" + path, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def nominal_minutes():
    text = Path(".github/workflows/rdc-lab.yml").read_text()
    match = re.search(r"^  ONLINE_MINUTES:\s*(\d+)\s*$", text, re.M)
    return int(match.group(1)) if match else 330


def duration_minutes(run):
    start = timestamp(run.get("run_started_at") or run.get("created_at"))
    end = timestamp(run.get("updated_at"))
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds() / 60)


def keep_step(job):
    steps = {s.get("name"): s for s in job.get("steps", [])}
    return next((steps[name] for name in KEEP_STEPS if name in steps), {})


def reliability(repo, runs, online_minutes):
    threshold = max(1, online_minutes - 20)
    records = []

    candidates = [
        r for r in runs
        if r.get("status") in ("completed", "in_progress")
        and r.get("conclusion") != "cancelled"
    ][: SAMPLE_RUNS + 5]

    for run in candidates:
        try:
            jobs = api(f'/repos/{repo}/actions/runs/{run["id"]}/jobs?filter=latest&per_page=100')["jobs"]
        except Exception:
            continue
        job = next((j for j in jobs if j.get("name") == JOB_NAME), None)
        if not job or not job.get("started_at"):
            continue

        keep = keep_step(job)
        started = timestamp(job.get("started_at"))
        completed = timestamp(job.get("completed_at"))
        keep_started = timestamp(keep.get("started_at"))
        keep_completed = timestamp(keep.get("completed_at"))
        keep_minutes = (
            max(0.0, (keep_completed - keep_started).total_seconds() / 60)
            if keep_started and keep_completed
            else None
        )
        records.append({
            "run_id": run["id"],
            "run_status": run.get("status"),
            "run_conclusion": run.get("conclusion"),
            "job_started": started,
            "job_completed": completed,
            "job_conclusion": job.get("conclusion"),
            "keep_conclusion": keep.get("conclusion"),
            "keep_minutes": keep_minutes,
        })

    completed_cycles = [
        r for r in records
        if r["run_status"] == "completed" and r["keep_minutes"] is not None
    ][:SAMPLE_RUNS]
    good_cycles = [
        r for r in completed_cycles
        if r["run_conclusion"] == "success"
        and r["job_conclusion"] == "success"
        and r["keep_conclusion"] == "success"
        and r["keep_minutes"] >= threshold
    ]
    cycle_rate = (
        round(100 * len(good_cycles) / len(completed_cycles))
        if completed_cycles else None
    )

    chronological = sorted(records, key=lambda r: r["job_started"])
    gaps = []
    for previous, current in zip(chronological, chronological[1:]):
        if not previous["job_completed"] or not current["job_started"]:
            continue
        gap = (current["job_started"] - previous["job_completed"]).total_seconds() / 60
        if gap < 0:
            continue
        gaps.append(gap)
    gaps = gaps[-SAMPLE_RUNS:]
    handoff_rate = (
        round(100 * sum(g <= HANDOFF_TARGET_MINUTES for g in gaps) / len(gaps))
        if gaps else None
    )
    median_gap = round(statistics.median(gaps), 1) if gaps else None

    return {
        "cycle_rate": cycle_rate,
        "cycle_sample": len(completed_cycles),
        "handoff_rate": handoff_rate,
        "handoff_sample": len(gaps),
        "median_gap": median_gap,
    }

def work_mode(remaining):
    if remaining is None:
        return "unknown"
    if remaining <= 0:
        return "due"
    if remaining <= 15:
        return "imminent"
    if remaining <= 30:
        return "checkpoint"
    if remaining <= 60:
        return "caution"
    return "safe"


def collect(repo, branch, now):
    online_minutes = nominal_minutes()
    query = urlencode({"per_page": 50, "branch": branch})
    runs = api(f"/repos/{repo}/actions/workflows/{WORKFLOW}/runs?{query}")["workflow_runs"]
    runs = [
        r for r in runs
        if r.get("head_repository", {}).get("full_name") == repo
        and r.get("event") in ("workflow_dispatch", "schedule")
    ]
    runs.sort(key=lambda r: (r.get("created_at", ""), r["id"]), reverse=True)
    stats = reliability(repo, runs, online_minutes)

    active = [r for r in runs if r.get("status") == "in_progress"]
    queued = [r for r in runs if r.get("status") not in ("completed", "in_progress")]
    run = next(iter(active or queued or runs), None)

    state = {
        "state": "idle",
        "checked": now.isoformat(),
        "run_id": None,
        "run_url": None,
        "started": None,
        "elapsed": None,
        "remaining": None,
        "handoff": None,
        "work_mode": "unknown",
        "successor_queued": bool(queued),
        **stats,
    }
    if not run:
        return state

    state["run_id"] = run["id"]
    state["run_url"] = run["html_url"]

    if run.get("status") == "completed":
        state["state"] = "failed" if run.get("conclusion") not in ("success", "neutral") else "idle"
        return state
    if run.get("status") != "in_progress":
        state["state"] = "queued"
        return state

    state["state"] = "starting"
    jobs = api(f'/repos/{repo}/actions/runs/{run["id"]}/jobs?filter=latest&per_page=100')["jobs"]
    job = next((j for j in jobs if j.get("name") == JOB_NAME), {})
    steps = {s.get("name"): s for s in job.get("steps", [])}
    keep = keep_step(job)
    verify = steps.get(VERIFY_STEP, {})

    if (
        job.get("status") == "in_progress"
        and keep.get("status") == "in_progress"
        and verify.get("conclusion") == "success"
        and keep.get("started_at")
    ):
        start = timestamp(keep["started_at"])
        handoff = start + dt.timedelta(minutes=online_minutes)
        elapsed = max(0, int((now - start).total_seconds() / 60))
        remaining = max(0, int((handoff - now).total_seconds() / 60))
        state.update(
            state="running",
            started=start.isoformat(),
            elapsed=elapsed,
            remaining=remaining,
            handoff=handoff.isoformat(),
            work_mode=work_mode(remaining),
        )
    elif keep.get("status") == "completed" or job.get("status") == "completed":
        state["state"] = "handover"
    return state


STATE_LABELS = {
    "running": "🟢 RDC verified · keepalive running",
    "starting": "🟡 Runner starting · RDC not yet verified",
    "queued": "🟡 Successor queued / waiting for a runner",
    "handover": "🟠 Handover in progress",
    "idle": "⚪ No active Runner Lab run observed",
    "failed": "🔴 Latest run failed · no active run observed",
    "unknown": "⚪ Unknown · GitHub API check failed",
}

MODE_LABELS = {
    "safe": "🟢 SAFE — normal work window",
    "caution": "🟡 CAUTION — finish bounded work only",
    "checkpoint": "🟠 CHECKPOINT — stop starting heavy work; push/snapshot now",
    "imminent": "🔴 HANDOFF IMMINENT — stop heavy work and finalize",
    "due": "🔴 HANDOFF DUE — current runner should be replaced",
    "unknown": "⚪ UNKNOWN",
}


def pct(value, sample):
    return f"{value}% ({sample} samples)" if value is not None else "—"


def render(state, repo):
    run_link = state.get("run_url") or f"https://github.com/{repo}/actions"
    remaining = f'{state["remaining"]} min' if state.get("remaining") is not None else "—"
    elapsed = f'{state["elapsed"]} min' if state.get("elapsed") is not None else "—"
    median = f'{state["median_gap"]} min' if state.get("median_gap") is not None else "—"
    successor = "✅ yes" if state.get("successor_queued") else "—"
    note = (
        "README is a GitHub Actions snapshot refreshed on run events and about every 10 minutes. "
        "For the exact live countdown while connected, run `./scripts/agent-run.sh status`; "
        "an agent with GitHub access should also inspect the current workflow run before starting long work. "
        "Full-cycle reliability uses the actual keepalive-step duration and requires at least ONLINE_MINUTES−20; "
        "handoff reliability uses actual runner-lab job start/end times and means the next job started within 15 minutes."
    )
    rows = [
        ("Runner state", STATE_LABELS.get(state.get("state"), STATE_LABELS["unknown"])),
        ("Agent work mode", MODE_LABELS.get(state.get("work_mode"), MODE_LABELS["unknown"])),
        ("Last checked", utc(timestamp(state.get("checked")))),
        ("Keepalive started", utc(timestamp(state.get("started")))),
        ("Elapsed at this check", elapsed),
        ("Remaining to nominal handoff", remaining),
        ("Nominal handoff", utc(timestamp(state.get("handoff")))),
        ("Successor already queued", successor),
        ("Full-cycle success", pct(state.get("cycle_rate"), state.get("cycle_sample", 0))),
        ("Handoff ≤15 min", pct(state.get("handoff_rate"), state.get("handoff_sample", 0))),
        ("Median handoff gap", median),
        ("Run details", f"[Open current run]({run_link})"),
    ]
    return (
        BEGIN + "\n\n## ⏱️ Live Runner status\n\n"
        + "| Item | Value |\n| :--- | :--- |\n"
        + "\n".join(f"| {k} | {v} |" for k, v in rows)
        + "\n\n> " + note + "\n\n" + END
    )


def replace_block(text, block):
    if text.count(BEGIN) != 1 or text.count(END) != 1:
        raise ValueError("README must contain exactly one RDC Lab status marker pair")
    start = text.index(BEGIN)
    end = text.index(END)
    if end < start:
        raise ValueError("Status markers are out of order")
    return text[:start] + block + text[end + len(END):]


def write_readme(state, repo):
    path = Path("README.md")
    path.write_text(replace_block(path.read_text(), render(state, repo)))


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--snapshot")
    args = parser.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", "aminsh35322088-ctrl/GitHub-Runner-Lab")
    branch = os.environ.get("DEFAULT_BRANCH", "main")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository name")

    if args.snapshot:
        state = json.loads(Path(args.snapshot).read_text())
    else:
        state = None
        for attempt in range(4):
            try:
                state = collect(repo, branch, dt.datetime.now(UTC))
            except Exception:
                print("::warning::Runner status API check failed; publishing unknown.")
                state = {
                    "state": "unknown", "checked": dt.datetime.now(UTC).isoformat(),
                    "run_id": None, "run_url": None, "started": None,
                    "elapsed": None, "remaining": None, "handoff": None,
                    "work_mode": "unknown", "successor_queued": False,
                    "cycle_rate": None, "cycle_sample": 0, "handoff_rate": None,
                    "handoff_sample": 0, "median_gap": None,
                }
                break
            if state["state"] != "starting" or attempt == 3:
                break
            time.sleep(10)

    if not args.publish:
        write_readme(state, repo)
        return

    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(3):
        git("fetch", "origin", branch)
        git("reset", "--hard", "FETCH_HEAD")
        write_readme(state, repo)
        git("add", "--", "README.md")
        if not git("diff", "--cached", "--name-only"):
            return
        git("commit", "-m", "docs: refresh runner status")
        try:
            git("push", "origin", f"HEAD:refs/heads/{branch}")
            return
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise RuntimeError("Runner status push failed") from None


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Publish a GitHub Actions runtime and reliability snapshot for the RDC Runner Lab."""

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
STOP_STEP = "Gracefully Stop RDC"
SAMPLE_RUNS = 12
HANDOFF_TARGET_MINUTES = 15

def timestamp(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

def utc(value):
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC") if value else "—"

def api(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "rdc-lab-status",
               "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("https://api.github.com" + path, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)

def workflow_limits():
    text = Path(".github/workflows/rdc-lab.yml").read_text()
    online = re.search(r"^  ONLINE_MINUTES:\s*(\d+)\s*$", text, re.M)
    auto_handoff = re.search(r"^  AGENT_AUTO_HANDOFF_MINUTES:\s*(\d+)\s*$", text, re.M)
    timeout = re.search(r"(?ms)^  runner-lab:\s*$.*?^    timeout-minutes:\s*(\d+)\s*$", text)
    return (
        int(online.group(1)) if online else 330,
        int(timeout.group(1)) if timeout else 350,
        int(auto_handoff.group(1)) if auto_handoff else 20,
    )

def step_by_names(steps, names):
    for name in names:
        step = steps.get(name)
        if step:
            return step
    return {}

def reliability(repo, runs):
    completed = [r for r in runs if r.get("status") == "completed"][:SAMPLE_RUNS]
    verify_outcomes, keepalive_outcomes, sessions = [], [], []
    for run in completed:
        try:
            jobs = api(f'/repos/{repo}/actions/runs/{run["id"]}/jobs?filter=latest&per_page=100')["jobs"]
        except Exception:
            continue
        job = next((j for j in jobs if j.get("name") == JOB_NAME), {})
        if not job:
            continue
        steps = {s.get("name"): s for s in job.get("steps", [])}
        verify = steps.get(VERIFY_STEP, {})
        keep = step_by_names(steps, KEEP_STEPS)
        if verify.get("status") == "completed":
            verify_outcomes.append(verify.get("conclusion") == "success")
        if (run.get("conclusion") != "cancelled" and keep.get("status") == "completed"
                and keep.get("started_at")):
            keepalive_outcomes.append(keep.get("conclusion") == "success")
        stop = steps.get(STOP_STEP, {})
        ready_at = timestamp(verify.get("completed_at")) if verify.get("conclusion") == "success" else None
        stopped_at = timestamp(stop.get("completed_at") or stop.get("started_at"))
        if ready_at:
            sessions.append({"ready": ready_at, "stopped": stopped_at})

    verify_rate = round(100 * sum(verify_outcomes) / len(verify_outcomes)) if verify_outcomes else None
    keepalive_rate = round(100 * sum(keepalive_outcomes) / len(keepalive_outcomes)) if keepalive_outcomes else None

    chronological = sorted(sessions, key=lambda session: session["ready"])
    gaps = []
    for previous, current in zip(chronological, chronological[1:]):
        if previous["stopped"]:
            gaps.append(max(0.0, (current["ready"] - previous["stopped"]).total_seconds() / 60))
    gaps = gaps[-SAMPLE_RUNS:]
    handoff_rate = round(100 * sum(g <= HANDOFF_TARGET_MINUTES for g in gaps) / len(gaps)) if gaps else None
    return {
        "verify_rate": verify_rate, "verify_sample": len(verify_outcomes),
        "keepalive_rate": keepalive_rate, "keepalive_sample": len(keepalive_outcomes),
        "handoff_rate": handoff_rate, "handoff_sample": len(gaps),
        "median_gap": round(statistics.median(gaps), 1) if gaps else None,
    }

def work_mode(remaining, auto_handoff):
    if remaining is None: return "unknown"
    if remaining <= 0: return "due"
    if remaining <= auto_handoff: return "restart"
    return "safe"

def collect(repo, branch, now):
    online_minutes, timeout_minutes, auto_handoff_minutes = workflow_limits()
    query = urlencode({"per_page": 50, "branch": branch})
    runs = api(f"/repos/{repo}/actions/workflows/{WORKFLOW}/runs?{query}")["workflow_runs"]
    runs = [r for r in runs if r.get("head_repository", {}).get("full_name") == repo
            and r.get("event") in ("workflow_dispatch", "schedule")]
    runs.sort(key=lambda r: (r.get("created_at", ""), r["id"]), reverse=True)
    stats = reliability(repo, runs)
    active = [r for r in runs if r.get("status") == "in_progress"]
    queued = [r for r in runs if r.get("status") not in ("completed", "in_progress")]
    run = next(iter(active or queued or runs), None)
    state = {
        "state": "idle", "checked": now.isoformat(), "stale": False, "run_id": None, "run_url": None,
        "job_started": None, "keepalive_started": None, "elapsed": None, "remaining": None,
        "handoff": None, "hard_timeout": None, "headroom": timeout_minutes - online_minutes,
        "auto_handoff_minutes": auto_handoff_minutes,
        "work_mode": "unknown", "successor_queued": bool(queued), **stats,
    }
    if not run:
        return state
    state["run_id"], state["run_url"] = run["id"], run["html_url"]
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
    keep, verify = step_by_names(steps, KEEP_STEPS), steps.get(VERIFY_STEP, {})
    job_start = timestamp(job.get("started_at") or run.get("run_started_at"))
    if job_start:
        handoff = job_start + dt.timedelta(minutes=online_minutes)
        hard_timeout = job_start + dt.timedelta(minutes=timeout_minutes)
        effective = min(handoff, hard_timeout)
        remaining = max(0, int((effective - now).total_seconds() / 60))
        state.update(job_started=job_start.isoformat(),
                     elapsed=max(0, int((now - job_start).total_seconds() / 60)),
                     remaining=remaining, handoff=handoff.isoformat(),
                     hard_timeout=hard_timeout.isoformat(),
                     work_mode=work_mode(remaining, auto_handoff_minutes))
    if job.get("status") == "in_progress" and keep.get("status") == "in_progress" and verify.get("conclusion") == "success":
        state["state"] = "running"
        if keep.get("started_at"):
            state["keepalive_started"] = timestamp(keep["started_at"]).isoformat()
    elif keep.get("status") == "completed" or job.get("status") == "completed":
        state["state"] = "handover"
    return state

STATE_LABELS = {
    "running":"🟢 RDC verified · keepalive running","starting":"🟡 Runner starting · RDC not yet verified",
    "queued":"🟡 Successor queued / waiting for a runner","handover":"🟠 Handover in progress",
    "idle":"⚪ No active Runner Lab run observed","failed":"🔴 Latest run failed · no active run observed",
    "unknown":"⚪ Unknown · GitHub API check failed",
}
MODE_LABELS = {
    "safe":"🟢 SAFE — normal work window",
    "restart":"🟠 RESTART WINDOW — checkpoint + rotate to a fresh runner",
    "due":"🔴 HANDOFF DUE — move to successor runner","unknown":"⚪ UNKNOWN",
}

def pct(value, sample):
    return f"{value}% ({sample} samples)" if value is not None else "—"

def render(state, repo):
    run_link = state.get("run_url") or f"https://github.com/{repo}/actions"
    fmtmin = lambda key: f'{state[key]} min' if state.get(key) is not None else "—"
    successor = "✅ yes" if state.get("successor_queued") else "—"
    rows = [
        ("Runner state", STATE_LABELS.get(state.get("state"), STATE_LABELS["unknown"])),
        ("Agent work mode", MODE_LABELS.get(state.get("work_mode"), MODE_LABELS["unknown"])),
        ("Status freshness", "⚠️ stale / unavailable" if state.get("stale") else f"✅ current as of {utc(timestamp(state.get('checked')))}"),
        ("Last checked", utc(timestamp(state.get("checked")))),
        ("Runner/job started", utc(timestamp(state.get("job_started")))),
        ("Keepalive started", utc(timestamp(state.get("keepalive_started")))),
        ("Runner age", fmtmin("elapsed")),
        ("Runner lifecycle remaining", fmtmin("remaining")),
        ("Auto restart threshold", f'{state.get("auto_handoff_minutes", 20)} min remaining'),
        ("Nominal handoff", utc(timestamp(state.get("handoff")))),
        ("Hard job timeout", utc(timestamp(state.get("hard_timeout")))),
        ("Planned timeout headroom", fmtmin("headroom")),
        ("Successor already queued", successor),
        ("RDC verification success", pct(state.get("verify_rate"), state.get("verify_sample", 0))),
        ("Keepalive completion", pct(state.get("keepalive_rate"), state.get("keepalive_sample", 0))),
        ("Handoff ≤15 min", pct(state.get("handoff_rate"), state.get("handoff_sample", 0))),
        ("Median handoff gap", fmtmin("median_gap")),
        ("Run details", f"[Open current run]({run_link})"),
    ]
    note = (
        "Refreshed on workflow events and about every 10 minutes. For the exact local clock while connected, "
        "run `./scripts/agent-run.sh status`. The lifecycle clock starts during early runner setup: handoff is "
        "planned at 330 minutes with a 350-minute hard job timeout. Normal work remains SAFE until the final "
        f'{state.get("auto_handoff_minutes", 20)} minutes, when the current run checkpoints and rotates to a fresh runner. '
        "Reliability percentages are measured from "
        f"the corresponding workflow steps in up to the last {SAMPLE_RUNS} completed runs; handoff reliability "
        f"means the next run started within {HANDOFF_TARGET_MINUTES} minutes."
    )
    return (
        BEGIN
        + "\n\n## ⏱️ Live Runner status\n\n"
        + "| Item | Value |\n| :--- | :--- |\n"
        + "\n".join(f"| {k} | {v} |" for k, v in rows)
        + "\n\n> "
        + note
        + "\n\n"
        + END
    )


def replace_block(text, block):
    if text.count(BEGIN)!=1 or text.count(END)!=1: raise ValueError("README must contain exactly one RDC Lab status marker pair")
    start,end=text.index(BEGIN),text.index(END)
    if end<start: raise ValueError("Status markers are out of order")
    return text[:start]+block+text[end+len(END):]

def write_readme(state, repo):
    path=Path("README.md"); path.write_text(replace_block(path.read_text(), render(state,repo)))

def git(*args):
    return subprocess.run(["git",*args],check=True,capture_output=True,text=True).stdout.strip()

def unknown_state(now):
    return {"state":"unknown","checked":now.isoformat(),"stale":True,"run_id":None,"run_url":None,"job_started":None,
            "keepalive_started":None,"elapsed":None,"remaining":None,"handoff":None,"hard_timeout":None,
            "headroom":None,"auto_handoff_minutes":20,"work_mode":"unknown","successor_queued":False,"verify_rate":None,"verify_sample":0,
            "keepalive_rate":None,"keepalive_sample":0,"handoff_rate":None,"handoff_sample":0,"median_gap":None}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--publish",action="store_true"); parser.add_argument("--snapshot")
    args=parser.parse_args()
    repo=os.environ.get("GITHUB_REPOSITORY","aminsh35322088-ctrl/GitHub-Runner-Lab")
    branch=os.environ.get("DEFAULT_BRANCH","main")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo): raise ValueError("Invalid repository name")
    if args.snapshot:
        state=json.loads(Path(args.snapshot).read_text())
    else:
        state=None
        for attempt in range(4):
            try: state=collect(repo,branch,dt.datetime.now(UTC))
            except Exception:
                print("::warning::Runner status API check failed; publishing unknown.")
                state=unknown_state(dt.datetime.now(UTC)); break
            if state["state"]!="starting" or attempt==3: break
            time.sleep(10)
    if not args.publish:
        write_readme(state,repo); return
    git("config","user.name","github-actions[bot]")
    git("config","user.email","41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(3):
        git("fetch","origin",branch); git("reset","--hard","FETCH_HEAD"); write_readme(state,repo); git("add","--","README.md")
        if not git("diff","--cached","--name-only"): return
        git("commit","-m","docs: refresh runner status")
        try: git("push","origin",f"HEAD:refs/heads/{branch}"); return
        except subprocess.CalledProcessError:
            if attempt==2: raise RuntimeError("Runner status push failed") from None

if __name__=="__main__":
    main()

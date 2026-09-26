#!/usr/bin/env python3
"""Docker/BuildKit storage preflight and guarded cache cleanup."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from lab_common import kit, lock
from lab_jobs import all_jobs
from lab_toolset import load_manifest


def run(argv, timeout=30):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def docker_root():
    override = os.getenv("AGENT_DOCKER_ROOT")
    if override:
        return Path(override).expanduser().resolve(), "override"
    result = run(["docker", "info", "--format", "{{.DockerRootDir}}"], timeout=15)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or "docker info did not return DockerRootDir")
    return Path(result.stdout.strip()), "docker-info"


def filesystem_stats(path):
    probe = Path(path)
    while True:
        try:
            stat = os.statvfs(probe)
            free_bytes = stat.f_bavail * stat.f_frsize
            inode_free_percent = (100 * stat.f_favail / stat.f_files) if stat.f_files else 0.0
            return {
                "probe_path": str(probe),
                "free_bytes": free_bytes,
                "free_gb": round(free_bytes / (1024 ** 3), 2),
                "inode_free_percent": round(inode_free_percent, 2),
            }
        except OSError:
            if probe.parent == probe:
                raise
            probe = probe.parent


def capture(argv, timeout=30):
    try:
        result = run(argv, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": str(error)}
    return {
        "ok": result.returncode == 0,
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def buildx_usage():
    raw = capture(["docker", "buildx", "du", "--format=json"], timeout=30)
    if not raw.get("ok"):
        return raw
    total = reclaimable = records = 0
    invalid = 0
    for line in raw.get("stdout", "").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            size = int(item.get("Size", 0))
        except (json.JSONDecodeError, TypeError, ValueError):
            invalid += 1
            continue
        records += 1
        total += size
        if item.get("Reclaimable") is True:
            reclaimable += size
    return {
        "ok": invalid == 0,
        "records": records,
        "total_bytes": total,
        "reclaimable_bytes": reclaimable,
        "invalid_records": invalid,
    }


def thresholds(args):
    manifest = load_manifest()
    base = manifest.get("thresholds", {})
    min_free_gb = args.min_free_gb
    if min_free_gb is None:
        min_free_gb = float(os.getenv("AGENT_DOCKER_MIN_FREE_GB", base.get("disk_free_gb", 10)))
    min_inode = args.min_inode_free_percent
    if min_inode is None:
        min_inode = float(os.getenv("AGENT_DOCKER_MIN_INODE_FREE_PERCENT", base.get("inode_free_percent", 5)))
    return min_free_gb, min_inode


def status(args):
    root, source = docker_root()
    stats = filesystem_stats(root)
    min_free_gb, min_inode = thresholds(args)
    healthy = stats["free_gb"] >= min_free_gb and stats["inode_free_percent"] >= min_inode
    report = {
        "docker_root": str(root),
        "docker_root_source": source,
        "filesystem": stats,
        "thresholds": {
            "min_free_gb": min_free_gb,
            "min_inode_free_percent": min_inode,
        },
        "healthy": healthy,
        "docker_system_df": capture(["docker", "system", "df"], timeout=20),
        "buildx_du": buildx_usage(),
    }
    return report


def validate_age(value):
    if not re.fullmatch(r"[1-9][0-9]*[smhd]", value):
        raise ValueError(f"invalid Docker age filter: {value}")
    return value


def prune(args):
    if not args.apply:
        return {
            "applied": False,
            "message": "dry-run only; pass --apply to prune unused Docker cache",
            "before": status(args),
        }

    builder_until = validate_age(args.builder_until)
    with lock(kit() / "jobs.lock"):
        active = [job["id"] for job in all_jobs() if job["state"] in ("queued", "running")]
        if active:
            raise RuntimeError(f"refusing Docker prune while managed jobs are active: {','.join(active)}")
        before = status(args)
        min_free_gb, _ = thresholds(args)
        builder = capture(
            [
                "docker", "buildx", "prune", "--force",
                "--filter", f"until={builder_until}",
                "--min-free-space", f"{min_free_gb:g}gb",
            ],
            timeout=120,
        )
        after = status(args)
    return {
        "applied": True,
        "builder_until": builder_until,
        "builder_prune": builder,
        "before": before,
        "after": after,
        "healthy": builder.get("ok") is True and after.get("healthy") is True,
    }


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "check", "prune"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--min-free-gb", type=float)
        cmd.add_argument("--min-inode-free-percent", type=float)
        if name == "prune":
            cmd.add_argument("--apply", action="store_true")
            cmd.add_argument(
                "--builder-until",
                default=os.getenv("AGENT_DOCKER_BUILDER_PRUNE_UNTIL", "24h"),
            )
    args = parser.parse_args()

    if args.action == "prune":
        report = prune(args)
        code = 0 if not report.get("applied") or report.get("healthy") else 1
    else:
        report = status(args)
        code = 0 if args.action == "status" or report["healthy"] else 1
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"docker storage error: {error}", file=sys.stderr)
        raise SystemExit(2)

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


def container_engine():
    engine = os.getenv("AGENT_CONTAINER_ENGINE", "docker").strip().lower()
    if engine not in ("docker", "podman"):
        raise RuntimeError("AGENT_CONTAINER_ENGINE must be docker or podman")
    return engine


def engine_command(*args):
    return [container_engine(), *args]


def docker_root():
    override = os.getenv("AGENT_DOCKER_ROOT")
    if override:
        return Path(override).expanduser().resolve(), "override"
    if container_engine() == "podman":
        result = run(engine_command("info", "--format", "{{.Store.GraphRoot}}"), timeout=15)
        label = "podman-info"
    else:
        result = run(engine_command("info", "--format", "{{.DockerRootDir}}"), timeout=15)
        label = "docker-info"
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or f"{container_engine()} info did not return a storage root")
    return Path(result.stdout.strip()), label


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


def parse_size(value):
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        raise ValueError("invalid size")
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?i?B)\s*", value, re.I)
    if not match:
        raise ValueError(f"invalid size: {value}")
    number = float(match.group(1))
    unit = match.group(2).upper()
    decimal = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4, "PB": 1000**5, "EB": 1000**6}
    binary = {"KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "TIB": 1024**4, "PIB": 1024**5, "EIB": 1024**6}
    factor = decimal.get(unit, binary.get(unit))
    if factor is None:
        raise ValueError(f"unsupported size unit: {unit}")
    return int(number * factor)


def buildx_usage():
    if container_engine() == "podman":
        # Podman exposes aggregate storage via `podman system df`; it does not
        # implement Docker Buildx's cache-record API. Do not fail storage health
        # or pretend aggregate image bytes are BuildKit cache bytes.
        return {
            "ok": True,
            "supported": False,
            "engine": "podman",
            "reason": "Podman does not expose Buildx cache records",
            "records": 0,
            "total_bytes": None,
            "reclaimable_bytes": None,
            "invalid_records": 0,
        }
    raw = capture(engine_command("buildx", "du", "--format=json"), timeout=30)
    if not raw.get("ok"):
        return raw
    total = reclaimable = records = 0
    invalid = 0
    for line in raw.get("stdout", "").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            size = parse_size(item.get("Size", 0))
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
        "container_engine": container_engine(),
        "container_system_df": capture(engine_command("system", "df"), timeout=20),
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
        if container_engine() == "podman":
            # Remove only old dangling images and Podman's explicit build cache;
            # never use `podman system prune --all` or prune volumes here.
            builder = capture(
                engine_command("image", "prune", "--force", "--build-cache", "--filter", f"until={builder_until}"),
                timeout=120,
            )
        else:
            builder = capture(
                engine_command("buildx", "prune", "--force", "--filter", f"until={builder_until}",
                               "--min-free-space", f"{min_free_gb:g}gb"),
                timeout=120,
            )
        after = status(args)
    return {
        "applied": True,
        "container_engine": container_engine(),
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

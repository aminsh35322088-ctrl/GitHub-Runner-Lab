#!/usr/bin/env python3
"""One-shot, read-only runner readiness report."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from lab_common import SCRIPTS, kit, runtime
from lab_jobs import all_jobs
from lab_toolset import expand, load_manifest


def probe(command, timeout=10):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "summary": result.stdout.strip(),
            "error": result.stderr.strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"ok": False, "error": str(error)}


def memory_info():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            token = raw.strip().split()[0]
            if token.isdigit():
                values[key] = int(token) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return {
        "total_bytes": values.get("MemTotal"),
        "available_bytes": values.get("MemAvailable"),
        "swap_total_bytes": values.get("SwapTotal"),
        "swap_free_bytes": values.get("SwapFree"),
    }


def inode_info(path: Path):
    try:
        stat = os.statvfs(path)
        percent = (100 * stat.f_favail / stat.f_files) if stat.f_files else 0
        return {"free_percent": round(percent, 2), "free": stat.f_favail, "total": stat.f_files}
    except OSError as error:
        return {"error": str(error), "free_percent": 0}


def repository_name():
    value = os.getenv("GITHUB_REPOSITORY")
    if value:
        return value
    try:
        remote = subprocess.check_output(
            ["git", "-C", str(SCRIPTS.parent), "remote", "get-url", "origin"],
            text=True, stderr=subprocess.DEVNULL, timeout=5,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", remote)
    return match.group(1) if match else None


def json_probe(command, timeout=20):
    result = probe(command, timeout)
    if not result["ok"]:
        return result
    try:
        return {"ok": True, "data": json.loads(result["summary"])}
    except json.JSONDecodeError as error:
        return {"ok": False, "error": f"invalid JSON: {error}", "raw": result["summary"][-2000:]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    args = parser.parse_args()

    toolset = load_manifest()
    thresholds = toolset.get("thresholds", {})
    disk_min = int(thresholds.get("disk_free_gb", 10)) * 1024**3
    inode_min = float(thresholds.get("inode_free_percent", 5))
    home = Path.home()

    required_tools = list(dict.fromkeys([*expand(toolset, "commands", "full"), "docker"]))
    disk = shutil.disk_usage(home)
    report = {
        "runtime": runtime(),
        "disk": {
            "path": str(home),
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
            "minimum_free_bytes": disk_min,
        },
        "inodes": {**inode_info(home), "minimum_free_percent": inode_min},
        "memory": memory_info(),
        "tools": {name: bool(shutil.which(name)) for name in required_tools},
        "active_jobs": [j["id"] for j in all_jobs() if j["state"] in ("queued", "running")],
    }

    report["rdc"] = probe(["bash", str(SCRIPTS / "health.sh")])
    report["docker"] = probe(["docker", "info", "--format", "{{.ServerVersion}}"])
    report["docker_storage"] = json_probe(
        [sys.executable, str(SCRIPTS / "lab_docker_storage.py"), "status"], timeout=40
    )

    try:
        shell = f'source {str(SCRIPTS / "agent-lib.sh")!r}; agent_full_toolchain_ready'
        toolchain = subprocess.run(
            ["bash", "-lc", shell], capture_output=True, text=True, timeout=20
        )
        report["toolchain"] = {"ok": toolchain.returncode == 0}
    except (OSError, subprocess.TimeoutExpired) as error:
        report["toolchain"] = {"ok": False, "error": str(error)}

    stamp = kit() / "checkpoint-persisted-at"
    report["last_durable_checkpoint"] = stamp.read_text().strip() if stamp.exists() else None

    repository = repository_name()
    if repository:
        github = probe(
            ["bash", str(SCRIPTS / "agent-github.sh"), "gh", "api",
             f"repos/{repository}", "--jq", ".permissions"],
            timeout=15,
        )
        permissions = None
        if github["ok"]:
            try:
                permissions = json.loads(github["summary"])
            except json.JSONDecodeError:
                github["ok"] = False
        report["github"] = {
            "verified": github["ok"], "repository": repository, "permissions": permissions
        }
    else:
        github = probe(["bash", str(SCRIPTS / "agent-github.sh"), "status"], timeout=15)
        report["github"] = {"verified": github["ok"], "repository": None, "permissions": None}

    if args.workspace:
        workspace = args.workspace.expanduser().resolve()
        report["project_contract"] = json_probe(
            [sys.executable, str(SCRIPTS / "lab_project_contract.py"), "show", str(workspace)]
        )

    docker_storage_ready = (
        report["docker_storage"].get("ok") is True
        and report["docker_storage"].get("data", {}).get("healthy") is True
    )
    report["ready"] = (
        report["runtime"]["state"] == "SAFE"
        and report["rdc"]["ok"]
        and report["docker"]["ok"]
        and docker_storage_ready
        and report["toolchain"]["ok"]
        and all(report["tools"].values())
        and report["disk"]["free_bytes"] >= disk_min
        and report["inodes"].get("free_percent", 0) >= inode_min
    )

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print(f"readiness error: {error}", file=sys.stderr)
        raise SystemExit(2)

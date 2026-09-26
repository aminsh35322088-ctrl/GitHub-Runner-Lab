#!/usr/bin/env python3
"""Validate an optional target-repository Agent Lab contract."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from lab_common import runtime

DEFAULT_RELATIVE = ".github/agent-lab/contract.json"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.+:-]+$")


def contract_path(workspace: Path) -> Path:
    rel = os.getenv("AGENT_PROJECT_CONTRACT", DEFAULT_RELATIVE)
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("AGENT_PROJECT_CONTRACT must be a safe workspace-relative path")
    return workspace / path


def load(workspace: Path) -> tuple[Path, dict]:
    path = contract_path(workspace)
    if not path.is_file():
        return path, {
            "schema_version": 1,
            "bootstrap": False,
            "required_commands": [],
            "required_pkg_config_modules": [],
            "minimum_lifecycle_seconds": 0,
            "minimum_free_disk_gb": 0,
        }
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unsupported project contract schema")
    allowed = {
        "schema_version", "bootstrap", "required_commands",
        "required_pkg_config_modules", "minimum_lifecycle_seconds",
        "minimum_free_disk_gb",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown project contract keys: {sorted(unknown)}")
    if not isinstance(data.get("bootstrap", False), bool):
        raise ValueError("bootstrap must be boolean")
    for key in ("required_commands", "required_pkg_config_modules"):
        values = data.get(key, [])
        if not isinstance(values, list) or any(not isinstance(x, str) or not SAFE_NAME.fullmatch(x) for x in values):
            raise ValueError(f"{key} must contain safe command/module names")
    for key in ("minimum_lifecycle_seconds", "minimum_free_disk_gb"):
        value = data.get(key, 0)
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{key} must be a non-negative number")
    return path, {
        "schema_version": 1,
        "bootstrap": data.get("bootstrap", False),
        "required_commands": data.get("required_commands", []),
        "required_pkg_config_modules": data.get("required_pkg_config_modules", []),
        "minimum_lifecycle_seconds": int(data.get("minimum_lifecycle_seconds", 0)),
        "minimum_free_disk_gb": float(data.get("minimum_free_disk_gb", 0)),
    }


def status(workspace: Path) -> tuple[dict, bool]:
    path, data = load(workspace)
    missing_commands = [name for name in data["required_commands"] if shutil.which(name) is None]
    missing_modules = []
    for module in data["required_pkg_config_modules"]:
        try:
            result = subprocess.run(
                ["pkg-config", "--exists", module],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
            if result.returncode != 0:
                missing_modules.append(module)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            missing_modules.append(module)

    life = runtime()
    required_life = data["minimum_lifecycle_seconds"]
    lifecycle_ok = life["state"] == "UNKNOWN" or (
        life["state"] == "SAFE" and life.get("remaining", 0) >= required_life
    )
    free = shutil.disk_usage(workspace).free
    required_free = int(data["minimum_free_disk_gb"] * 1024**3)
    disk_ok = free >= required_free

    report = {
        "path": str(path),
        "present": path.is_file(),
        "bootstrap": data["bootstrap"],
        "required_commands": data["required_commands"],
        "missing_commands": missing_commands,
        "required_pkg_config_modules": data["required_pkg_config_modules"],
        "missing_pkg_config_modules": missing_modules,
        "minimum_lifecycle_seconds": required_life,
        "lifecycle": life,
        "minimum_free_disk_gb": data["minimum_free_disk_gb"],
        "free_disk_bytes": free,
        "ready": not missing_commands and not missing_modules and lifecycle_ok and disk_ok,
    }
    return report, report["ready"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("show", "check", "bootstrap-required"))
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("workspace does not exist")
    path, data = load(workspace)
    if args.action == "bootstrap-required":
        print("true" if data["bootstrap"] else "false")
        return 0
    report, ready = status(workspace)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if args.action == "show" or ready else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"project contract error: {error}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Validate the disposable runner host with Goss and emit durable reports."""
from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

from lab_toolset import DEFAULT_MANIFEST, expand, load_manifest

ROOT = Path(__file__).resolve().parents[1]


def _command(exec_text, *, timeout=10000):
    return {"exec": exec_text, "exit-status": 0, "timeout": timeout}


def build_specs(toolset, *, home: Path, cache: Path, require_rdc: bool):
    """Build hard-gate and advisory Goss specs from the canonical manifest."""
    home = Path(home).resolve()
    cache = Path(cache).resolve()
    thresholds = toolset.get("thresholds", {})
    min_disk = int(thresholds.get("disk_free_gb", 10))
    min_inode = int(thresholds.get("inode_free_percent", 5))

    hard = {"command": {}, "dns": {}, "http": {}}
    advisory = {"http": {}}

    disk_code = (
        "import shutil,sys;"
        f"sys.exit(0 if shutil.disk_usage({str(home)!r}).free >= {min_disk}*1024**3 else 1)"
    )
    inode_code = (
        "import os,sys;"
        f"s=os.statvfs({str(home)!r});"
        "p=(100*s.f_favail/s.f_files) if s.f_files else 0;"
        f"sys.exit(0 if p >= {min_inode} else 1)"
    )
    hard["command"]["disk_free_gb"] = _command(f"python3 -c {shlex.quote(disk_code)}")
    hard["command"]["inode_free_percent"] = _command(f"python3 -c {shlex.quote(inode_code)}")
    for label, path in (("home_writable", home), ("tmp_writable", Path("/tmp")), ("cache_writable", cache)):
        hard["command"][label] = _command(f"test -d {str(path)!r} -a -w {str(path)!r}")

    for name in expand(toolset, "commands", "full"):
        hard["command"][f"tool_{name}"] = _command(f"command -v {name} >/dev/null")

    expected_node = toolset.get("node", {}).get("expected")
    if expected_node:
        hard["command"]["node_version"] = _command(
            f'test "$(node --version)" = "v{expected_node}"'
        )

    if require_rdc:
        hard["command"]["rdc_health"] = _command(f"{ROOT / 'scripts' / 'health.sh'}", timeout=15000)

    for item in toolset.get("network", {}).get("critical", []):
        if item.get("kind") == "dns":
            hard["dns"][item["host"]] = {
                "resolvable": True,
                "timeout": 2000,
            }
        elif item.get("kind") == "http":
            hard["http"][item["url"]] = {"status": 200, "timeout": 5000}

    for item in toolset.get("network", {}).get("advisory", []):
        if item.get("kind") == "http":
            advisory["http"][item["url"]] = {"status": 200, "timeout": 5000}

    hard = {k: v for k, v in hard.items() if v}
    advisory = {k: v for k, v in advisory.items() if v}
    return hard, advisory


def _run_goss(goss_bin: Path, spec: dict, output: Path, name: str):
    spec_path = output / f"{name}.goss.json"
    log_path = output / f"{name}.goss.log"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n")
    started = time.monotonic()
    result = subprocess.run(
        [str(goss_bin), "-g", str(spec_path), "validate", "--format", "json", "--no-color"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    combined = result.stdout
    if result.stderr:
        combined += ("\n" if combined else "") + result.stderr
    log_path.write_text(combined)
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "log": log_path.name,
        "spec": spec_path.name,
    }


def _write_reports(output: Path, summary: dict):
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    lines = [
        "# Runner validation",
        "",
        f"**Classification:** {summary['classification']}",
        f"**Profile:** {summary['profile']}",
        f"**Toolchain:** {summary['toolchain_version']}",
        "",
        "| Stage | Status | Exit | Duration |",
        "| --- | --- | ---: | ---: |",
    ]
    for name, stage in summary["stages"].items():
        lines.append(
            f"| {name} | {stage['status']} | {stage.get('exit_code', 0)} | "
            f"{stage.get('duration_seconds', 0)}s |"
        )
    lines.append("")
    (output / "summary.md").write_text("\n".join(lines))

    suite = ET.Element(
        "testsuite",
        name="runner-validation",
        tests=str(len(summary["stages"])),
        failures=str(sum(1 for s in summary["stages"].values() if s["status"] == "failed")),
        skipped=str(sum(1 for s in summary["stages"].values() if s["status"] == "degraded")),
    )
    for name, stage in summary["stages"].items():
        case = ET.SubElement(
            suite, "testcase", name=name, time=str(stage.get("duration_seconds", 0))
        )
        if stage["status"] == "failed":
            ET.SubElement(case, "failure", message=f"{name} failed").text = stage.get("log", "")
        elif stage["status"] == "degraded":
            ET.SubElement(case, "skipped", message="advisory degradation")
    ET.ElementTree(suite).write(output / "junit.xml", encoding="utf-8", xml_declaration=True)


def _run_smoke(smoke_script: Path, profile: str, output: Path):
    smoke_output = output / "smoke"
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(smoke_script), profile, "--output-dir", str(smoke_output)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    summary_path = smoke_output / "summary.json"
    details = {}
    if summary_path.is_file():
        details = json.loads(summary_path.read_text()).get("stages", {})
    log_path = output / "smoke.log"
    combined = result.stdout
    if result.stderr:
        combined += ("\n" if combined else "") + result.stderr
    log_path.write_text(combined)
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "log": log_path.name,
        "details": details,
    }


def _resolve_goss(explicit: Path | None):
    if explicit:
        path = explicit.expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"Goss binary is not executable: {path}")
        return path
    result = subprocess.run(
        [str(ROOT / "scripts" / "install-goss.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "failed to install Goss")
    path = Path(result.stdout.strip().splitlines()[-1])
    if not path.is_file():
        raise RuntimeError(f"Goss installer returned missing path: {path}")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("quick", "full"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--goss-bin", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--smoke-script", type=Path, default=ROOT / "scripts" / "lab_smoke.py")
    parser.add_argument("--require-rdc", action="store_true")
    args = parser.parse_args()

    toolset = load_manifest(args.manifest)
    cache = Path(os.getenv("AGENT_KIT_CACHE_DIR", Path.home() / ".cache" / "agent-runner-kit"))
    cache.mkdir(parents=True, exist_ok=True)
    output = (args.output_dir or cache / "runner-validations" / f"{int(time.time())}-{os.getpid()}").resolve()
    output.mkdir(parents=True, exist_ok=True)

    require_rdc = args.require_rdc or Path(os.getenv("RDC_PID_FILE", "/tmp/rdc.pid")).exists()
    hard, advisory = build_specs(
        toolset, home=Path.home(), cache=cache, require_rdc=require_rdc
    )
    goss_bin = _resolve_goss(args.goss_bin)

    stages = {}
    host = _run_goss(goss_bin, hard, output, "host")
    stages["host"] = host
    classification = "passed"

    if host["status"] != "passed":
        classification = "failed"
        stages["network_advisory"] = {
            "status": "skipped", "exit_code": 0, "duration_seconds": 0
        }
    else:
        advisory_result = _run_goss(goss_bin, advisory, output, "network-advisory")
        if advisory_result["status"] == "failed":
            advisory_result["status"] = "degraded"
            classification = "degraded"
        stages["network_advisory"] = advisory_result

    if classification != "failed" and not args.skip_smoke:
        smoke = _run_smoke(args.smoke_script, args.profile, output)
        stages["smoke"] = smoke
        if smoke["status"] != "passed":
            classification = "failed"
    elif classification == "failed" and not args.skip_smoke:
        stages["smoke"] = {"status": "skipped", "exit_code": 0, "duration_seconds": 0}

    summary = {
        "classification": classification,
        "profile": args.profile,
        "toolchain_version": toolset["toolchain_version"],
        "require_rdc": require_rdc,
        "stages": stages,
    }
    _write_reports(output, summary)
    print((output / "summary.md").read_text(), end="")
    return 1 if classification == "failed" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"runner validation error: {error}", file=sys.stderr)
        raise SystemExit(2)

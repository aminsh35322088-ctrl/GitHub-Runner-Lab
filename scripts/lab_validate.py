#!/usr/bin/env python3
"""Project-owned full validation with durable summaries and flaky diagnostics."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def run_stage(hook, workspace, name, output, args=()):
    started = time.monotonic()
    log_path = output / f"{name}.log"
    with log_path.open("w") as log:
        result = subprocess.run(
            ["bash", str(hook), name, *args],
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=workspace,
            env=os.environ.copy(),
        )
    return {
        "name": name,
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 2),
        "log": log_path.name,
    }


def write_summary(output, summary):
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Agent Lab validation",
        "",
        f"**Result:** {summary['classification']}",
        "",
        "| Stage | Result | Duration |",
        "| --- | --- | ---: |",
    ]
    for stage in summary["stages"]:
        result = "PASS" if stage["exit_code"] == 0 else "FAIL"
        lines.append(f"| {stage['name']} | {result} | {stage['duration_seconds']}s |")
    if summary.get("failed_tests"):
        lines.extend(["", "Failed selectors:", *[f"- `{x}`" for x in summary["failed_tests"]]])
    lines.extend(["", "A flaky classification remains a failed validation; retries are diagnostic only.", ""])
    (output / "summary.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    hook = workspace / os.getenv("AGENT_PROJECT_RUNNER", ".github/agent-lab/runner.sh")
    if not hook.is_file():
        raise ValueError(f"Target branch does not provide {hook.relative_to(workspace)}")
    run_key = f"{workspace.name}-{os.getpid()}-{time.time_ns()}"
    default_output = Path.home() / ".cache" / "agent-runner-kit" / "validations" / run_key
    output = (args.output_dir or Path(os.getenv("AGENT_JOB_OUTPUT_DIR", default_output))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_JOB_OUTPUT_DIR"] = str(output)

    stages = []
    classification = "passed"
    failed_tests = []
    try:
        for name in ("prepare", "check", "full"):
            if name == "full":
                (output / "failed-tests.txt").unlink(missing_ok=True)
            stage = run_stage(hook, workspace, name, output)
            stages.append(stage)
            if stage["exit_code"] != 0:
                classification = "failed"
                if name == "full":
                    failed_file = output / "failed-tests.txt"
                    if failed_file.exists():
                        failed_tests = [x.strip() for x in failed_file.read_text().splitlines() if x.strip()]
                    if failed_tests:
                        retry = run_stage(hook, workspace, "test", output, failed_tests)
                        stages.append({**retry, "name": "diagnostic-retry"})
                        if retry["exit_code"] == 0:
                            classification = "flaky"
                break
    finally:
        cleanup = run_stage(hook, workspace, "clean-materialized", output)
        if cleanup["exit_code"] != 0 and classification == "passed":
            classification = "cleanup-failed"
        stages.append(cleanup)

    summary = {
        "classification": classification,
        "workspace": str(workspace),
        "sha": subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        ).stdout.strip(),
        "stages": [stage for stage in stages if stage["name"] != "clean-materialized"],
        "cleanup": cleanup,
        "failed_tests": failed_tests,
    }
    write_summary(output, summary)
    print((output / "summary.md").read_text(), end="")
    return 0 if classification == "passed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Validation error: {error}", file=sys.stderr)
        raise SystemExit(2)

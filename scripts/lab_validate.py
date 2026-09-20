#!/usr/bin/env python3
"""Project-owned full validation with bounded logs and interruption-safe cleanup."""
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

_ACTIVE_PROCESS = None
_RECEIVED_SIGNAL = None
_CLEANUP_RUNNING = False

def descendants(root_pid):
    """Return Linux /proc descendants deepest-first without external tools."""
    children = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = int(path.parent.name)
            fields = path.read_text().rsplit(")", 1)[1].split()
            parent = int(fields[1])
            children.setdefault(parent, []).append(pid)
        except (OSError, ValueError, IndexError):
            pass
    ordered = []
    stack = list(children.get(root_pid, ()))
    while stack:
        pid = stack.pop()
        ordered.append(pid)
        stack.extend(children.get(pid, ()))
    return reversed(ordered)


def signal_tree(process, signum):
    for pid in descendants(process.pid):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            pass
    try:
        process.send_signal(signum)
    except ProcessLookupError:
        pass


def handle_signal(signum, _frame):
    global _RECEIVED_SIGNAL
    if _RECEIVED_SIGNAL is None:
        _RECEIVED_SIGNAL = signum
    process = _ACTIVE_PROCESS
    if not _CLEANUP_RUNNING and process is not None and process.poll() is None:
        signal_tree(process, signum)

def run_stage(hook, workspace, name, output, args=(), max_log_bytes=10 * 1024 * 1024):
    global _ACTIVE_PROCESS
    started = time.monotonic()
    log_path = output / f"{name}.log"
    env = os.environ.copy()
    env["AGENT_VALIDATION_STAGE"] = name
    written = 0
    truncated = False
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            ["bash", str(hook), name, *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=workspace, env=env,
        )
        _ACTIVE_PROCESS = process
        poller = selectors.DefaultSelector()
        poller.register(process.stdout, selectors.EVENT_READ)
        try:
            while process.poll() is None or poller.get_map():
                for key, _ in poller.select(0.2):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        poller.unregister(key.fileobj)
                        continue
                    room = max(0, max_log_bytes - written)
                    if room:
                        log.write(chunk[:room])
                        written += min(room, len(chunk))
                    if len(chunk) > room:
                        truncated = True
            exit_code = process.wait()
        finally:
            _ACTIVE_PROCESS = None
            poller.close()
    return {
        "name": name, "category": "CLEANUP" if name == "clean-materialized" else "PROJECT",
        "exit_code": exit_code,
        "duration_seconds": round(time.monotonic() - started, 2),
        "log": log_path.name, "log_bytes": written, "log_truncated": truncated,
    }

def write_summary(output, summary):
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Agent Lab validation", "", f"**Result:** {summary['classification']}", "",
        "| Stage | Result | Duration | Log |", "| --- | --- | ---: | --- |",
    ]
    for stage in [*summary["stages"], summary["cleanup"]]:
        result = "PASS" if stage["exit_code"] == 0 else "FAIL"
        log_note = f"`{stage['log']}`"
        if stage.get("log_truncated"):
            log_note += " (truncated)"
        lines.append(f"| {stage['name']} | {result} | {stage['duration_seconds']}s | {log_note} |")
    if summary.get("interrupted_by"):
        lines.extend(["", f"Interrupted by `{summary['interrupted_by']}`; cleanup was still attempted."])
    if summary.get("failed_tests"):
        lines.extend(["", "Failed selectors:", *[f"- `{x}`" for x in summary["failed_tests"]]])
    lines.extend(["", "A flaky classification remains a failed validation; retries are diagnostic only.", ""])
    (output / "summary.md").write_text("\n".join(lines))

def main():
    global _CLEANUP_RUNNING
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--log-max-mb", type=float,
                        default=float(os.getenv("AGENT_VALIDATION_LOG_MAX_MB", "10")),
                        help="Maximum retained size per stage log in MiB (default: 10).")
    args = parser.parse_args()
    if args.log_max_mb <= 0:
        raise ValueError("--log-max-mb must be positive")
    max_log_bytes = max(1, int(args.log_max_mb * 1024 * 1024))
    workspace = args.workspace.resolve()
    hook = workspace / os.getenv("AGENT_PROJECT_RUNNER", ".github/agent-lab/runner.sh")
    if not hook.is_file():
        raise ValueError(f"Target branch does not provide {hook.relative_to(workspace)}")
    run_key = f"{workspace.name}-{os.getpid()}-{time.time_ns()}"
    default_output = Path.home() / ".cache" / "agent-runner-kit" / "validations" / run_key
    output = (args.output_dir or Path(os.getenv("AGENT_JOB_OUTPUT_DIR", default_output))).resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["AGENT_JOB_OUTPUT_DIR"] = str(output)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, handle_signal)

    stages = []
    classification = "passed"
    failed_tests = []
    try:
        for name in ("prepare", "check", "full"):
            if name == "full":
                (output / "failed-tests.txt").unlink(missing_ok=True)
            stage = run_stage(hook, workspace, name, output, max_log_bytes=max_log_bytes)
            stages.append(stage)
            if _RECEIVED_SIGNAL is not None:
                classification = "interrupted"
                break
            if stage["exit_code"] != 0:
                classification = "failed"
                if name == "full":
                    failed_file = output / "failed-tests.txt"
                    if failed_file.exists():
                        failed_tests = [x.strip() for x in failed_file.read_text().splitlines() if x.strip()]
                    if failed_tests:
                        retry = run_stage(hook, workspace, "test", output, failed_tests,
                                          max_log_bytes=max_log_bytes)
                        stages.append({**retry, "name": "diagnostic-retry"})
                        if retry["exit_code"] == 0:
                            classification = "flaky"
                break
    finally:
        _CLEANUP_RUNNING = True
        cleanup = run_stage(hook, workspace, "clean-materialized", output,
                            max_log_bytes=max_log_bytes)
        _CLEANUP_RUNNING = False
        if cleanup["exit_code"] != 0 and classification == "passed":
            classification = "cleanup-failed"

    interrupted_by = None
    if _RECEIVED_SIGNAL is not None:
        try:
            interrupted_by = signal.Signals(_RECEIVED_SIGNAL).name
        except ValueError:
            interrupted_by = str(_RECEIVED_SIGNAL)
    summary = {
        "classification": classification, "workspace": str(workspace),
        "sha": subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False).stdout.strip(),
        "stages": stages, "cleanup": cleanup, "failed_tests": failed_tests,
        "interrupted_by": interrupted_by, "log_max_bytes": max_log_bytes,
    }
    write_summary(output, summary)
    print((output / "summary.md").read_text(), end="")
    if _RECEIVED_SIGNAL is not None:
        return 128 + _RECEIVED_SIGNAL
    return 0 if classification == "passed" else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Validation error: {error}", file=sys.stderr)
        raise SystemExit(2)

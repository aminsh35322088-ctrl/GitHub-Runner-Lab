#!/usr/bin/env python3
"""Bounded functional smoke tests for the disposable engineering runner."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

QUICK_STAGES = ["native", "cmake_ninja", "node", "python", "git"]
FULL_STAGES = QUICK_STAGES + ["docker", "media"]


def stage_names(profile: str):
    if profile == "quick":
        return list(QUICK_STAGES)
    if profile == "full":
        return list(FULL_STAGES)
    raise ValueError(f"unknown smoke profile: {profile}")


def stage_category(name: str):
    if name in ("native", "cmake_ninja", "node", "python", "git"):
        return "TOOLCHAIN"
    if name == "docker":
        return "DOCKER"
    if name == "media":
        return "MEDIA"
    raise ValueError(f"unknown smoke stage: {name}")


def _run(argv, *, cwd: Path, log: list[str], timeout=30, env=None, expected=None):
    display = " ".join(str(x) for x in argv)
    log.append(f"$ {display}")
    try:
        result = subprocess.run(
            [str(x) for x in argv],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"command not found: {argv[0]}") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"command timed out after {timeout}s: {display}") from error
    if result.stdout:
        log.append(result.stdout.rstrip())
    if result.stderr:
        log.append(result.stderr.rstrip())
    if result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {display}")
    if expected is not None and result.stdout.strip() != expected:
        raise RuntimeError(
            f"unexpected output for {display}: {result.stdout.strip()!r} != {expected!r}"
        )
    return result


def _native(work: Path, log: list[str]):
    source = work / "smoke.c"
    source.write_text(
        '#include <stdio.h>\nint main(void){puts("runner-native-smoke");return 0;}\n'
    )
    for compiler in ("gcc", "clang"):
        binary = work / f"smoke-{compiler}"
        _run([compiler, "-O0", "-Wall", "-Wextra", "-Werror", source, "-o", binary],
             cwd=work, log=log)
        _run([binary], cwd=work, log=log, expected="runner-native-smoke")


def _cmake_ninja(work: Path, log: list[str]):
    (work / "main.c").write_text(
        '#include <stdio.h>\nint main(void){puts("runner-cmake-smoke");return 0;}\n'
    )
    (work / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\n"
        "project(runner_smoke C)\n"
        "add_executable(runner_smoke main.c)\n"
    )
    build = work / "build"
    _run(["cmake", "-S", work, "-B", build, "-G", "Ninja"], cwd=work, log=log)
    _run(["cmake", "--build", build], cwd=work, log=log)
    _run([build / "runner_smoke"], cwd=work, log=log, expected="runner-cmake-smoke")


def _node(work: Path, log: list[str]):
    _run(["node", "-e", "process.stdout.write('runner-node-smoke')"],
         cwd=work, log=log, expected="runner-node-smoke")


def _python(work: Path, log: list[str]):
    venv = work / "venv"
    _run(["python3", "-m", "venv", venv], cwd=work, log=log, timeout=45)
    _run(
        [venv / "bin" / "python", "-c",
         "import ssl,sqlite3,venv;print('runner-python-smoke')"],
        cwd=work,
        log=log,
        expected="runner-python-smoke",
    )


def _git(work: Path, log: list[str]):
    repo = work / "repo"
    repo.mkdir()
    _run(["git", "init", "--initial-branch=main", repo], cwd=work, log=log)
    _run(["git", "config", "user.name", "Runner Smoke"], cwd=repo, log=log)
    _run(["git", "config", "user.email", "runner-smoke@example.invalid"], cwd=repo, log=log)
    (repo / "tracked.txt").write_text("runner git smoke\n")
    _run(["git", "add", "."], cwd=repo, log=log)
    _run(["git", "commit", "-m", "smoke"], cwd=repo, log=log)
    worktree = work / "worktree"
    _run(["git", "worktree", "add", "-b", "smoke-worktree", worktree, "HEAD"],
         cwd=repo, log=log)
    status = _run(["git", "status", "--porcelain"], cwd=worktree, log=log)
    if status.stdout.strip():
        raise RuntimeError("git worktree is unexpectedly dirty")
    _run(["git", "lfs", "version"], cwd=repo, log=log)


def _docker(work: Path, log: list[str]):
    _run(["docker", "version"], cwd=work, log=log, timeout=20)
    _run(["docker", "buildx", "version"], cwd=work, log=log, timeout=20)
    _run(["docker", "compose", "version"], cwd=work, log=log, timeout=20)

    source = work / "docker-smoke.c"
    source.write_text(
        '#include <unistd.h>\n'
        'int main(void){const char m[]="runner-docker-smoke\\n";'
        'return write(1,m,sizeof(m)-1)<0;}\n'
    )
    binary = work / "smoke"
    _run(["gcc", "-static", "-Os", source, "-o", binary], cwd=work, log=log, timeout=30)
    (work / "Dockerfile").write_text(
        'FROM scratch\nCOPY smoke /smoke\nENTRYPOINT ["/smoke"]\n'
    )
    image = f"agent-lab-smoke:{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        _run(["docker", "build", "--network", "none", "-t", image, "."],
             cwd=work, log=log, timeout=60)
        _run(
            ["docker", "run", "--rm", "--network", "none", "--read-only",
             "--cap-drop", "ALL", "--pids-limit", "32", "--memory", "64m",
             "--cpus", "0.5", image],
            cwd=work,
            log=log,
            timeout=30,
            expected="runner-docker-smoke",
        )
    finally:
        subprocess.run(
            ["docker", "image", "rm", "-f", image],
            cwd=work,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )


def _media(work: Path, log: list[str]):
    tone = work / "tone.wav"
    resampled = work / "resampled.wav"
    _run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "sine=frequency=1000:duration=0.2", "-c:a", "pcm_s16le", tone],
        cwd=work, log=log, timeout=30
    )
    probe = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", tone],
        cwd=work, log=log
    )
    if float(probe.stdout.strip()) <= 0:
        raise RuntimeError("ffprobe reported a non-positive duration")
    _run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", tone,
         "-ar", "8000", resampled],
        cwd=work, log=log, timeout=30
    )
    image = work / "image.png"
    resized = work / "resized.png"
    _run(["convert", "-size", "8x8", "xc:white", image], cwd=work, log=log)
    _run(["convert", image, "-resize", "4x4!", resized], cwd=work, log=log)
    _run(["identify", "-format", "%wx%h", resized],
         cwd=work, log=log, expected="4x4")


STAGES = {
    "native": _native,
    "cmake_ninja": _cmake_ninja,
    "node": _node,
    "python": _python,
    "git": _git,
    "docker": _docker,
    "media": _media,
}


def run_stage(name: str, output: Path):
    log_lines: list[str] = []
    log_path = output / f"{name}.log"
    started = time.monotonic()
    status = "passed"
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix=f"work-{name}-", dir=output) as temp:
            STAGES[name](Path(temp), log_lines)
    except Exception as exc:
        status = "failed"
        error = str(exc)
        log_lines.append(f"ERROR: {error}")
    log_path.write_text("\n".join(log_lines) + ("\n" if log_lines else ""))
    result = {
        "status": status,
        "category": stage_category(name),
        "duration_seconds": round(time.monotonic() - started, 3),
        "log": log_path.name,
    }
    if error:
        result["error"] = error
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=("quick", "full"))
    parser.add_argument("--only", choices=FULL_STAGES)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    names = [args.only] if args.only else stage_names(args.profile)
    stages = {}
    classification = "passed"
    for name in names:
        stages[name] = run_stage(name, output)
        if stages[name]["status"] != "passed":
            classification = "failed"
            break
    summary = {"classification": classification, "profile": args.profile, "stages": stages}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0 if classification == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

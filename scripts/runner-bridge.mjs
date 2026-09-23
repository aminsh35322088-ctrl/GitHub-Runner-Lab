#!/usr/bin/env node
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";

const BRIDGE_URL = process.env.RUNNER_BRIDGE_URL || "";
const OIDC_AUDIENCE = "opencode-telegram-runner";
const MAX_OUTPUT_BYTES = 128 * 1024;
const MAX_FILE_BYTES = 256 * 1024;
const MAX_LIST_ITEMS = 500;
const DEFAULT_EXEC_TIMEOUT_MS = 60_000;

function fail(message) {
  throw new Error(message);
}

function safeEnvironment() {
  const blocked = /(TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE|AUTH|ACTIONS_ID_TOKEN)/i;
  const out = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value === undefined || blocked.test(key)) continue;
    out[key] = value;
  }
  return out;
}

function clipBuffer(buffer) {
  if (buffer.length <= MAX_OUTPUT_BYTES) return buffer.toString("utf8");
  const tail = buffer.subarray(buffer.length - MAX_OUTPUT_BYTES).toString("utf8");
  return `…(truncated, ${buffer.length} bytes total)\n${tail}`;
}

async function runProcess(command, args, options = {}) {
  const cwd = options.cwd || process.env.GITHUB_WORKSPACE || process.cwd();
  const timeoutMs = Math.max(1000, Number(options.timeoutMs || DEFAULT_EXEC_TIMEOUT_MS));
  return await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env: options.env || safeEnvironment(),
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const stdout = [];
    const stderr = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    const keep = (chunks, chunk, current) => {
      chunks.push(chunk);
      current += chunk.length;
      while (current > MAX_OUTPUT_BYTES && chunks.length > 1) {
        current -= chunks.shift().length;
      }
      return current;
    };

    child.stdout.on("data", (chunk) => {
      stdoutBytes = keep(stdout, Buffer.from(chunk), stdoutBytes);
    });
    child.stderr.on("data", (chunk) => {
      stderrBytes = keep(stderr, Buffer.from(chunk), stderrBytes);
    });

    const timer = setTimeout(() => {
      try {
        process.kill(-child.pid, "SIGTERM");
      } catch {}
      setTimeout(() => {
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {}
      }, 5000).unref();
    }, timeoutMs);

    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      resolve({
        code: code ?? null,
        signal: signal ?? null,
        stdout: clipBuffer(Buffer.concat(stdout)),
        stderr: clipBuffer(Buffer.concat(stderr)),
      });
    });
  });
}

function requireString(value, name) {
  if (typeof value !== "string" || !value.trim()) fail(`${name} is required`);
  return value.trim();
}

function optionalString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function allowedRoots() {
  return [
    path.resolve(os.homedir()),
    path.resolve(process.env.GITHUB_WORKSPACE || process.cwd()),
    path.resolve(process.env.RUNNER_TEMP || os.tmpdir()),
    path.resolve("/tmp"),
  ];
}

function withinRoot(candidate, root) {
  return candidate === root || candidate.startsWith(root + path.sep);
}

async function resolveAllowedPath(input, cwd, { forWrite = false } = {}) {
  const raw = requireString(input, "path");
  const base = path.resolve(cwd || process.env.GITHUB_WORKSPACE || process.cwd());
  const absolute = path.resolve(base, raw);
  const roots = allowedRoots();

  if (!roots.some((root) => withinRoot(absolute, root))) {
    fail("path is outside Runner Bridge allowed roots");
  }

  if (!forWrite) {
    const real = await fs.realpath(absolute);
    if (!roots.some((root) => withinRoot(real, root))) {
      fail("resolved path escapes Runner Bridge allowed roots");
    }
    return real;
  }

  let parent = path.dirname(absolute);
  for (;;) {
    try {
      const realParent = await fs.realpath(parent);
      if (!roots.some((root) => withinRoot(realParent, root))) {
        fail("write path escapes Runner Bridge allowed roots");
      }
      break;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
      const next = path.dirname(parent);
      if (next === parent) throw error;
      parent = next;
    }
  }
  return absolute;
}

function repoRoot() {
  return process.env.GITHUB_WORKSPACE || process.cwd();
}

async function agentRun(args, options = {}) {
  const script = path.join(repoRoot(), "scripts", "agent-run.sh");
  return runProcess(script, args, {
    cwd: options.cwd || repoRoot(),
    timeoutMs: options.timeoutMs,
  });
}

async function actionStatus(args, timeoutMs) {
  const result = await agentRun(["status"], { timeoutMs });
  return { ...result, runId: process.env.GITHUB_RUN_ID || null };
}

async function actionExec(args, timeoutMs) {
  const command = requireString(args.command, "command");
  const cwd = optionalString(args.cwd) || repoRoot();
  const resolvedCwd = await resolveAllowedPath(cwd, repoRoot());
  return runProcess("/bin/bash", ["-lc", command], { cwd: resolvedCwd, timeoutMs });
}

async function actionJobStart(args, timeoutMs) {
  const command = requireString(args.command, "command");
  const cwd = await resolveAllowedPath(optionalString(args.cwd) || repoRoot(), repoRoot());
  const seconds = Math.max(1, Math.min(21_000, Math.trunc((Number(args.timeoutMs) || timeoutMs) / 1000)));
  const result = await agentRun(
    ["job", "start", "--cwd", cwd, "--timeout", String(seconds), "--", "/bin/bash", "-lc", command],
    { timeoutMs: Math.min(timeoutMs, 30_000) },
  );
  if (result.code !== 0) fail(result.stderr || result.stdout || "failed to start runner job");
  const jobId = result.stdout.trim().split(/\s+/).at(-1);
  return { jobId, stdout: result.stdout, stderr: result.stderr };
}

async function actionJobCommand(action, args, timeoutMs) {
  const jobId = requireString(args.jobId, "jobId");
  const verb = action.split(".")[1];
  return agentRun(["job", verb, jobId], { timeoutMs });
}

async function actionFileRead(args) {
  const cwd = optionalString(args.cwd) || repoRoot();
  const target = await resolveAllowedPath(args.path, cwd);
  const stat = await fs.stat(target);
  if (!stat.isFile()) fail("path is not a file");
  if (stat.size > MAX_FILE_BYTES) fail(`file exceeds ${MAX_FILE_BYTES} byte read limit`);
  return { path: target, content: await fs.readFile(target, "utf8"), bytes: stat.size };
}

async function actionFileWrite(args) {
  const cwd = optionalString(args.cwd) || repoRoot();
  const target = await resolveAllowedPath(args.path, cwd, { forWrite: true });
  const content = typeof args.content === "string" ? args.content : fail("content is required");
  if (Buffer.byteLength(content) > MAX_FILE_BYTES) fail(`content exceeds ${MAX_FILE_BYTES} byte write limit`);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await fs.writeFile(target, content, { encoding: "utf8", mode: 0o600 });
  return { path: target, bytes: Buffer.byteLength(content) };
}

async function actionFileList(args) {
  const cwd = optionalString(args.cwd) || repoRoot();
  const target = await resolveAllowedPath(optionalString(args.path) || cwd, cwd);
  const limit = Math.max(1, Math.min(MAX_LIST_ITEMS, Number(args.limit) || 200));
  const entries = await fs.readdir(target, { withFileTypes: true });
  return {
    path: target,
    entries: entries.slice(0, limit).map((entry) => ({
      name: entry.name,
      type: entry.isDirectory() ? "directory" : entry.isFile() ? "file" : "other",
    })),
    truncated: entries.length > limit,
  };
}

async function actionFileSearch(args, timeoutMs) {
  const query = requireString(args.query, "query");
  const cwd = optionalString(args.cwd) || repoRoot();
  const target = await resolveAllowedPath(optionalString(args.path) || cwd, cwd);
  const argv = ["--line-number", "--no-heading", "--color", "never", "--max-count", "200"];
  const glob = optionalString(args.glob);
  if (glob) argv.push("--glob", glob);
  argv.push("--", query, target);
  const result = await runProcess("rg", argv, { cwd: target, timeoutMs });
  if (result.code !== 0 && result.code !== 1) fail(result.stderr || "rg failed");
  return result;
}

async function actionWorkspacePrepare(args, timeoutMs) {
  const argv = ["prepare"];
  const repo = optionalString(args.repo);
  const ref = optionalString(args.ref);
  const pr = Number(args.pr);
  const dir = optionalString(args.cwd);
  if (repo) {
    if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:\.git)?$/u.test(repo)) {
      fail("repo must be an https://github.com/OWNER/REPO URL");
    }
    argv.push("--repo", repo);
  }
  if (ref) argv.push("--ref", ref);
  if (Number.isSafeInteger(pr) && pr > 0) argv.push("--pr", String(pr));
  if (dir) argv.push("--dir", await resolveAllowedPath(dir, repoRoot(), { forWrite: true }));
  return agentRun(argv, { timeoutMs });
}

async function actionValidate(args, timeoutMs) {
  const mode = optionalString(args.mode) || "project";
  if (mode === "runner-quick" || mode === "runner-full") {
    return agentRun(["validate", "runner", mode === "runner-full" ? "full" : "quick"], { timeoutMs });
  }
  const cwd = await resolveAllowedPath(optionalString(args.cwd) || repoRoot(), repoRoot());
  return agentRun(["validate", "project", cwd], { timeoutMs });
}

async function handleAction(action, args, timeoutMs) {
  switch (action) {
    case "status": return actionStatus(args, timeoutMs);
    case "exec": return actionExec(args, timeoutMs);
    case "job.start": return actionJobStart(args, timeoutMs);
    case "job.status":
    case "job.logs":
    case "job.wait":
    case "job.stop":
      return actionJobCommand(action, args, timeoutMs);
    case "file.read": return actionFileRead(args);
    case "file.write": return actionFileWrite(args);
    case "file.list": return actionFileList(args);
    case "file.search": return actionFileSearch(args, timeoutMs);
    case "workspace.prepare": return actionWorkspacePrepare(args, timeoutMs);
    case "validate": return actionValidate(args, timeoutMs);
    default: fail(`unsupported action: ${action}`);
  }
}

async function requestOidcToken() {
  const requestUrl = requireString(process.env.ACTIONS_ID_TOKEN_REQUEST_URL, "ACTIONS_ID_TOKEN_REQUEST_URL");
  const requestToken = requireString(process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN, "ACTIONS_ID_TOKEN_REQUEST_TOKEN");
  const url = new URL(requestUrl);
  url.searchParams.set("audience", OIDC_AUDIENCE);
  const response = await fetch(url, {
    headers: { authorization: `bearer ${requestToken}`, accept: "application/json" },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) fail(`GitHub OIDC token request failed with HTTP ${response.status}`);
  const payload = await response.json();
  return requireString(payload.value, "OIDC token");
}

async function selfTest() {
  if (typeof WebSocket !== "function") fail("Node WebSocket client is unavailable");
  const env = safeEnvironment();
  if (Object.keys(env).some((key) => /(TOKEN|SECRET|PASSWORD|ACTIONS_ID_TOKEN)/i.test(key))) {
    fail("safe environment retained a secret-like variable");
  }
  const result = await runProcess("/bin/bash", ["-lc", "printf runner-bridge-self-test"], {
    cwd: repoRoot(),
    timeoutMs: 5000,
  });
  if (result.code !== 0 || result.stdout !== "runner-bridge-self-test") {
    fail("process execution self-test failed");
  }
  console.log("runner-bridge self-test: PASS");
}

async function connectLoop() {
  if (!BRIDGE_URL.startsWith("wss://")) fail("RUNNER_BRIDGE_URL must use wss://");
  let attempt = 0;

  for (;;) {
    try {
      const token = await requestOidcToken();
      await new Promise((resolve, reject) => {
        const ws = new WebSocket(BRIDGE_URL);
        let authenticated = false;
        let closed = false;

        ws.addEventListener("open", () => {
          ws.send(JSON.stringify({ type: "auth", token }));
        });

        ws.addEventListener("message", (event) => {
          let message;
          try {
            message = JSON.parse(String(event.data));
          } catch {
            return;
          }

          if (message.type === "auth.ok") {
            authenticated = true;
            attempt = 0;
            console.log(`[runner-bridge] connected run=${message.runId || process.env.GITHUB_RUN_ID || "unknown"}`);
            return;
          }

          if (message.type !== "request" || typeof message.requestId !== "string") return;
          const timeoutMs = Math.max(1000, Math.min(15 * 60_000, Number(message.timeoutMs) || DEFAULT_EXEC_TIMEOUT_MS));
          Promise.resolve()
            .then(() => handleAction(String(message.action || ""), message.args || {}, timeoutMs))
            .then((result) => {
              if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: "response", requestId: message.requestId, ok: true, result }));
              }
            })
            .catch((error) => {
              if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                  type: "response",
                  requestId: message.requestId,
                  ok: false,
                  error: error instanceof Error ? error.message : String(error),
                }));
              }
            });
        });

        ws.addEventListener("error", () => {
          if (!authenticated && !closed) reject(new Error("runner bridge websocket connection failed"));
        });

        ws.addEventListener("close", (event) => {
          closed = true;
          console.warn(`[runner-bridge] disconnected code=${event.code} reason=${event.reason || "none"}`);
          resolve();
        });

        const authTimer = setTimeout(() => {
          if (!authenticated && ws.readyState === WebSocket.OPEN) ws.close(4003, "auth timeout");
        }, 15_000);
        authTimer.unref();
      });
    } catch (error) {
      console.warn("[runner-bridge] connection error:", error instanceof Error ? error.message : String(error));
    }

    attempt += 1;
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5));
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
}

if (process.argv.includes("--self-test")) {
  selfTest().catch((error) => {
    console.error(error);
    process.exit(1);
  });
} else {
  connectLoop().catch((error) => {
    console.error(error);
    process.exit(1);
  });
}

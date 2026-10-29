import assert from "node:assert/strict";
import test from "node:test";
import worker from "./index.js";

const AUTH_TOKEN = "test-broker-token-with-enough-entropy";
const BASE_URL = "https://vault.example.test";

function makeEnv(overrides = {}) {
  return {
    BROKER_ACCESS_TOKEN: AUTH_TOKEN,
    CRED_GITHUB_TOKEN: "ghp-test-github-token",
    CRED_TAILSCALE_KEY: "tskey-test-tailscale-key",
    CRED_EMPTY: "",
    CLOUDFLARE_API_TOKEN: "must-never-be-exposed",
    OTHER_SETTING: "must-not-be-exposed",
    ...overrides,
  };
}

function authorized(path, { method = "GET", token = AUTH_TOKEN } = {}) {
  return new Request(new URL(path, BASE_URL), {
    method,
    headers: token === null ? {} : { Authorization: `Bearer ${token}` },
  });
}

async function json(response) {
  assert.match(response.headers.get("content-type") ?? "", /application\/json/i);
  return response.json();
}

test("health is public, minimal, and does not expose binding names or values", async () => {
  const response = await worker.fetch(new Request(new URL("/health", BASE_URL)), makeEnv());
  assert.equal(response.status, 200);
  assert.match(response.headers.get("cache-control") ?? "", /no-store/i);
  assert.deepEqual(await json(response), { ok: true, service: "runner-credential-vault" });
});

test("credential routes require bearer authentication", async () => {
  const response = await worker.fetch(authorized("/v1/credentials", { token: null }), makeEnv());
  assert.equal(response.status, 401);
  assert.deepEqual(await json(response), { error: "unauthorized" });
});

test("credential routes reject an incorrect bearer token", async () => {
  const response = await worker.fetch(authorized("/v1/credentials", { token: "wrong-token" }), makeEnv());
  assert.equal(response.status, 401);
  assert.deepEqual(await json(response), { error: "unauthorized" });
});

test("an unconfigured broker fails closed", async () => {
  const response = await worker.fetch(authorized("/v1/credentials"), { CRED_GITHUB_TOKEN: "secret" });
  assert.equal(response.status, 503);
  assert.deepEqual(await json(response), { error: "vault_unavailable" });
});

test("list returns configured CRED_ names only and never values", async () => {
  const response = await worker.fetch(authorized("/v1/credentials"), makeEnv());
  assert.equal(response.status, 200);
  const body = await json(response);
  assert.deepEqual(body, { credentials: ["GITHUB_TOKEN", "TAILSCALE_KEY"] });
  assert.doesNotMatch(JSON.stringify(body), /ghp-test|tskey-test|must-never/);
  assert.match(response.headers.get("cache-control") ?? "", /no-store/i);
});

test("get returns exactly the requested credential without exposing other bindings", async () => {
  const response = await worker.fetch(authorized("/v1/credentials/GITHUB_TOKEN"), makeEnv());
  assert.equal(response.status, 200);
  assert.deepEqual(await json(response), {
    name: "GITHUB_TOKEN",
    value: "ghp-test-github-token",
  });
  assert.match(response.headers.get("cache-control") ?? "", /no-store/i);
  assert.equal(response.headers.get("pragma"), "no-cache");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("access-control-allow-origin"), null);
});

test("unknown or empty credentials return 404 without revealing internal bindings", async () => {
  for (const name of ["UNKNOWN", "EMPTY", "BROKER_ACCESS_TOKEN", "CLOUDFLARE_API_TOKEN"]) {
    const response = await worker.fetch(authorized(`/v1/credentials/${encodeURIComponent(name)}`), makeEnv());
    assert.equal(response.status, 404, name);
    assert.deepEqual(await json(response), { error: "credential_not_found" });
  }
  const invalidName = await worker.fetch(authorized("/v1/credentials/..%2FOTHER_SETTING"), makeEnv());
  assert.equal(invalidName.status, 400);
  assert.deepEqual(await json(invalidName), { error: "invalid_credential_name" });
});

test("only uppercase credential names matching the contract are accepted", async () => {
  for (const name of ["github_token", "GITHUB-TOKEN", "GITHUB.TOKEN"]) {
    const response = await worker.fetch(authorized(`/v1/credentials/${name}`), makeEnv());
    assert.equal(response.status, 400, name);
    assert.deepEqual(await json(response), { error: "invalid_credential_name" });
  }
});

test("non-GET methods cannot mutate the vault", async () => {
  for (const path of ["/v1/credentials", "/v1/credentials/GITHUB_TOKEN"]) {
    const response = await worker.fetch(authorized(path, { method: "POST" }), makeEnv());
    assert.equal(response.status, 405);
    assert.deepEqual(await json(response), { error: "method_not_allowed" });
  }
});

test("non-string Worker bindings are never listed or returned as credentials", async () => {
  const env = makeEnv({ CRED_OBJECT: { get: () => "not a string" } });
  const list = await worker.fetch(authorized("/v1/credentials"), env);
  assert.deepEqual(await json(list), { credentials: ["GITHUB_TOKEN", "TAILSCALE_KEY"] });
  const get = await worker.fetch(authorized("/v1/credentials/OBJECT"), env);
  assert.equal(get.status, 404);
});

test("responses vary by authorization and never cache credential material", async () => {
  const response = await worker.fetch(authorized("/v1/credentials/GITHUB_TOKEN"), makeEnv());
  assert.equal(response.headers.get("vary"), "Authorization");
  assert.match(response.headers.get("cache-control") ?? "", /no-store/i);
});

const CREDENTIAL_PREFIX = "CRED_";
const CREDENTIAL_NAME_PATTERN = /^[A-Z][A-Z0-9_]{0,79}$/;
const MIN_TOKEN_LENGTH = 32;
const MAX_TOKEN_LENGTH = 256;

const JSON_HEADERS = {
  "Content-Type": "application/json; charset=utf-8",
  "Cache-Control": "no-store, private, max-age=0",
  "Pragma": "no-cache",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Frame-Options": "DENY",
  "Vary": "Authorization",
};

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: JSON_HEADERS,
  });
}

function constantTimeEqual(left, right) {
  const a = new TextEncoder().encode(left);
  const b = new TextEncoder().encode(right);
  let difference = a.length ^ b.length;

  // Iterate over the expected token's length regardless of the first
  // differing byte. Oversized inputs are rejected before this function.
  for (let i = 0; i < a.length; i += 1) {
    difference |= a[i] ^ (b[i] ?? 0);
  }

  return difference === 0;
}

function authenticate(request, env) {
  const expected = env.BROKER_ACCESS_TOKEN;
  if (typeof expected !== "string" || expected.length < MIN_TOKEN_LENGTH || expected.length > MAX_TOKEN_LENGTH) {
    return jsonResponse({ error: "vault_unavailable" }, 503);
  }

  const authorization = request.headers.get("authorization") ?? "";
  const match = /^Bearer ([\x21-\x7e]{1,256})$/.exec(authorization);
  if (!match || match[1].length < MIN_TOKEN_LENGTH || match[1].length > MAX_TOKEN_LENGTH) {
    return jsonResponse({ error: "unauthorized" }, 401);
  }

  if (!constantTimeEqual(expected, match[1])) {
    return jsonResponse({ error: "unauthorized" }, 401);
  }

  return null;
}

function listCredentialNames(env) {
  return Object.keys(env)
    .filter((key) => key.startsWith(CREDENTIAL_PREFIX))
    .filter((key) => CREDENTIAL_NAME_PATTERN.test(key.slice(CREDENTIAL_PREFIX.length)))
    .filter((key) => typeof env[key] === "string" && env[key].length > 0)
    .map((key) => key.slice(CREDENTIAL_PREFIX.length))
    .sort();
}

function getCredential(env, name) {
  if (!CREDENTIAL_NAME_PATTERN.test(name)) {
    return jsonResponse({ error: "invalid_credential_name" }, 400);
  }

  const bindingName = `${CREDENTIAL_PREFIX}${name}`;
  if (!Object.prototype.hasOwnProperty.call(env, bindingName)) {
    return jsonResponse({ error: "credential_not_found" }, 404);
  }

  const value = env[bindingName];
  if (typeof value !== "string" || value.length === 0) {
    return jsonResponse({ error: "credential_not_found" }, 404);
  }

  return jsonResponse({ name, value });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResponse({ ok: true, service: "runner-credential-vault" });
    }

    if (request.method !== "GET") {
      return jsonResponse({ error: "method_not_allowed" }, 405);
    }

    const authFailure = authenticate(request, env);
    if (authFailure) return authFailure;

    if (url.pathname === "/v1/credentials") {
      return jsonResponse({ credentials: listCredentialNames(env) });
    }

    const routeMatch = /^\/v1\/credentials\/([^/]+)$/.exec(url.pathname);
    if (!routeMatch) {
      return jsonResponse({ error: "not_found" }, 404);
    }

    let name;
    try {
      name = decodeURIComponent(routeMatch[1]);
    } catch {
      return jsonResponse({ error: "invalid_credential_name" }, 400);
    }

    return getCredential(env, name);
  },
};

#!/usr/bin/env python3
"""Discover the Vault deployment and export only strictly validated values."""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_DOCUMENT_BYTES = 64 * 1024


def validate_deployment(payload: object, owner: str) -> dict[str, str]:
    """Return normalized env values or raise ValueError without side effects."""
    if not isinstance(owner, str) or not OWNER_RE.fullmatch(owner):
        raise ValueError("repository owner is not a valid GitHub owner name")
    if not isinstance(payload, dict):
        raise ValueError("deployment document must be a JSON object")

    url = payload.get("url")
    client_ref = payload.get("clientRef")
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError("deployment URL must be a non-empty string")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValueError("deployment URL contains control characters")
    if not OWNER_RE.fullmatch(owner):
        raise ValueError("repository owner is not a valid GitHub owner name")
    try:
        parsed = urlsplit(url)
        # Access .port to force urllib's port validation.
        _ = parsed.port
    except ValueError as error:
        raise ValueError("deployment URL is malformed") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or parsed.port is not None
        or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\."
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.workers\.dev",
            parsed.hostname or "",
        )
        or url not in (f"https://{parsed.netloc}", f"https://{parsed.netloc}/")
    ):
        raise ValueError("deployment URL must be an HTTPS origin with no path, query or credentials")
    if not isinstance(client_ref, str) or not SHA_RE.fullmatch(client_ref):
        raise ValueError("clientRef must be an immutable lowercase 40-character commit SHA")

    return {
        "VAULT_URL": url.rstrip("/"),
        "VAULT_CLIENT_REPOSITORY": f"{owner}/GitHub-runner-vault",
        "VAULT_CLIENT_REF": client_ref,
    }


def load_deployment(owner: str, token: str | None = None) -> object:
    """Read deployment.json through the Contents API, including from a private Vault repo."""
    if not isinstance(owner, str) or not OWNER_RE.fullmatch(owner):
        raise ValueError("repository owner is not a valid GitHub owner name")
    if token and any(char in token for char in ("\r", "\n")):
        raise ValueError("GitHub API credential has an invalid format")
    url = (
        f"https://api.github.com/repos/{owner}/GitHub-runner-vault/"
        "contents/deployment.json?ref=main"
    )
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "Cache-Control": "no-cache",
        "User-Agent": "github-runner-lab",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10) as response:
        raw = response.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("deployment document exceeds the size limit")
    return json.loads(raw.decode("utf-8"))


def main() -> int:
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER", "")
    output_path = os.environ.get("GITHUB_OUTPUT")
    env_path = os.environ.get("GITHUB_ENV")
    if not output_path or not env_path:
        print("::error::GitHub Actions output files are unavailable.")
        return 1

    try:
        token = os.environ.pop("GITHUB_API_TOKEN", None)
        payload = load_deployment(owner, token)
        values = validate_deployment(payload, owner)
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write("configured=false\n")
        # Avoid echoing untrusted payload values into workflow logs.
        print(f"::notice::Vault deployment was not discovered or failed validation ({type(error).__name__}); Vault integration is disabled for this run.")
        return 0

    # Values are written only after all fields have passed strict validation.
    with open(env_path, "a", encoding="utf-8") as env_file:
        for key, value in values.items():
            env_file.write(f"{key}={value}\n")
    with open(output_path, "a", encoding="utf-8") as output:
        output.write("configured=true\n")
    print("Vault deployment discovered and validated; environment values exported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

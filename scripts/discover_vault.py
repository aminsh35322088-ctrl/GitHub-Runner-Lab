#!/usr/bin/env python3
"""Discover and validate a Runner Credential Vault deployment."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_DOCUMENT_BYTES = 64 * 1024
DEFAULT_VAULT_REPOSITORY = "GitHub-runner-vault"


def resolve_vault_repository(owner: str, configured: str | None = None) -> str:
    """Use an explicit full repository name or the historical same-owner default."""
    if not isinstance(owner, str) or not OWNER_RE.fullmatch(owner):
        raise ValueError("repository owner is not a valid GitHub owner name")
    repository = configured.strip() if isinstance(configured, str) else ""
    if not repository:
        repository = f"{owner}/{DEFAULT_VAULT_REPOSITORY}"
    if not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("RUNNER_VAULT_REPOSITORY must be a valid GitHub owner/name")
    return repository


def valid_public_hostname(hostname: str) -> bool:
    """Accept public DNS names and global IPs; reject localhost/private addresses."""
    try:
        address = ipaddress.ip_address(hostname)
        return address.is_global
    except ValueError:
        normalized = hostname.rstrip(".")
        if len(normalized) > 253 or "." not in normalized:
            return False
        labels = normalized.split(".")
        return all(DOMAIN_LABEL_RE.fullmatch(label) for label in labels)


def validate_deployment(
    payload: object,
    owner: str,
    repository: str | None = None,
) -> dict[str, str]:
    """Return normalized env values or raise ValueError without side effects."""
    expected_repository = resolve_vault_repository(owner, repository)
    if not isinstance(payload, dict):
        raise ValueError("deployment document must be a JSON object")

    url = payload.get("url")
    client_ref = payload.get("clientRef")
    deployed_repository = payload.get("repository")
    if not isinstance(deployed_repository, str) or not REPOSITORY_RE.fullmatch(deployed_repository):
        raise ValueError("deployment repository identity is missing or invalid")
    if deployed_repository.lower() != expected_repository.lower():
        raise ValueError("deployment metadata belongs to a different Vault repository")
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError("deployment URL must be a non-empty string")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValueError("deployment URL contains control characters")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError as error:
        raise ValueError("deployment URL is malformed") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not valid_public_hostname(parsed.hostname)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or parsed.port is not None
        or url not in (f"https://{parsed.netloc}", f"https://{parsed.netloc}/")
    ):
        raise ValueError("deployment URL must be a public HTTPS origin with no path, query or credentials")
    if not isinstance(client_ref, str) or not SHA_RE.fullmatch(client_ref):
        raise ValueError("clientRef must be an immutable lowercase 40-character commit SHA")

    return {
        "VAULT_URL": url.rstrip("/"),
        "VAULT_CLIENT_REPOSITORY": deployed_repository,
        "VAULT_CLIENT_REF": client_ref,
    }


def load_deployment(repository: str) -> object:
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise ValueError("Vault repository must be a valid GitHub owner/name")
    url = f"https://raw.githubusercontent.com/{repository}/main/deployment.json"
    request = Request(url, headers={"Accept": "application/json", "Cache-Control": "no-cache"})
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
        repository = resolve_vault_repository(owner, os.environ.get("RUNNER_VAULT_REPOSITORY"))
        payload = load_deployment(repository)
        values = validate_deployment(payload, owner, repository)
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write("configured=false\n")
        print(f"::notice::Vault deployment was not discovered or failed validation ({type(error).__name__}); Vault integration is disabled for this run.")
        return 0

    # Values are written only after every deployment field and repository identity
    # has passed validation. Metadata from an upstream fork is never reused.
    with open(env_path, "a", encoding="utf-8") as env_file:
        for key, value in values.items():
            env_file.write(f"{key}={value}\n")
    with open(output_path, "a", encoding="utf-8") as output:
        output.write("configured=true\n")
    print("Vault deployment discovered and validated; environment values exported.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

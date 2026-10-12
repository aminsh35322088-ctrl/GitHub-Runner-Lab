#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "runner-toolset.json"

def load_manifest(path=DEFAULT_MANIFEST):
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unsupported runner toolset schema")
    for key in ("toolchain_version", "profile_groups", "packages", "commands", "pkg_config_modules", "pkg_config_path", "goss", "actionlint"):
        if key not in data:
            raise ValueError(f"missing manifest key: {key}")
    return data

def expand(data, section, profile):
    groups = data["profile_groups"].get(profile)
    if not groups:
        raise ValueError(f"unknown profile: {profile}")
    out = []
    seen = set()
    for group in groups:
        for item in data[section].get(group, []):
            if item not in seen:
                seen.add(item)
                out.append(item)
    return out

def latest_release(data, tool):
    repo = data[tool]["latest_release"]["repo"]
    request = Request(
        f"https://api.github.com/repos/{repo}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "github-runner-lab"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except Exception as error:
        raise ValueError(f"could not resolve latest {tool} release from GitHub: {error}") from error


def release_asset_meta(data, tool, arch, release):
    config = data[tool]["latest_release"]
    arch_name = config["architectures"].get(arch)
    if not arch_name:
        raise ValueError(f"unsupported {tool} architecture: {arch}")
    tag = release.get("tag_name", "")
    version = tag[1:] if tag.startswith("v") else tag
    if not version:
        raise ValueError(f"latest {tool} release has no tag")
    filename = config["asset_template"].format(version=version, arch=arch_name)
    asset = next((item for item in release.get("assets", []) if item.get("name") == filename), None)
    digest = (asset or {}).get("digest", "")
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError(f"latest {tool} release asset {filename} has no GitHub SHA-256 digest")
    base_url = f"https://github.com/{config['repo']}/releases/download/{tag}"
    return {"version": version, "base_url": base_url, "filename": filename, "sha256": digest[7:]}


def goss_meta(data, arch):
    return release_asset_meta(data, "goss", arch, latest_release(data, "goss"))


def actionlint_meta(data, arch):
    return release_asset_meta(data, "actionlint", arch, latest_release(data, "actionlint"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("packages", "commands", "pkg-config-modules"):
        s = sub.add_parser(name)
        s.add_argument("profile", choices=("core", "build", "media", "full"))
    sub.add_parser("toolchain-version")
    sub.add_parser("pkg-config-path")
    g = sub.add_parser("goss")
    g.add_argument("arch")
    a = sub.add_parser("actionlint")
    a.add_argument("arch")
    sub.add_parser("validate")
    args = p.parse_args()
    data = load_manifest(args.manifest)
    if args.command in ("packages", "commands", "pkg-config-modules"):
        section = "pkg_config_modules" if args.command == "pkg-config-modules" else args.command
        print("\n".join(expand(data, section, args.profile)))
    elif args.command == "toolchain-version":
        print(data["toolchain_version"])
    elif args.command == "pkg-config-path":
        print(":".join(data["pkg_config_path"]))
    elif args.command == "goss":
        print(json.dumps(goss_meta(data, args.arch), sort_keys=True))
    elif args.command == "actionlint":
        print(json.dumps(actionlint_meta(data, args.arch), sort_keys=True))
    elif args.command == "validate":
        for profile in data["profile_groups"]:
            expand(data, "packages", profile)
            expand(data, "commands", profile)
            expand(data, "pkg_config_modules", profile)
        print("TOOLSET=VALID")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"toolset error: {error}", file=sys.stderr)
        raise SystemExit(2)

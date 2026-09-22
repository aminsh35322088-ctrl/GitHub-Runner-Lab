#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "runner-toolset.json"

def load_manifest(path=DEFAULT_MANIFEST):
    data = json.loads(Path(path).read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unsupported runner toolset schema")
    for key in ("toolchain_version", "profile_groups", "packages", "commands", "pkg_config_modules", "pkg_config_path", "goss"):
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

def goss_meta(data, arch):
    aliases = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}
    arch = aliases.get(arch, arch)
    asset = data["goss"]["assets"].get(arch)
    if not asset:
        raise ValueError(f"unsupported goss architecture: {arch}")
    return {
        "version": data["goss"]["version"],
        "base_url": data["goss"]["base_url"],
        **asset,
    }

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

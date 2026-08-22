#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import tomllib


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: check-release-version.py vMAJOR.MINOR.PATCH", file=sys.stderr)
        return 2

    tag = sys.argv[1]
    if re.fullmatch(r"v\d+\.\d+\.\d+", tag) is None:
        print(f"Invalid release tag '{tag}'; expected vMAJOR.MINOR.PATCH.", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    expected = tag.removeprefix("v")

    desktop_package = json.loads(
        (repo_root / "apps/desktop/package.json").read_text(encoding="utf-8")
    )
    tauri_config = json.loads(
        (repo_root / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    with (repo_root / "engine/pyproject.toml").open("rb") as engine_file:
        engine_project = tomllib.load(engine_file)

    versions = {
        "apps/desktop/package.json": desktop_package["version"],
        "apps/desktop/src-tauri/tauri.conf.json": tauri_config["version"],
        "engine/pyproject.toml": engine_project["project"]["version"],
    }
    mismatches = {path: version for path, version in versions.items() if version != expected}

    if mismatches:
        print(f"Release tag {tag} expects version {expected}, but found:", file=sys.stderr)
        for path, version in mismatches.items():
            print(f"- {path}: {version}", file=sys.stderr)
        return 1

    print(f"Release version {expected} is consistent across all manifests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

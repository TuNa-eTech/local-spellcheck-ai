#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


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
    if tomllib is not None:
        with (repo_root / "engine/pyproject.toml").open("rb") as engine_file:
            engine_project = tomllib.load(engine_file)
        engine_version = engine_project["project"]["version"]
    else:
        pyproject_text = (repo_root / "engine/pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'\[project\][\s\S]*?version\s*=\s*"([^"]+)"', pyproject_text)
        if not match:
            raise RuntimeError("Could not find project.version in engine/pyproject.toml")
        engine_version = match.group(1)

    versions = {
        "apps/desktop/package.json": desktop_package["version"],
        "apps/desktop/src-tauri/tauri.conf.json": tauri_config["version"],
        "engine/pyproject.toml": engine_version,
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

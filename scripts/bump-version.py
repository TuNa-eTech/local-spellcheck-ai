#!/usr/bin/env python3
"""Unified version bump script for SoátVăn.

Synchronizes version across all project manifests:
- VERSION (root file)
- apps/desktop/package.json
- apps/desktop/package-lock.json
- apps/desktop/src-tauri/tauri.conf.json
- apps/desktop/src-tauri/Cargo.toml
- apps/desktop/src-tauri/Cargo.lock
- engine/pyproject.toml
- engine/src/soatvan/__init__.py
- engine/uv.lock

Usage:
    python scripts/bump-version.py 0.1.7
    python scripts/bump-version.py --patch
    python scripts/bump-version.py --minor
    python scripts/bump-version.py --major
    python scripts/bump-version.py 0.1.7 --commit --tag
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def get_current_version() -> str:
    version_file = REPO_ROOT / "VERSION"
    if version_file.exists():
        v = version_file.read_text(encoding="utf-8").strip()
        if v:
            return v
    pkg_file = REPO_ROOT / "apps/desktop/package.json"
    data = json.loads(pkg_file.read_text(encoding="utf-8"))
    return str(data["version"])


def calculate_next_version(current: str, bump_type: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current)
    if not match:
        raise ValueError(f"Current version '{current}' is not valid semantic version (X.Y.Z)")
    major, minor, patch = map(int, match.groups())
    if bump_type == "patch":
        patch += 1
    elif bump_type == "minor":
        minor += 1
        patch = 0
    elif bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    else:
        raise ValueError(f"Unknown bump type '{bump_type}'")
    return f"{major}.{minor}.{patch}"


def update_json_file(path: Path, key: str, value: str, dry_run: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    data[key] = value
    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")


def update_package_lock(path: Path, new_version: str, dry_run: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    data["version"] = new_version
    if "packages" in data and "" in data["packages"]:
        data["packages"][""]["version"] = new_version
    new_text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")


def update_regex_file(path: Path, pattern: str, replacement: str, dry_run: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, count=1)
    if count == 0:
        raise RuntimeError(f"Failed to match pattern {pattern!r} in {path}")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")


def update_cargo_lock(path: Path, new_version: str, dry_run: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = r'(\[\[package\]\]\s+name = "soatvan-desktop"\s+version = ")[^"]+(")'
    new_text, count = re.subn(pattern, rf"\g<1>{new_version}\g<2>", text, count=1)
    if count == 0:
        raise RuntimeError(f"Could not find soatvan-desktop package in {path}")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")


def update_engine_init(path: Path, new_version: str, dry_run: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = r'(__version__\s*=\s*")[^"]+(")'
    new_text, count = re.subn(pattern, rf"\g<1>{new_version}\g<2>", text, count=1)
    if count == 0:
        raise RuntimeError(f"Could not find __version__ in {path}")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")


def update_engine_pyproject(path: Path, new_version: str, dry_run: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = r'(\[project\][\s\S]*?version\s*=\s*")[^"]+(")'
    new_text, count = re.subn(pattern, rf"\g<1>{new_version}\g<2>", text, count=1)
    if count == 0:
        raise RuntimeError(f"Could not find project.version in {path}")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")


def update_cargo_toml(path: Path, new_version: str, dry_run: bool = False) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = r'(\[package\][\s\S]*?version\s*=\s*")[^"]+(")'
    new_text, count = re.subn(pattern, rf"\g<1>{new_version}\g<2>", text, count=1)
    if count == 0:
        raise RuntimeError(f"Could not find package.version in {path}")
    if not dry_run:
        path.write_text(new_text, encoding="utf-8")


def update_uv_lock(dry_run: bool = False) -> None:
    if dry_run:
        print("[dry-run] Would execute: uv lock --project engine")
        return
    print("Running 'uv lock --project engine'...")
    subprocess.run(["uv", "lock", "--project", "engine"], cwd=REPO_ROOT, check=True)


def verify_version(new_version: str) -> bool:
    check_script = REPO_ROOT / "scripts" / "check-release-version.py"
    result = subprocess.run(
        [sys.executable, str(check_script), f"v{new_version}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Version verification failed:", file=sys.stderr)
        print(result.stderr or result.stdout, file=sys.stderr)
        return False
    print(f"Verified: Version {new_version} is consistent across all manifests.")
    return True


def git_commit_and_tag(new_version: str, push: bool = False) -> None:
    tag = f"v{new_version}"
    print(f"Staging changed files in git...")
    subprocess.run(["git", "add", "-A"], cwd=REPO_ROOT, check=True)
    
    commit_msg = f"chore(release): bump version to {new_version}"
    print(f"Creating commit: {commit_msg}...")
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=REPO_ROOT, check=True)

    print(f"Creating git tag: {tag}...")
    subprocess.run(["git", "tag", "-a", tag, "-m", f"SoatVan {tag}"], cwd=REPO_ROOT, check=True)

    if push:
        print("Pushing commit and tag to remote...")
        subprocess.run(["git", "push", "origin", "main"], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "push", "origin", tag], cwd=REPO_ROOT, check=True)
        print("Pushed successfully!")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump SoátVăn version across all project manifests.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("version", nargs="?", help="Explicit target version (e.g. 0.1.7 or v0.1.7)")
    group.add_argument("--patch", action="store_true", help="Bump patch version (X.Y.Z -> X.Y.Z+1)")
    group.add_argument("--minor", action="store_true", help="Bump minor version (X.Y.Z -> X.Y+1.0)")
    group.add_argument("--major", action="store_true", help="Bump major version (X.Y.Z -> X+1.0.0)")

    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing to disk")
    parser.add_argument("--commit", action="store_true", help="Create git commit for release")
    parser.add_argument("--tag", action="store_true", help="Create git tag for release (implies --commit)")
    parser.add_argument("--push", action="store_true", help="Push commit and tag to origin (implies --tag)")

    args = parser.parse_args()

    current_ver = get_current_version()
    print(f"Current version: {current_ver}")

    if args.patch:
        target_ver = calculate_next_version(current_ver, "patch")
    elif args.minor:
        target_ver = calculate_next_version(current_ver, "minor")
    elif args.major:
        target_ver = calculate_next_version(current_ver, "major")
    else:
        target_ver = args.version.removeprefix("v").strip()

    if not re.fullmatch(r"\d+\.\d+\.\d+", target_ver):
        print(f"Error: Target version '{target_ver}' must follow semantic versioning (MAJOR.MINOR.PATCH)", file=sys.stderr)
        return 1

    if target_ver == current_ver:
        print(f"Version is already {current_ver}. Nothing to bump.")
        return 0

    print(f"Bumping version: {current_ver} -> {target_ver}")

    prefix = "[dry-run] " if args.dry_run else ""
    files_to_update = [
        "VERSION",
        "apps/desktop/package.json",
        "apps/desktop/package-lock.json",
        "apps/desktop/src-tauri/tauri.conf.json",
        "apps/desktop/src-tauri/Cargo.toml",
        "apps/desktop/src-tauri/Cargo.lock",
        "engine/pyproject.toml",
        "engine/src/soatvan/__init__.py",
    ]

    for f in files_to_update:
        print(f"{prefix}Updating {f}...")

    # 1. Root VERSION
    if not args.dry_run:
        (REPO_ROOT / "VERSION").write_text(f"{target_ver}\n", encoding="utf-8")

    # 2. Desktop package.json & package-lock.json
    update_json_file(REPO_ROOT / "apps/desktop/package.json", "version", target_ver, dry_run=args.dry_run)
    update_package_lock(REPO_ROOT / "apps/desktop/package-lock.json", target_ver, dry_run=args.dry_run)

    # 3. Tauri configuration
    update_json_file(REPO_ROOT / "apps/desktop/src-tauri/tauri.conf.json", "version", target_ver, dry_run=args.dry_run)

    # 4. Cargo.toml & Cargo.lock
    update_cargo_toml(REPO_ROOT / "apps/desktop/src-tauri/Cargo.toml", target_ver, dry_run=args.dry_run)
    update_cargo_lock(REPO_ROOT / "apps/desktop/src-tauri/Cargo.lock", target_ver, dry_run=args.dry_run)

    # 5. Python pyproject.toml & __init__.py
    update_engine_pyproject(REPO_ROOT / "engine/pyproject.toml", target_ver, dry_run=args.dry_run)
    update_engine_init(REPO_ROOT / "engine/src/soatvan/__init__.py", target_ver, dry_run=args.dry_run)

    # 6. uv.lock
    update_uv_lock(dry_run=args.dry_run)

    if not args.dry_run:
        if not verify_version(target_ver):
            return 1

    if args.push or args.tag or args.commit:
        if args.dry_run:
            print(f"[dry-run] Would commit, tag v{target_ver}" + (" and push" if args.push else ""))
        else:
            git_commit_and_tag(target_ver, push=args.push)

    print(f"\nSuccessfully bumped SoátVăn to version {target_ver}!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Where the sidecar keeps per-user state, and where a portable build lives.

Kept free of adapters (no lxml, sqlite3, zipfile) so the domain and use-case
layers may import it — `tests/test_architecture.py` enforces that.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DATA_DIR_ENV = "SOATVAN_DATA_DIR"


def local_data_dir() -> Path:
    """The per-user state directory the sidecar keeps its databases in.

    The Rust host always passes `SOATVAN_DATA_DIR` (its own
    `app_local_data_dir()`, e.g. `%LOCALAPPDATA%\\vn.soatvan.desktop`), so the
    fallback below only applies when the engine is run on its own.
    """
    configured = os.environ.get(DATA_DIR_ENV)
    if configured:
        return Path(configured)
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "SoatVan"


def bundle_root() -> Path | None:
    """The portable folder this engine was shipped in, when it is a frozen build.

    A portable release lays out `<root>/SoatVan.exe` and
    `<root>/engine/soatvan-engine.exe`, so the folder the user extracted is the
    grandparent of the executable. `None` when running from source, where
    `sys.executable` is the interpreter and means nothing.
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent.parent

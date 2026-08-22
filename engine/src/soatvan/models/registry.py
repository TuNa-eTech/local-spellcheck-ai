from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ModelRegistry:
    """Fail-closed registry. Signature activation is owned by the Rust provisioner in M2."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def status(self) -> dict[str, Any]:
        active = self._root / "active" / "manifest.json"
        if not active.is_file():
            return {"state": "not_installed"}
        try:
            manifest = json.loads(active.read_text(encoding="utf-8"))
            model = active.parent / manifest["file"]
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            if model.stat().st_size != manifest["size"] or digest != manifest["sha256"]:
                return {"state": "invalid", "code": "MODEL_INTEGRITY_FAILED"}
            return {
                "state": "ready",
                "model_id": manifest["model_id"],
                "version": manifest["version"],
            }
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}

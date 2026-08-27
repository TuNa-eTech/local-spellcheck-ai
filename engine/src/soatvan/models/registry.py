from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from soatvan import PROTOCOL_VERSION
from soatvan.workflow.ports import ContextClassifier

from .classifier import LlamaCppClassifier, ModelLoadFailed, ModelRuntimeUnavailable, RuntimeFactory


class ModelRegistry:
    """Integrity check and runtime activation for packages verified by the Rust host."""

    def __init__(
        self,
        root: Path,
        runtime_factory: RuntimeFactory | None = None,
        public_key: str | None = None,
    ) -> None:
        self._root = root
        self._runtime_factory = runtime_factory
        self._cache_key: tuple[int, int, int, int, int] | None = None
        self._verified_key: tuple[int, int, int, int, int] | None = None
        self._classifier: ContextClassifier | None = None
        self._public_key = public_key or os.environ.get("SOATVAN_MODEL_PUBLIC_KEY")

    def status(self, activate: bool = True) -> dict[str, Any]:
        active = self._root / "active" / "manifest.json"
        if not active.is_file():
            self._clear_cache()
            return {"state": "not_installed"}
        try:
            manifest = json.loads(active.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError
            trust = manifest.get("trust")
            capabilities = manifest.get("capabilities")
            if manifest.get("schema_version") != 2 or not _capabilities_valid(
                capabilities, trust
            ):
                self._clear_cache()
                return {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}
            if trust == "release_signed":
                if not self._verify_signature(manifest):
                    self._clear_cache()
                    return {"state": "invalid", "code": "MODEL_SIGNATURE_INVALID"}
                if not _quality_approved(manifest.get("quality_gate")):
                    self._clear_cache()
                    return {"state": "installed", "code": "MODEL_QUALITY_GATE_REQUIRED"}
            elif trust == "local_unverified":
                if "signature" in manifest or "quality_gate" in manifest:
                    self._clear_cache()
                    return {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}
            else:
                self._clear_cache()
                return {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}
            if not _runtime_config_approved(manifest):
                self._clear_cache()
                return {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}
            if manifest.get("engine_protocol") != PROTOCOL_VERSION:
                return {"state": "invalid", "code": "MODEL_PROTOCOL_INCOMPATIBLE"}
            if not _safe_name(manifest["file"]) or not _safe_name(manifest["license_file"]):
                raise ValueError
            model = active.parent / manifest["file"]
            license_path = active.parent / manifest["license_file"]
            try:
                model_stat = model.stat()
                license_stat = license_path.stat()
            except OSError:
                self._clear_cache()
                return {"state": "invalid", "code": "MODEL_INTEGRITY_FAILED"}
            key = (
                model_stat.st_size,
                model_stat.st_mtime_ns,
                license_stat.st_size,
                license_stat.st_mtime_ns,
                active.stat().st_mtime_ns,
            )
            if activate and self._cache_key == key and self._classifier is not None:
                return _status("ready", manifest)
            if self._verified_key != key:
                digest = _sha256_file(model)
                if (
                    not license_path.is_file()
                    or not 1 <= license_stat.st_size <= 1024 * 1024
                    or model_stat.st_size != manifest["size"]
                    or digest != manifest["sha256"]
                ):
                    self._clear_cache()
                    return {"state": "invalid", "code": "MODEL_INTEGRITY_FAILED"}
                self._verified_key = key
            if not activate:
                self._close_runtime()
                return _status("installed", manifest)
            if self._cache_key != key or self._classifier is None:
                factory = self._runtime_factory
                self._classifier = (
                    LlamaCppClassifier(model, manifest, factory)
                    if factory is not None
                    else LlamaCppClassifier(model, manifest)
                )
                self._cache_key = key
            return _status("ready", manifest)
        except ModelRuntimeUnavailable:
            self._clear_cache()
            status = _status("installed", manifest)
            status["code"] = "MODEL_RUNTIME_UNAVAILABLE"
            return status
        except ModelLoadFailed:
            self._clear_cache()
            return {**_status("invalid", manifest), "code": "MODEL_LOAD_FAILED"}
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            self._clear_cache()
            return {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}

    def classifier(self) -> ContextClassifier | None:
        return self._classifier if self.status().get("state") == "ready" else None

    def supports_full_review(self) -> bool:
        status = self.status()
        capabilities = status.get("capabilities")
        return bool(
            status.get("state") in {"installed", "ready"}
            and isinstance(capabilities, dict)
            and capabilities.get("full_review") is True
        )

    def deactivate(self) -> None:
        self._clear_cache()

    def _clear_cache(self) -> None:
        self._close_runtime()
        self._verified_key = None

    def _close_runtime(self) -> None:
        close = getattr(self._classifier, "close", None)
        if callable(close):
            close()
        self._cache_key = None
        self._classifier = None

    def _verify_signature(self, manifest: dict[str, Any]) -> bool:
        if not self._public_key or not isinstance(manifest.get("signature"), str):
            return False
        try:
            key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(self._public_key, validate=True)
            )
            signature = base64.b64decode(manifest["signature"], validate=True)
            unsigned = {key: value for key, value in manifest.items() if key != "signature"}
            payload = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")
            key.verify(signature, payload)
            return True
        except (ValueError, InvalidSignature):
            return False


def _safe_name(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value != "." and "/" not in value and "\\" not in value


def _status(state: str, manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": state,
        "model_id": manifest["model_id"],
        "version": manifest["version"],
        "trust": manifest["trust"],
        "release_approved": manifest["trust"] == "release_signed",
        "capabilities": dict(manifest["capabilities"]),
    }


def _capabilities_valid(value: object, trust: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"candidate_filter", "full_review"}:
        return False
    if value.get("candidate_filter") is not True or not isinstance(
        value.get("full_review"), bool
    ):
        return False
    return trust in {"release_signed", "local_unverified"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _quality_approved(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("profiles"), list):
        return False
    corpus_hash = value.get("corpus_sha256")
    profiles = value["profiles"]
    if (
        not isinstance(corpus_hash, str)
        or len(corpus_hash) != 64
        or any(character not in "0123456789abcdef" for character in corpus_hash)
        or len(profiles) < 2
    ):
        return False
    approved_memory: list[float] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            return False
        try:
            memory = float(profile["machine_memory_mb"])
            documents = profile["documents"]
            precision = float(profile["precision"])
            recall = float(profile["recall"])
            p95 = float(profile["p95_seconds"])
            peak_rss = float(profile["peak_rss_mb"])
            report_hash = profile["report_sha256"]
        except (KeyError, TypeError, ValueError):
            return False
        if (
            not all(math.isfinite(item) for item in (memory, precision, recall, p95, peak_rss))
            or isinstance(documents, bool)
            or not isinstance(documents, int)
            or documents < 20
            or not 0.90 <= precision <= 1
            or not 0.85 <= recall <= 1
            or not 0 < p95 <= 180
            or peak_rss <= 0
            or not isinstance(report_hash, str)
            or len(report_hash) != 64
            or any(character not in "0123456789abcdef" for character in report_hash)
        ):
            return False
        approved_memory.append(memory)
    return any(7000 <= value <= 9216 for value in approved_memory) and any(
        15000 <= value <= 18432 for value in approved_memory
    )


def _runtime_config_approved(manifest: dict[str, Any]) -> bool:
    integer_limits = {
        "memory_mb": (1, 1_000_000),
        "context_size": (512, 32_768),
        "batch_size": (1, 64),
        "max_tokens": (32, 4096),
        "review_chunk_tokens": (64, 32_768),
        "timeout_seconds": (1, 900),
    }
    for name, (minimum, maximum) in integer_limits.items():
        value = manifest.get(name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not minimum <= value <= maximum
        ):
            return False
    context_size = manifest.get("context_size", 2048)
    max_tokens = manifest.get("max_tokens", 512)
    available_review_tokens = context_size - max_tokens - 256
    if available_review_tokens < 64:
        return False
    review_chunk_tokens = manifest.get("review_chunk_tokens")
    if review_chunk_tokens is not None and review_chunk_tokens > available_review_tokens:
        return False
    seed = manifest.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        return False
    confidence = manifest.get("minimum_confidence")
    return confidence is None or (
        not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
        and math.isfinite(float(confidence))
        and 0 <= float(confidence) <= 1
    )

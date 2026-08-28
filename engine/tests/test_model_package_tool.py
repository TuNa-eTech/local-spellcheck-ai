from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator, ValidationError

TOOL_PATH = Path(__file__).parents[2] / "tools" / "package_model.py"
SPEC = importlib.util.spec_from_file_location("package_model", TOOL_PATH)
assert SPEC and SPEC.loader
PACKAGE_MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE_MODEL)
canonical_unsigned = PACKAGE_MODEL.canonical_unsigned
package_model = PACKAGE_MODEL.package_model
load_private_key = PACKAGE_MODEL.load_private_key


def test_package_model_creates_verifiable_deterministic_manifest(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF-test-model")
    license_file = tmp_path / "LICENSE.txt"
    license_file.write_text("approved license", encoding="utf-8")
    package = tmp_path / "approved.svmodel"
    private_key = Ed25519PrivateKey.generate()
    reports = []
    model_sha256 = hashlib.sha256(model.read_bytes()).hexdigest()
    for memory_mb in (8192, 16384):
        report = tmp_path / f"benchmark-{memory_mb}.json"
        report.write_text(
            json.dumps(
                {
                    "model": {"id": "approved-model", "version": "1.0.0"},
                    "model_sha256": model_sha256,
                    "machine": {"memory_mb": memory_mb},
                    "documents": 20,
                    "precision": 0.91,
                    "recall": 0.86,
                    "latency_seconds": {"p95": 2.0},
                    "peak_rss_mb": 2048,
                    "corpus_sha256": "c" * 64,
                }
            ),
            encoding="utf-8",
        )
        reports.append(report)
    manifest = package_model(
        model,
        license_file,
        package,
        private_key,
        reports,
        model_id="approved-model",
        version="1.0.0",
        memory_mb=2048,
    )
    private_key.public_key().verify(
        base64.b64decode(manifest["signature"]), canonical_unsigned(manifest)
    )
    assert manifest["schema_version"] == 2
    assert manifest["trust"] == "release_signed"
    assert manifest["capabilities"] == {
        "candidate_filter": True,
        "full_review": False,
    }
    assert manifest["review_chunk_tokens"] == 1200
    assert manifest["timeout_seconds"] == 300
    with zipfile.ZipFile(package) as archive:
        assert set(archive.namelist()) == {"model.gguf", "LICENSE.txt", "manifest.json"}
        stored = json.loads(archive.read("manifest.json"))
        assert stored == manifest
        assert archive.read("model.gguf") == b"GGUF-test-model"
    schema = json.loads(
        (Path(__file__).parents[2] / "contracts" / "model-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(manifest)


def test_packager_accepts_ed25519_pem_and_rejects_report_for_other_model_bytes(
    tmp_path: Path,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "model-key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    assert load_private_key(key_path).public_key().public_bytes_raw() == (
        private_key.public_key().public_bytes_raw()
    )

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "model": {"id": "approved-model", "version": "1.0.0"},
                "model_sha256": "0" * 64,
                "machine": {"memory_mb": 8192},
                "documents": 20,
                "precision": 0.91,
                "recall": 0.86,
                "latency_seconds": {"p95": 2.0},
                "peak_rss_mb": 2048,
                "corpus_sha256": "c" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="different model bytes"):
        PACKAGE_MODEL.quality_gate_from_reports(
            [report], "approved-model", "1.0.0", "f" * 64
        )


def test_packager_rejects_reserved_or_empty_license_file(tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    license_file = tmp_path / "manifest.json"
    license_file.write_bytes(b"")
    with pytest.raises(ValueError, match="distinct"):
        package_model(
            model,
            license_file,
            tmp_path / "bad.svmodel",
            Ed25519PrivateKey.generate(),
            [],
            model_id="approved-model",
            version="1.0.0",
            memory_mb=2048,
        )


def test_packager_accepts_document_cap_larger_than_input_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    license_file = tmp_path / "LICENSE.txt"
    license_file.write_text("approved", encoding="utf-8")
    monkeypatch.setattr(
        PACKAGE_MODEL,
        "quality_gate_from_reports",
        lambda *_: {"corpus_sha256": "c" * 64, "profiles": []},
    )
    manifest = package_model(
        model,
        license_file,
        tmp_path / "accepted.svmodel",
        Ed25519PrivateKey.generate(),
        [],
        model_id="approved-model",
        version="1.0.0",
        memory_mb=2048,
        context_size=1024,
        max_tokens=512,
        review_chunk_tokens=1200,
    )

    assert manifest["review_chunk_tokens"] == 1200


def test_manifest_schema_allows_experimental_local_full_review_without_release_evidence() -> None:
    schema = json.loads(
        (Path(__file__).parents[2] / "contracts" / "model-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema)
    manifest = {
        "schema_version": 2,
        "model_id": "local-custom-model",
        "version": "local",
        "engine_protocol": 1,
        "file": "model.gguf",
        "size": 4,
        "sha256": "a" * 64,
        "license_file": "LOCAL-IMPORT-NOTICE.txt",
        "trust": "local_unverified",
        "capabilities": {"candidate_filter": True, "full_review": True},
    }
    validator.validate(manifest)

    manifest["signature"] = "auto-local" * 4
    with pytest.raises(ValidationError):
        validator.validate(manifest)
    manifest.pop("signature")
    validator.validate(manifest)
    manifest["quality_gate"] = {"corpus_sha256": "c" * 64, "profiles": []}
    with pytest.raises(ValidationError):
        validator.validate(manifest)

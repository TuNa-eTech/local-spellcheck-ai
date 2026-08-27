from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from soatvan.models import LlamaCppClassifier, ModelInferenceTimeout, ModelRegistry
from soatvan.models.classifier import _preferred_gpu_layers
from soatvan.workflow.ports import ClassificationCandidate


class Token:
    def raise_if_cancelled(self) -> None:
        return None


class Runtime:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def create_chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"choices": [{"message": {"content": self.content}}]}

    def close(self) -> None:
        self.closed = True


def test_runtime_enables_gpu_layers_only_when_backend_supports_offload() -> None:
    class Module:
        def __init__(self, supported: bool) -> None:
            self.supported = supported

        def llama_supports_gpu_offload(self) -> bool:
            return self.supported

    assert _preferred_gpu_layers(Module(True)) == -1
    assert _preferred_gpu_layers(Module(False)) == 0
    assert _preferred_gpu_layers(object()) == 0


def candidate(candidate_id: str = "candidate-1") -> ClassificationCandidate:
    return ClassificationCandidate(
        candidate_id,
        "document:p0",
        "sát nhập",
        "sáp nhập",
        "confusion.sát_nhập.v1",
        0,
        "Văn bản sát nhập nội dung.",
    )


def test_llama_classifier_accepts_only_schema_constrained_known_candidates(tmp_path: Path) -> None:
    runtime = Runtime(
        json.dumps(
            {
                "verdicts": [
                    {"candidate_id": "candidate-1", "verdict": "keep", "confidence": 0.94},
                    {"candidate_id": "invented", "verdict": "keep", "confidence": 1},
                ]
            }
        )
    )
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1", "batch_size": 2},
        lambda *_: runtime,
    )
    verdicts = classifier.classify((candidate(),), "Ưu tiên thuật ngữ nội bộ", Token())
    assert [(item.candidate_id, item.verdict, item.confidence) for item in verdicts] == [
        ("candidate-1", "keep", 0.94)
    ]
    assert runtime.calls[0]["temperature"] == 0
    assert runtime.calls[0]["response_format"]["type"] == "json_object"
    assert runtime.calls[0]["response_format"]["schema"]["additionalProperties"] is False
    assert runtime.calls[0]["stream"] is True
    prompt = json.loads(runtime.calls[0]["messages"][1]["content"])
    assert prompt["custom_rule"] == "Ưu tiên thuật ngữ nội bộ"
    assert prompt["candidates"][0]["source_text"] == "sát nhập"


def test_malformed_classifier_output_fails_closed(tmp_path: Path) -> None:
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1"},
        lambda *_: Runtime("not-json"),
    )
    assert classifier.classify((candidate(),), "", Token()) == ()


def test_classifier_rejects_an_incomplete_batch_of_verdicts(tmp_path: Path) -> None:
    runtime = Runtime(
        json.dumps(
            {
                "verdicts": [
                    {"candidate_id": "candidate-1", "verdict": "drop", "confidence": 1}
                ]
            }
        )
    )
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1", "batch_size": 2},
        lambda *_: runtime,
    )

    assert classifier.classify((candidate(), candidate("candidate-2")), "", Token()) == ()


def test_streaming_classifier_collects_json_and_enforces_deadline(tmp_path: Path) -> None:
    class StreamingRuntime:
        def create_chat_completion(self, **_: Any):
            content = '{"verdicts":[{"candidate_id":"candidate-1","verdict":"keep","confidence":1}]}'
            return iter(
                {"choices": [{"delta": {"content": part}}]}
                for part in (content[:20], content[20:])
            )

    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1", "timeout_seconds": 2},
        lambda *_: StreamingRuntime(),
    )
    assert classifier.classify((candidate(),), "", Token())[0].verdict == "keep"

    expired = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1", "timeout_seconds": -1},
        lambda *_: StreamingRuntime(),
    )
    with pytest.raises(ModelInferenceTimeout, match="MODEL_INFERENCE_TIMEOUT"):
        expired.classify((candidate(),), "", Token())


def test_classifier_deadline_covers_all_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class OneChunkRuntime:
        def create_chat_completion(self, **_: Any):
            return iter([{"choices": [{"delta": {"content": '{"verdicts":[]}'}}]}])

    ticks = iter([0.0, 1.0, 3.0])
    monkeypatch.setattr("soatvan.models.classifier.time.monotonic", lambda: next(ticks))
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1", "batch_size": 1, "timeout_seconds": 2},
        lambda *_: OneChunkRuntime(),
    )
    with pytest.raises(ModelInferenceTimeout, match="MODEL_INFERENCE_TIMEOUT"):
        classifier.classify((candidate("a"), candidate("b")), "", Token())


def test_native_abort_callback_enforces_deadline_before_first_stream_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class AbortAwareRuntime:
        def __init__(self) -> None:
            self.predicate = self._not_aborted
            self.reset = False

        @staticmethod
        def _not_aborted() -> bool:
            return False

        def set_abort_predicate(self, predicate):
            self.predicate = predicate

        def create_chat_completion(self, **_: Any):
            if self.predicate():
                raise RuntimeError("native decode aborted")
            return iter(())

        def reset_after_abort(self) -> None:
            self.reset = True

    runtime = AbortAwareRuntime()
    ticks = iter([0.0, 3.0])
    monkeypatch.setattr("soatvan.models.classifier.time.monotonic", lambda: next(ticks))
    classifier = LlamaCppClassifier(
        tmp_path / "model.gguf",
        {"model_id": "test", "version": "1", "timeout_seconds": 2},
        lambda *_: runtime,
    )
    with pytest.raises(ModelInferenceTimeout, match="MODEL_INFERENCE_TIMEOUT"):
        classifier.classify((candidate(),), "", Token())
    assert runtime.reset is True


def test_registry_only_reports_ready_after_integrity_and_smoke_load(tmp_path: Path) -> None:
    active = tmp_path / "active"
    active.mkdir()
    model = active / "model.gguf"
    model.write_bytes(b"test-model")
    (active / "LICENSE.txt").write_text("approved", encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "model_id": "test-model",
        "version": "1.0.0",
        "engine_protocol": 1,
        "file": model.name,
        "size": model.stat().st_size,
        "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "license_file": "LICENSE.txt",
        "trust": "release_signed",
        "capabilities": {"candidate_filter": True, "full_review": False},
        "quality_gate": {
            "corpus_sha256": "c" * 64,
            "profiles": [
                {
                    "machine_memory_mb": memory,
                    "documents": 20,
                    "precision": 0.91,
                    "recall": 0.86,
                    "p95_seconds": 2,
                    "peak_rss_mb": 2048,
                    "report_sha256": character * 64,
                }
                for memory, character in ((8192, "a"), (16384, "b"))
            ],
        },
    }
    private_key = Ed25519PrivateKey.generate()

    def write_signed() -> None:
        manifest.pop("signature", None)
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        manifest["signature"] = base64.b64encode(private_key.sign(payload)).decode()
        (active / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    write_signed()
    runtime = Runtime('{"verdicts":[]}')
    public_key = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    ).decode()
    registry = ModelRegistry(tmp_path, lambda *_: runtime, public_key)
    assert registry.status() == {
        "state": "ready",
        "model_id": "test-model",
        "version": "1.0.0",
        "trust": "release_signed",
        "release_approved": True,
        "capabilities": {"candidate_filter": True, "full_review": False},
    }
    assert registry.classifier() is not None

    manifest["timeout_seconds"] = 901
    write_signed()
    assert registry.status() == {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}
    manifest.pop("timeout_seconds")
    write_signed()
    assert registry.status()["state"] == "ready"
    manifest["context_size"] = 512
    manifest["max_tokens"] = 512
    write_signed()
    assert registry.status() == {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}
    manifest.pop("context_size")
    manifest.pop("max_tokens")
    write_signed()
    assert registry.status(activate=False)["state"] == "installed"
    assert runtime.closed is True
    assert registry.status()["state"] == "ready"

    model.write_bytes(b"tampered")
    assert registry.status() == {"state": "invalid", "code": "MODEL_INTEGRITY_FAILED"}
    assert registry.classifier() is None
    assert runtime.closed is True


def test_registry_runs_local_import_with_experimental_full_review(
    tmp_path: Path,
) -> None:
    active = tmp_path / "active"
    active.mkdir()
    model = active / "model.gguf"
    model.write_bytes(b"test-model")
    notice = active / "LOCAL-IMPORT-NOTICE.txt"
    notice.write_text("No license or release approval supplied.", encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "model_id": "local-model",
        "version": "local",
        "engine_protocol": 1,
        "file": model.name,
        "size": model.stat().st_size,
        "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "license_file": notice.name,
        "trust": "local_unverified",
        "capabilities": {"candidate_filter": True, "full_review": True},
    }
    (active / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    runtime = Runtime('{"verdicts":[]}')
    registry = ModelRegistry(tmp_path, lambda *_: runtime)

    assert registry.status() == {
        "state": "ready",
        "model_id": "local-model",
        "version": "local",
        "trust": "local_unverified",
        "release_approved": False,
        "capabilities": {"candidate_filter": True, "full_review": True},
    }
    assert registry.classifier() is not None
    assert registry.supports_full_review() is True


def test_registry_rejects_legacy_auto_local_signature_but_allows_full_review(
    tmp_path: Path,
) -> None:
    active = tmp_path / "active"
    active.mkdir()
    model = active / "model.gguf"
    model.write_bytes(b"test-model")
    notice = active / "NOTICE.txt"
    notice.write_text("notice", encoding="utf-8")
    manifest = {
        "schema_version": 2,
        "model_id": "local-model",
        "version": "local",
        "engine_protocol": 1,
        "file": model.name,
        "size": model.stat().st_size,
        "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
        "license_file": notice.name,
        "trust": "local_unverified",
        "capabilities": {"candidate_filter": True, "full_review": False},
        "signature": "auto-local",
    }
    manifest_path = active / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    registry = ModelRegistry(tmp_path, lambda *_: Runtime('{"verdicts":[]}'))
    assert registry.status() == {"state": "invalid", "code": "MODEL_MANIFEST_INVALID"}

    manifest.pop("signature")
    manifest["capabilities"]["full_review"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert registry.status()["state"] == "ready"
    assert registry.supports_full_review() is True

from __future__ import annotations

import argparse
import json
import platform
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Self

import psutil
from evaluate_findings import validate_run
from soatvan.checking import Finding, Preset, RuleEngine
from soatvan.dictionary import SqliteDictionaryRepository
from soatvan.document import DocxPackage
from soatvan.models import ModelRegistry
from soatvan.workflow import ProcessDocument, ProcessRequest
from soatvan.workflow.ports import AnnotationResult, CancellationToken


class Token:
    def raise_if_cancelled(self) -> None:
        return None


class MemorySampler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._process = psutil.Process()
        self.peak_rss = self._process.memory_info().rss
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        self.peak_rss = max(self.peak_rss, self._process.memory_info().rss)

    def _sample(self) -> None:
        while not self._stop.wait(0.05):
            self.peak_rss = max(self.peak_rss, self._process.memory_info().rss)


class CapturingDocxPackage:
    def __init__(self) -> None:
        self._delegate = DocxPackage()
        self.findings: tuple[Finding, ...] = ()

    def inspect(self, source: Path) -> dict[str, object]:
        return self._delegate.inspect(source)

    def read_blocks(self, source: Path):
        return self._delegate.read_blocks(source)

    def write_annotations(
        self,
        source: Path,
        target: Path,
        findings: Iterable[Finding],
        cancellation: CancellationToken | None = None,
    ) -> AnnotationResult:
        materialized = tuple(findings)
        result = self._delegate.write_annotations(
            source, target, materialized, cancellation
        )
        written = frozenset(result.written_ids)
        self.findings = tuple(item for item in materialized if item.id in written)
        return result


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _manifest(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "models" / "active" / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("active model manifest is invalid")
    return value


def _quality(review: dict[str, int | str] | None) -> dict[str, Any]:
    if review is None:
        return {
            "status": "complete",
            "coverage_percent": 100.0,
            "timeout_chunks": 0,
            "invalid_output_chunks": 0,
            "truncated_outputs": None,
        }
    total_blocks = int(review["total_blocks"])
    reviewed_blocks = int(review["reviewed_blocks"])
    return {
        "status": str(review["status"]),
        "coverage_percent": (
            100.0 * reviewed_blocks / total_blocks if total_blocks else 100.0
        ),
        "timeout_chunks": int(review["timeout_chunks"]),
        "invalid_output_chunks": int(review["invalid_output_chunks"]),
        "truncated_outputs": None,
    }


def export_run(
    *,
    document_path: Path,
    data_dir: Path,
    annotated_output: Path,
    run_output: Path,
    run_id: str,
    corpus_id: str,
    document_id: str,
    prompt_id: str,
    custom_prompt: str,
    quantization: str,
    preset: Preset,
    rules_only: bool,
    include_rule_findings: bool,
) -> dict[str, Any]:
    documents = CapturingDocxPackage()
    dictionary = SqliteDictionaryRepository(data_dir / "dictionary.db")
    registry: ModelRegistry | None = None
    if rules_only:
        configuration = {
            "prompt_id": prompt_id,
            "model_id": "rules-only",
            "model_version": "engine",
            "quantization": "none",
        }
    else:
        manifest = _manifest(data_dir)
        configuration = {
            "prompt_id": prompt_id,
            "model_id": str(manifest["model_id"]),
            "model_version": str(manifest["version"]),
            "quantization": quantization,
        }
        registry = ModelRegistry(data_dir / "models")
        status = registry.status(activate=False)
        if status.get("state") != "installed":
            raise ValueError(f"model is not installed: {status}")
        capabilities = status.get("capabilities")
        if not isinstance(capabilities, dict) or capabilities.get("full_review") is not True:
            raise ValueError("active model does not support full review")
    processor = ProcessDocument(documents, dictionary, RuleEngine(), registry)
    request = ProcessRequest(
        source=document_path,
        temporary_output=annotated_output,
        preset=preset,
        use_model=not rules_only,
        custom_prompt=custom_prompt,
        ignored_words=dictionary.ignored_words(),
        full_review=not rules_only,
        include_rule_findings=include_rule_findings and not rules_only,
    )
    progress_events: list[dict[str, Any]] = []
    started = time.perf_counter()
    try:
        with MemorySampler() as memory:
            result = processor.execute(
                request,
                lambda stage, percent, message: progress_events.append(
                    {
                        "stage": stage,
                        "percent": percent,
                        "message": message,
                        "elapsed_seconds": time.perf_counter() - started,
                    }
                ),
                Token(),
            )
        latency_seconds = time.perf_counter() - started
    finally:
        if registry is not None:
            registry.deactivate()
    run = {
        "schema_version": 1,
        "run_id": run_id,
        "corpus_id": corpus_id,
        "configuration": configuration,
        "performance": {
            "latency_seconds": latency_seconds,
            "peak_rss_mb": memory.peak_rss / 1024 / 1024,
        },
        "quality": _quality(result.review),
        "documents": [
            {
                "document_id": document_id,
                "source_sha256": _sha256(document_path),
                "findings": [item.as_dict() for item in documents.findings],
            }
        ],
    }
    validate_run(run)
    _write_json(run_output, run)
    diagnostics = {
        "run": run,
        "process_result": {
            "finding_count": result.finding_count,
            "output_path": str(result.output_path) if result.output_path else None,
            "counts": result.counts,
            "review": result.review,
        },
        "progress": progress_events,
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": psutil.cpu_count(),
            "memory_mb": psutil.virtual_memory().total / 1024 / 1024,
        },
    }
    return diagnostics


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the SoatVan workflow and export its final findings for benchmarking"
    )
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--annotated-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--corpus-id", required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--prompt-id", required=True)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--quantization", default="local-import-unknown")
    parser.add_argument("--preset", choices=[item.value for item in Preset], default="standard")
    parser.add_argument("--rules-only", action="store_true")
    parser.add_argument("--include-rule-findings", action="store_true")
    args = parser.parse_args()
    try:
        custom_prompt = (
            args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else ""
        )
        diagnostics = export_run(
            document_path=args.document,
            data_dir=args.data_dir,
            annotated_output=args.annotated_output,
            run_output=args.output,
            run_id=args.run_id,
            corpus_id=args.corpus_id,
            document_id=args.document_id,
            prompt_id=args.prompt_id,
            custom_prompt=custom_prompt,
            quantization=args.quantization,
            preset=Preset(args.preset),
            rules_only=args.rules_only,
            include_rule_findings=args.include_rule_findings,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    summary = {
        "run_output": str(args.output),
        "annotated_output": diagnostics["process_result"]["output_path"],
        "findings": diagnostics["process_result"]["finding_count"],
        "performance": diagnostics["run"]["performance"],
        "quality": diagnostics["run"]["quality"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import threading
import time
from pathlib import Path
from typing import Any, Self

import psutil

from soatvan.models import LlamaCppClassifier
from soatvan.workflow.ports import ClassificationCandidate

CANDIDATE_FIELDS = {
    "candidate_id",
    "paragraph_id",
    "source_text",
    "suggestion",
    "reason_code",
    "occurrence_index",
    "context",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * ratio + 0.5)))
    return ordered[index]


def validate_corpus(value: object, minimum_documents: int = 20) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) < minimum_documents:
        raise ValueError(f"corpus requires at least {minimum_documents} documents")
    documents: set[str] = set()
    total_candidates = 0
    validated: list[dict[str, Any]] = []
    for case in value:
        if not isinstance(case, dict):
            raise ValueError("each corpus entry must be an object")
        document_id = case.get("document_id")
        candidates = case.get("candidates")
        expected_keep = case.get("expected_keep")
        ground_truth_total = case.get("ground_truth_total")
        custom_prompt = case.get("custom_prompt", "")
        if (
            not isinstance(document_id, str)
            or not document_id.strip()
            or document_id in documents
            or not isinstance(candidates, list)
            or not isinstance(expected_keep, list)
            or isinstance(ground_truth_total, bool)
            or not isinstance(ground_truth_total, int)
            or ground_truth_total < 0
            or not isinstance(custom_prompt, str)
            or len(custom_prompt) > 1000
        ):
            raise ValueError("invalid corpus document boundary")
        candidate_ids: set[str] = set()
        for item in candidates:
            if not isinstance(item, dict) or set(item) != CANDIDATE_FIELDS:
                raise ValueError("invalid classifier candidate")
            candidate_id = item.get("candidate_id")
            if (
                not isinstance(candidate_id, str)
                or not candidate_id
                or candidate_id in candidate_ids
                or isinstance(item.get("occurrence_index"), bool)
                or not isinstance(item.get("occurrence_index"), int)
                or item["occurrence_index"] < 0
                or any(
                    not isinstance(item.get(field), str)
                    for field in CANDIDATE_FIELDS - {"occurrence_index"}
                )
            ):
                raise ValueError("invalid classifier candidate")
            candidate_ids.add(candidate_id)
        if (
            any(not isinstance(item, str) for item in expected_keep)
            or len(set(expected_keep)) != len(expected_keep)
            or not set(expected_keep) <= candidate_ids
            or ground_truth_total < len(expected_keep)
        ):
            raise ValueError("ground truth must reference known candidates")
        documents.add(document_id)
        total_candidates += len(candidates)
        validated.append(case)
    if len(documents) < minimum_documents or total_candidates == 0:
        raise ValueError("corpus lacks distinct documents or candidates")
    return validated


def evaluate(classifier: Any, cases: list[dict[str, Any]]) -> dict[str, Any]:
    true_positive = 0
    predicted = 0
    expected = 0
    latencies: list[float] = []
    for case in cases:
        candidates = tuple(ClassificationCandidate(**item) for item in case["candidates"])
        expected_keep = set(case["expected_keep"])
        started = time.perf_counter()
        verdicts = classifier.classify(candidates, str(case.get("custom_prompt", "")), Token())
        latencies.append(time.perf_counter() - started)
        actual_keep = {
            item.candidate_id
            for item in verdicts
            if item.verdict == "keep" and item.confidence >= classifier.minimum_confidence
        }
        true_positive += len(actual_keep & expected_keep)
        predicted += len(actual_keep)
        expected += int(case["ground_truth_total"])
    return {
        "documents": len({str(case["document_id"]) for case in cases}),
        "cases": len(cases),
        "candidates": sum(len(case["candidates"]) for case in cases),
        "precision": true_positive / predicted if predicted else (1.0 if expected == 0 else 0.0),
        "recall": true_positive / expected if expected else 1.0,
        "latency_seconds": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": max(latencies, default=0.0),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark a local SoatVan classifier")
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-precision", type=float, default=0.90)
    parser.add_argument("--min-recall", type=float, default=0.85)
    parser.add_argument("--max-p95-seconds", type=float, required=True)
    parser.add_argument("--max-rss-mb", type=float, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        cases = validate_corpus(json.loads(args.corpus.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    with MemorySampler() as memory:
        load_started = time.perf_counter()
        classifier = LlamaCppClassifier(args.model, manifest)
        load_seconds = time.perf_counter() - load_started
        try:
            metrics = evaluate(classifier, cases)
        finally:
            classifier.close()
    report = {
        "model": {"id": manifest["model_id"], "version": manifest["version"]},
        "model_sha256": file_sha256(args.model),
        "machine": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "logical_cpus": psutil.cpu_count(),
            "memory_mb": round(psutil.virtual_memory().total / 1024 / 1024, 2),
        },
        "load_seconds": load_seconds,
        "peak_rss_mb": round(memory.peak_rss / 1024 / 1024, 2),
        "corpus_sha256": hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
        **metrics,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = (
        report["precision"] < args.min_precision
        or report["recall"] < args.min_recall
        or report["latency_seconds"]["p95"] > args.max_p95_seconds
        or report["peak_rss_mb"] > args.max_rss_mb
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

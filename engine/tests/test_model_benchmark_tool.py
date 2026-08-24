from __future__ import annotations

import importlib.util
from pathlib import Path

from soatvan.workflow.ports import ClassifierVerdict

TOOL_PATH = Path(__file__).parents[2] / "tools" / "benchmark_model.py"
SPEC = importlib.util.spec_from_file_location("benchmark_model", TOOL_PATH)
assert SPEC and SPEC.loader
BENCHMARK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCHMARK)


class Classifier:
    minimum_confidence = 0.8

    def classify(self, candidates, custom_prompt, cancellation):
        del custom_prompt
        cancellation.raise_if_cancelled()
        return tuple(
            ClassifierVerdict(item.candidate_id, "keep" if item.candidate_id == "a" else "drop", 1)
            for item in candidates
        )


def test_benchmark_reports_quality_and_latency_metrics() -> None:
    cases = [
        {
            "document_id": "doc-01",
            "candidates": [
                {
                    "candidate_id": "a",
                    "paragraph_id": "p0",
                    "source_text": "sát nhập",
                    "suggestion": "sáp nhập",
                    "reason_code": "confusion",
                    "occurrence_index": 0,
                    "context": "sát nhập",
                },
                {
                    "candidate_id": "b",
                    "paragraph_id": "p0",
                    "source_text": "OpenAI",
                    "suggestion": "",
                    "reason_code": "unknown",
                    "occurrence_index": 0,
                    "context": "OpenAI",
                },
            ],
            "expected_keep": ["a"],
            "ground_truth_total": 1,
        }
    ]
    assert BENCHMARK.validate_corpus(cases, minimum_documents=1) == cases
    report = BENCHMARK.evaluate(Classifier(), cases)
    assert report["precision"] == 1
    assert report["recall"] == 1
    assert report["cases"] == 1
    assert report["documents"] == 1
    assert report["candidates"] == 2
    assert report["latency_seconds"]["p95"] >= 0


def test_benchmark_rejects_corpus_that_cannot_measure_end_to_end_recall() -> None:
    cases = [
        {
            "document_id": "doc-01",
            "candidates": [],
            "expected_keep": [],
            "ground_truth_total": 1,
        }
    ]
    try:
        BENCHMARK.validate_corpus(cases, minimum_documents=1)
    except ValueError as error:
        assert "candidates" in str(error)
    else:
        raise AssertionError("candidate-free corpus must not pass the benchmark gate")

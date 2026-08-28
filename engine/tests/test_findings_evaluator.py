from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

TOOL_PATH = Path(__file__).parents[2] / "tools" / "evaluate_findings.py"
SPEC = importlib.util.spec_from_file_location("evaluate_findings", TOOL_PATH)
assert SPEC and SPEC.loader
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)

SOURCE_SHA256 = "a" * 64


def annotation(
    annotation_id: str,
    disposition: str,
    category: str,
    block_id: str,
    start: int,
    source_text: str,
    accepted_suggestions: list[str] | None | object = ...,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": annotation_id,
        "disposition": disposition,
        "category": category,
        "block_id": block_id,
        "start": start,
        "end": start + len(source_text),
        "source_text": source_text,
    }
    if accepted_suggestions is not ...:
        value["accepted_suggestions"] = accepted_suggestions
    return value


def finding(
    finding_id: str,
    block_id: str,
    start: int,
    source_text: str,
    suggestion: str,
    confidence: float,
    category: str = "spelling",
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "category": category,
        "origin": "llm",
        "detector_id": f"test.{finding_id}",
        "block_id": block_id,
        "start": start,
        "end": start + len(source_text),
        "source_text": source_text,
        "suggestion": suggestion,
        "reason": "Test finding",
        "rule_version": "test-v1",
        "confidence": confidence,
    }


def ground_truth(annotations: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "corpus_id": "corpus-v1",
        "provenance": "synthetic_example",
        "documents": [
            {
                "document_id": "doc-01",
                "source_sha256": SOURCE_SHA256,
                "annotations": annotations,
            }
        ],
    }


def run(findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": "run-v1",
        "corpus_id": "corpus-v1",
        "configuration": {
            "prompt_id": "prompt-a",
            "model_id": "model-a",
            "model_version": "1",
            "quantization": "Q4_K_M",
        },
        "performance": {"latency_seconds": 12.5, "peak_rss_mb": 2048},
        "quality": {
            "status": "complete",
            "coverage_percent": 100,
            "timeout_chunks": 0,
            "invalid_output_chunks": 0,
            "truncated_outputs": 0,
        },
        "documents": [
            {
                "document_id": "doc-01",
                "source_sha256": SOURCE_SHA256,
                "findings": findings,
            }
        ],
    }


def mixed_case() -> tuple[dict[str, Any], dict[str, Any]]:
    truth = ground_truth(
        [
            annotation("R1", "required", "spelling", "document:p0", 0, "xữ lý", ["xử lý"]),
            annotation(
                "R2",
                "required",
                "date",
                "document:p1",
                0,
                "31-6-2026",
                ["30-6-2026"],
            ),
            annotation(
                "O1", "optional", "word_choice", "document:p0", 7, "kì hạn", ["kỳ hạn"]
            ),
            annotation("N1", "must_not_warn", "proper_name", "document:p0", 15, "OpenAI"),
        ]
    )
    actual = run(
        [
            finding("F1", "document:p0", 0, "xữ lý", "xử lý", 0.96),
            finding("F2", "document:p0", 7, "kì hạn", "kỳ hạn", 0.89, "word_choice"),
            finding("F3", "document:p0", 15, "OpenAI", "Open AI", 0.91, "word_choice"),
            finding("F4", "document:p1", 5, "2026", "2027", 0.82, "technical"),
        ]
    )
    return truth, actual


def test_evaluator_scores_required_optional_negative_and_unmatched_findings() -> None:
    truth, actual = mixed_case()

    report = EVALUATOR.evaluate(truth, actual)

    assert report["totals"] == {
        "ground_truth_required": 2,
        "ground_truth_optional": 1,
        "ground_truth_must_not_warn": 1,
        "findings": 4,
        "scored_findings": 3,
        "true_positive": 1,
        "false_positive": 2,
        "false_negative": 1,
        "optional_accepted": 1,
        "explicit_negative_hits": 1,
        "precision": pytest.approx(1 / 3),
        "recall": 0.5,
        "f1": 0.4,
    }
    assert report["by_confidence"]["certain"]["precision"] == 0.5
    assert report["by_confidence"]["uncertain"]["optional_accepted"] == 1
    assert {item["reason"] for item in report["outcomes"]} == {
        "accepted_required",
        "accepted_optional",
        "explicit_negative",
        "unmatched_prediction",
        "missed_required",
    }
    assert report["by_category"]["date"]["false_negative"] == 1
    assert report["by_category"]["proper_name"]["explicit_negative_hits"] == 1


def test_duplicate_prediction_is_fp_and_null_accepts_any_non_noop_suggestion() -> None:
    truth = ground_truth(
        [
            annotation(
                "R1",
                "required",
                "spelling",
                "document:p0",
                0,
                "xữ lý",
                ["xử lý", "xử lí"],
            ),
            annotation("R2", "required", "logic", "document:p1", 0, "Tổng sai", None),
        ]
    )
    actual = run(
        [
            finding("F1", "document:p0", 0, "xữ lý", "xử lí", 0.95),
            finding("F2", "document:p0", 0, "xữ lý", "xử lý", 0.94),
            finding("F3", "document:p1", 0, "Tổng sai", "Kiểm tra lại tổng", 0.88, "grammar"),
        ]
    )

    report = EVALUATOR.evaluate(truth, actual)

    assert report["totals"]["true_positive"] == 2
    assert report["totals"]["false_positive"] == 1
    assert report["totals"]["false_negative"] == 0
    assert report["totals"]["precision"] == pytest.approx(2 / 3)
    duplicate = next(item for item in report["outcomes"] if item["finding_id"] == "F2")
    assert duplicate["reason"] == "duplicate_prediction"


def test_suggestion_mismatch_is_both_fp_and_fn() -> None:
    truth = ground_truth(
        [annotation("R1", "required", "spelling", "document:p0", 0, "xữ lý", ["xử lý"])]
    )
    actual = run([finding("F1", "document:p0", 0, "xữ lý", "xử lí khác", 0.99)])

    report = EVALUATOR.evaluate(truth, actual)

    assert report["totals"]["true_positive"] == 0
    assert report["totals"]["false_positive"] == 1
    assert report["totals"]["false_negative"] == 1
    assert [item["reason"] for item in report["outcomes"]] == [
        "suggestion_mismatch",
        "missed_required",
    ]


def test_semantic_validation_rejects_ambiguous_or_mismatched_inputs() -> None:
    truth, actual = mixed_case()
    duplicate_anchor = copy.deepcopy(truth)
    duplicate_anchor["documents"][0]["annotations"].append(
        annotation("R3", "required", "spelling", "document:p0", 0, "xữ lý", ["xử lý"])
    )
    with pytest.raises(ValueError, match="duplicate ground-truth anchor"):
        EVALUATOR.validate_ground_truth(duplicate_anchor)

    bad_length = copy.deepcopy(truth)
    bad_length["documents"][0]["annotations"][0]["end"] += 1
    with pytest.raises(ValueError, match="anchor length mismatch"):
        EVALUATOR.validate_ground_truth(bad_length)

    wrong_hash = copy.deepcopy(actual)
    wrong_hash["documents"][0]["source_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="source_sha256 mismatch"):
        EVALUATOR.evaluate(truth, wrong_hash)

    draft = copy.deepcopy(truth)
    draft["provenance"] = "draft"
    with pytest.raises(ValueError, match="must be finalized"):
        EVALUATOR.evaluate(draft, actual)

    invalid_negative = copy.deepcopy(truth)
    invalid_negative["documents"][0]["annotations"][-1]["accepted_suggestions"] = ["Open AI"]
    with pytest.raises(ValueError, match="schema invalid"):
        EVALUATOR.validate_ground_truth(invalid_negative)


def test_repository_example_report_is_valid_and_reproducible() -> None:
    examples = Path(__file__).parents[2] / "benchmarks" / "examples"
    truth_path = examples / "synthetic-ground-truth.json"
    run_path = examples / "synthetic-run.json"
    expected_path = examples / "synthetic-report.json"
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    actual = json.loads(run_path.read_text(encoding="utf-8"))
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    report = EVALUATOR.evaluate(
        truth,
        actual,
        corpus_sha256=EVALUATOR._file_sha256(truth_path),
        run_sha256=EVALUATOR._file_sha256(run_path),
    )

    assert report == expected
    assert EVALUATOR.validate_report(expected) == expected


def test_cli_writes_valid_report_before_quality_gate_failure(tmp_path: Path) -> None:
    truth, actual = mixed_case()
    truth_path = tmp_path / "truth.json"
    run_path = tmp_path / "run.json"
    report_path = tmp_path / "report.json"
    failed_report_path = tmp_path / "failed-report.json"
    truth_path.write_text(json.dumps(truth, ensure_ascii=False), encoding="utf-8")
    run_path.write_text(json.dumps(actual, ensure_ascii=False), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--ground-truth",
            str(truth_path),
            "--run",
            str(run_path),
            "--output",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert EVALUATOR.validate_report(report) == report
    assert report["corpus_sha256"] == EVALUATOR._file_sha256(truth_path)
    assert report["run_sha256"] == EVALUATOR._file_sha256(run_path)

    failed = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--ground-truth",
            str(truth_path),
            "--run",
            str(run_path),
            "--output",
            str(failed_report_path),
            "--min-precision",
            "0.9",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    assert failed_report_path.is_file()

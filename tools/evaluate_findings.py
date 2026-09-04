from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS = Path(__file__).parents[1] / "contracts"
DEFAULT_CERTAIN_CONFIDENCE = 0.9

JsonObject = dict[str, Any]
Anchor = tuple[str, int, int, str]


def _validator(schema_name: str) -> Draft202012Validator:
    schemas: dict[str, JsonObject] = {}
    registry = Registry()
    for path in CONTRACTS.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        schemas[path.name] = schema
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    return Draft202012Validator(schemas[schema_name], registry=registry)


def _validate_schema(value: object, schema_name: str, label: str) -> JsonObject:
    errors = sorted(_validator(schema_name).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        error = errors[0]
        location = "$" + "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in error.path
        )
        raise ValueError(f"{label} schema invalid at {location}: {error.message}")
    assert isinstance(value, dict)
    return value


def _anchor(item: JsonObject) -> Anchor:
    return (
        str(item["block_id"]),
        int(item["start"]),
        int(item["end"]),
        str(item["source_text"]),
    )


def validate_ground_truth(value: object) -> JsonObject:
    corpus = _validate_schema(
        value, "benchmark-ground-truth.schema.json", "ground truth"
    )
    document_ids: set[str] = set()
    for document in corpus["documents"]:
        document_id = str(document["document_id"])
        if document_id in document_ids:
            raise ValueError(f"duplicate ground-truth document_id: {document_id}")
        document_ids.add(document_id)
        annotation_ids: set[str] = set()
        anchors: set[Anchor] = set()
        for annotation in document["annotations"]:
            annotation_id = str(annotation["id"])
            anchor = _anchor(annotation)
            if annotation_id in annotation_ids:
                raise ValueError(f"duplicate ground-truth annotation id: {annotation_id}")
            if anchor in anchors:
                raise ValueError(
                    f"duplicate ground-truth anchor in {document_id}: {anchor!r}"
                )
            if int(annotation["end"]) - int(annotation["start"]) != len(
                str(annotation["source_text"])
            ):
                raise ValueError(f"ground-truth anchor length mismatch: {annotation_id}")
            annotation_ids.add(annotation_id)
            anchors.add(anchor)
    return corpus


def validate_run(value: object) -> JsonObject:
    run = _validate_schema(value, "benchmark-run.schema.json", "benchmark run")
    document_ids: set[str] = set()
    for document in run["documents"]:
        document_id = str(document["document_id"])
        if document_id in document_ids:
            raise ValueError(f"duplicate run document_id: {document_id}")
        document_ids.add(document_id)
        finding_ids: set[str] = set()
        for finding in document["findings"]:
            finding_id = str(finding["id"])
            confidence = float(finding["confidence"])
            if finding_id in finding_ids:
                raise ValueError(f"duplicate finding id in {document_id}: {finding_id}")
            if not math.isfinite(confidence):
                raise ValueError(f"non-finite finding confidence: {finding_id}")
            if int(finding["end"]) - int(finding["start"]) != len(
                str(finding["source_text"])
            ):
                raise ValueError(f"finding anchor length mismatch: {finding_id}")
            finding_ids.add(finding_id)
    return run


def validate_report(value: object) -> JsonObject:
    return _validate_schema(value, "benchmark-report.schema.json", "benchmark report")


def _new_score() -> JsonObject:
    return {
        "ground_truth_required": 0,
        "ground_truth_optional": 0,
        "ground_truth_must_not_warn": 0,
        "findings": 0,
        "scored_findings": 0,
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
        "optional_accepted": 0,
        "explicit_negative_hits": 0,
    }


def _new_prediction_score() -> JsonObject:
    return {
        "findings": 0,
        "scored_findings": 0,
        "true_positive": 0,
        "false_positive": 0,
        "optional_accepted": 0,
        "explicit_negative_hits": 0,
    }


def _finalize_score(score: JsonObject) -> JsonObject:
    true_positive = int(score["true_positive"])
    false_positive = int(score["false_positive"])
    false_negative = int(score["false_negative"])
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = (
        true_positive / precision_denominator
        if precision_denominator
        else (1.0 if int(score["ground_truth_required"]) == 0 else 0.0)
    )
    recall = true_positive / recall_denominator if recall_denominator else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {**score, "precision": precision, "recall": recall, "f1": f1}


def _finalize_prediction_score(score: JsonObject) -> JsonObject:
    true_positive = int(score["true_positive"])
    false_positive = int(score["false_positive"])
    denominator = true_positive + false_positive
    return {**score, "precision": true_positive / denominator if denominator else 1.0}


def _ground_truth_counter(disposition: str) -> str:
    if disposition == "must_not_warn":
        return "ground_truth_must_not_warn"
    return f"ground_truth_{disposition}"


def _add_ground_truth(score: JsonObject, disposition: str) -> None:
    counter = _ground_truth_counter(disposition)
    score[counter] = int(score[counter]) + 1


def _record_prediction(
    scores: tuple[JsonObject, ...],
    confidence_score: JsonObject,
    outcome: str,
    reason: str,
) -> None:
    for score in (*scores, confidence_score):
        score["findings"] = int(score["findings"]) + 1
    if outcome == "optional":
        for score in (*scores, confidence_score):
            score["optional_accepted"] = int(score["optional_accepted"]) + 1
        return
    result_counter = "true_positive" if outcome == "tp" else "false_positive"
    for score in (*scores, confidence_score):
        score["scored_findings"] = int(score["scored_findings"]) + 1
        score[result_counter] = int(score[result_counter]) + 1
        if reason == "explicit_negative":
            score["explicit_negative_hits"] = int(score["explicit_negative_hits"]) + 1


def _record_miss(scores: tuple[JsonObject, ...]) -> None:
    for score in scores:
        score["false_negative"] = int(score["false_negative"]) + 1


def _suggestion_is_accepted(annotation: JsonObject, finding: JsonObject) -> bool:
    accepted = annotation["accepted_suggestions"]
    suggestion = str(finding["suggestion"])
    if accepted is None:
        return suggestion != str(annotation["source_text"])
    return suggestion in accepted


def _canonical_sha256(value: JsonObject) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate(
    ground_truth_value: object,
    run_value: object,
    *,
    certain_confidence: float = DEFAULT_CERTAIN_CONFIDENCE,
    corpus_sha256: str | None = None,
    run_sha256: str | None = None,
) -> JsonObject:
    if not math.isfinite(certain_confidence) or not 0 <= certain_confidence <= 1:
        raise ValueError("certain confidence threshold must be between 0 and 1")
    ground_truth = validate_ground_truth(ground_truth_value)
    if ground_truth["provenance"] == "draft":
        raise ValueError("draft ground truth must be finalized before evaluation")
    run = validate_run(run_value)
    if ground_truth["corpus_id"] != run["corpus_id"]:
        raise ValueError("ground truth and run corpus_id do not match")

    truth_documents = {item["document_id"]: item for item in ground_truth["documents"]}
    run_documents = {item["document_id"]: item for item in run["documents"]}
    if set(truth_documents) != set(run_documents):
        raise ValueError("ground truth and run must contain the same document_ids")
    for document_id, truth_document in truth_documents.items():
        if truth_document["source_sha256"] != run_documents[document_id]["source_sha256"]:
            raise ValueError(f"source_sha256 mismatch for document: {document_id}")

    totals = _new_score()
    document_scores = {document_id: _new_score() for document_id in truth_documents}
    category_scores: dict[str, JsonObject] = {}
    confidence_scores = {
        "certain": _new_prediction_score(),
        "uncertain": _new_prediction_score(),
    }
    outcomes: list[JsonObject] = []

    for document_id, truth_document in truth_documents.items():
        document_score = document_scores[document_id]
        for annotation in truth_document["annotations"]:
            category_score = category_scores.setdefault(
                str(annotation["category"]), _new_score()
            )
            disposition = str(annotation["disposition"])
            _add_ground_truth(totals, disposition)
            _add_ground_truth(document_score, disposition)
            _add_ground_truth(category_score, disposition)

    for document_id, truth_document in truth_documents.items():
        document_score = document_scores[document_id]
        annotations = {
            _anchor(annotation): annotation for annotation in truth_document["annotations"]
        }
        consumed: set[str] = set()
        for finding in run_documents[document_id]["findings"]:
            annotation = annotations.get(_anchor(finding))
            finding_id = str(finding["id"])
            if annotation is None:
                category = str(finding["category"])
                ground_truth_id = None
                outcome = "fp"
                reason = "unmatched_prediction"
            else:
                category = str(annotation["category"])
                ground_truth_id = str(annotation["id"])
                disposition = str(annotation["disposition"])
                if disposition == "must_not_warn":
                    outcome = "fp"
                    reason = "explicit_negative"
                elif ground_truth_id in consumed:
                    outcome = "fp"
                    reason = "duplicate_prediction"
                elif not _suggestion_is_accepted(annotation, finding):
                    outcome = "fp"
                    reason = "suggestion_mismatch"
                elif disposition == "optional":
                    consumed.add(ground_truth_id)
                    outcome = "optional"
                    reason = "accepted_optional"
                else:
                    consumed.add(ground_truth_id)
                    outcome = "tp"
                    reason = "accepted_required"
            category_score = category_scores.setdefault(category, _new_score())
            confidence_band = (
                "certain"
                if float(finding["confidence"]) >= certain_confidence
                else "uncertain"
            )
            _record_prediction(
                (totals, document_score, category_score),
                confidence_scores[confidence_band],
                outcome,
                reason,
            )
            outcomes.append(
                {
                    "document_id": document_id,
                    "outcome": outcome,
                    "reason": reason,
                    "category": category,
                    "ground_truth_id": ground_truth_id,
                    "finding_id": finding_id,
                }
            )

        for annotation in truth_document["annotations"]:
            if (
                annotation["disposition"] == "required"
                and str(annotation["id"]) not in consumed
            ):
                category = str(annotation["category"])
                _record_miss((totals, document_score, category_scores[category]))
                outcomes.append(
                    {
                        "document_id": document_id,
                        "outcome": "fn",
                        "reason": "missed_required",
                        "category": category,
                        "ground_truth_id": str(annotation["id"]),
                        "finding_id": None,
                    }
                )

    report = {
        "schema_version": 1,
        "corpus_id": str(ground_truth["corpus_id"]),
        "run_id": str(run["run_id"]),
        "corpus_sha256": corpus_sha256 or _canonical_sha256(ground_truth),
        "run_sha256": run_sha256 or _canonical_sha256(run),
        "configuration": run["configuration"],
        "performance": run["performance"],
        "quality": run["quality"],
        "confidence_threshold": certain_confidence,
        "totals": _finalize_score(totals),
        "by_category": {
            category: _finalize_score(score)
            for category, score in sorted(category_scores.items())
        },
        "by_confidence": {
            band: _finalize_prediction_score(score)
            for band, score in confidence_scores.items()
        },
        "documents": [
            {"document_id": document_id, "score": _finalize_score(score)}
            for document_id, score in document_scores.items()
        ],
        "outcomes": outcomes,
    }
    return validate_report(report)


def _file_sha256(path: Path) -> str:
    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate SoatVan findings against reviewed ground truth"
    )
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--certain-confidence", type=float, default=DEFAULT_CERTAIN_CONFIDENCE
    )
    parser.add_argument("--min-precision", type=float)
    parser.add_argument("--min-recall", type=float)
    args = parser.parse_args()
    try:
        ground_truth = _load_json(args.ground_truth)
        run = _load_json(args.run)
        report = evaluate(
            ground_truth,
            run,
            certain_confidence=args.certain_confidence,
            corpus_sha256=_file_sha256(args.ground_truth),
            run_sha256=_file_sha256(args.run),
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(str(error)) from error

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    totals = report["totals"]
    if (
        args.min_precision is not None
        and float(totals["precision"]) < args.min_precision
    ) or (
        args.min_recall is not None and float(totals["recall"]) < args.min_recall
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

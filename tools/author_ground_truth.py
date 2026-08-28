from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from evaluate_findings import validate_ground_truth
from soatvan.document import DocxPackage

WORKSHEET_FIELDS = [
    "id",
    "disposition",
    "category",
    "block_id",
    "start",
    "end",
    "source_text",
    "accepted_suggestions_json",
    "note",
    "block_kind",
    "block_text",
]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def bootstrap(
    document_path: Path,
    corpus_id: str,
    document_id: str,
    draft_path: Path,
    worksheet_path: Path,
) -> dict[str, Any]:
    package = DocxPackage()
    blocks = package.read_blocks(document_path)
    draft = {
        "schema_version": 1,
        "corpus_id": corpus_id,
        "provenance": "draft",
        "documents": [
            {
                "document_id": document_id,
                "source_sha256": file_sha256(document_path),
                "annotations": [],
            }
        ],
    }
    validate_ground_truth(draft)
    _write_json(draft_path, draft)
    worksheet_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = worksheet_path.with_name(f".{worksheet_path.name}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=WORKSHEET_FIELDS)
        writer.writeheader()
        for block in blocks:
            writer.writerow(
                {
                    "id": "",
                    "disposition": "",
                    "category": "",
                    "block_id": block.id,
                    "start": "",
                    "end": "",
                    "source_text": "",
                    "accepted_suggestions_json": "",
                    "note": "",
                    "block_kind": block.kind,
                    "block_text": block.text,
                }
            )
    temporary.replace(worksheet_path)
    return {
        "document_id": document_id,
        "source_sha256": draft["documents"][0]["source_sha256"],
        "blocks": len(blocks),
        "draft": str(draft_path),
        "worksheet": str(worksheet_path),
    }


def _annotation_from_row(
    row: dict[str, str], block_text: str, row_number: int
) -> dict[str, Any]:
    annotation_id = row["id"].strip()
    disposition = row["disposition"].strip()
    category = row["category"].strip()
    source_text = row["source_text"]
    try:
        start = int(row["start"])
        end = int(row["end"])
    except ValueError as error:
        raise ValueError(f"worksheet row {row_number}: start/end must be integers") from error
    if disposition not in {"required", "optional", "must_not_warn"}:
        raise ValueError(f"worksheet row {row_number}: invalid disposition")
    if not category:
        raise ValueError(f"worksheet row {row_number}: category is required")
    if start < 0 or start >= end or end > len(block_text):
        raise ValueError(f"worksheet row {row_number}: anchor is outside block")
    if block_text[start:end] != source_text:
        raise ValueError(f"worksheet row {row_number}: source_text does not match DOCX")
    annotation: dict[str, Any] = {
        "id": annotation_id,
        "disposition": disposition,
        "category": category,
        "block_id": row["block_id"].strip(),
        "start": start,
        "end": end,
        "source_text": source_text,
    }
    suggestions_text = row["accepted_suggestions_json"].strip()
    if disposition == "must_not_warn":
        if suggestions_text:
            raise ValueError(
                f"worksheet row {row_number}: must_not_warn cannot have suggestions"
            )
    else:
        if not suggestions_text:
            raise ValueError(
                f"worksheet row {row_number}: accepted_suggestions_json is required"
            )
        try:
            suggestions = json.loads(suggestions_text)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"worksheet row {row_number}: suggestions must be JSON array or null"
            ) from error
        if suggestions is not None and (
            not isinstance(suggestions, list)
            or not suggestions
            or any(not isinstance(item, str) for item in suggestions)
        ):
            raise ValueError(
                f"worksheet row {row_number}: suggestions must be JSON array or null"
            )
        annotation["accepted_suggestions"] = suggestions
    note = row["note"].strip()
    if note:
        annotation["note"] = note
    return annotation


def finalize(
    document_path: Path,
    draft_path: Path,
    worksheet_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    draft = validate_ground_truth(json.loads(draft_path.read_text(encoding="utf-8")))
    if draft["provenance"] != "draft" or len(draft["documents"]) != 1:
        raise ValueError("finalize requires a single-document draft")
    document = draft["documents"][0]
    current_hash = file_sha256(document_path)
    if current_hash != document["source_sha256"]:
        raise ValueError("DOCX hash does not match ground-truth draft")
    blocks = {block.id: block for block in DocxPackage().read_blocks(document_path)}
    with worksheet_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != WORKSHEET_FIELDS:
            raise ValueError("worksheet header is invalid")
        annotations: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=2):
            if not row["id"].strip():
                continue
            block_id = row["block_id"].strip()
            block = blocks.get(block_id)
            if block is None:
                raise ValueError(f"worksheet row {row_number}: unknown block_id")
            annotations.append(_annotation_from_row(row, block.text, row_number))
    reviewed = {
        **draft,
        "provenance": "human_reviewed",
        "documents": [{**document, "annotations": annotations}],
    }
    validate_ground_truth(reviewed)
    _write_json(output_path, reviewed)
    counts = {name: 0 for name in ("required", "optional", "must_not_warn")}
    for annotation in annotations:
        counts[str(annotation["disposition"])] += 1
    return {
        "document_id": document["document_id"],
        "source_sha256": current_hash,
        "annotations": len(annotations),
        "counts": counts,
        "output": str(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Author reviewed ground truth for a DOCX")
    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--document", type=Path, required=True)
    bootstrap_parser.add_argument("--corpus-id", required=True)
    bootstrap_parser.add_argument("--document-id", required=True)
    bootstrap_parser.add_argument("--draft", type=Path, required=True)
    bootstrap_parser.add_argument("--worksheet", type=Path, required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--document", type=Path, required=True)
    finalize_parser.add_argument("--draft", type=Path, required=True)
    finalize_parser.add_argument("--worksheet", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            result = bootstrap(
                args.document,
                args.corpus_id,
                args.document_id,
                args.draft,
                args.worksheet,
            )
        else:
            result = finalize(args.document, args.draft, args.worksheet, args.output)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

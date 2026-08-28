from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

AUTHOR_TOOL = Path(__file__).parents[2] / "tools" / "author_ground_truth.py"
EXPORT_TOOL = Path(__file__).parents[2] / "tools" / "export_benchmark_run.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_ground_truth_bootstrap_and_finalize_revalidate_docx_anchors(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["Văn bản sát nhập  nội dung."]])
    draft = tmp_path / "draft.json"
    worksheet = tmp_path / "worksheet.csv"
    reviewed = tmp_path / "reviewed.json"

    bootstrapped = _run(
        str(AUTHOR_TOOL),
        "bootstrap",
        "--document",
        str(source),
        "--corpus-id",
        "real-corpus-v1",
        "--document-id",
        "doc-01",
        "--draft",
        str(draft),
        "--worksheet",
        str(worksheet),
    )

    assert bootstrapped.returncode == 0, bootstrapped.stderr
    draft_value = json.loads(draft.read_text(encoding="utf-8"))
    assert draft_value["provenance"] == "draft"
    assert draft_value["documents"][0]["source_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    with worksheet.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        rows = list(reader)
    assert fields is not None
    block_text = rows[0]["block_text"]
    start = block_text.index("sát nhập")
    rows[0].update(
        {
            "id": "E001",
            "disposition": "required",
            "category": "compound_word",
            "start": str(start),
            "end": str(start + len("sát nhập")),
            "source_text": "sát nhập",
            "accepted_suggestions_json": '["sáp nhập"]',
            "note": "Người duyệt xác nhận.",
        }
    )
    with worksheet.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    finalized = _run(
        str(AUTHOR_TOOL),
        "finalize",
        "--document",
        str(source),
        "--draft",
        str(draft),
        "--worksheet",
        str(worksheet),
        "--output",
        str(reviewed),
    )

    assert finalized.returncode == 0, finalized.stderr
    reviewed_value = json.loads(reviewed.read_text(encoding="utf-8"))
    assert reviewed_value["provenance"] == "human_reviewed"
    assert reviewed_value["documents"][0]["annotations"] == [
        {
            "id": "E001",
            "disposition": "required",
            "category": "compound_word",
            "block_id": "document:p0",
            "start": start,
            "end": start + len("sát nhập"),
            "source_text": "sát nhập",
            "accepted_suggestions": ["sáp nhập"],
            "note": "Người duyệt xác nhận.",
        }
    ]

    rows[0]["source_text"] = "không khớp"
    with worksheet.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    rejected = _run(
        str(AUTHOR_TOOL),
        "finalize",
        "--document",
        str(source),
        "--draft",
        str(draft),
        "--worksheet",
        str(worksheet),
        "--output",
        str(tmp_path / "rejected.json"),
    )
    assert rejected.returncode != 0
    assert "source_text does not match DOCX" in rejected.stderr


def test_rules_only_export_captures_written_findings_and_preserves_source(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["Văn bản sát nhập  xử lí."]])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    annotated = tmp_path / "annotated.docx"
    run_output = tmp_path / "run.json"

    completed = _run(
        str(EXPORT_TOOL),
        "--document",
        str(source),
        "--data-dir",
        str(tmp_path / "data"),
        "--annotated-output",
        str(annotated),
        "--output",
        str(run_output),
        "--run-id",
        "rules-baseline-v1",
        "--corpus-id",
        "corpus-v1",
        "--document-id",
        "doc-01",
        "--prompt-id",
        "no-prompt",
        "--rules-only",
    )

    assert completed.returncode == 0, completed.stderr
    run = json.loads(run_output.read_text(encoding="utf-8"))
    findings = run["documents"][0]["findings"]
    assert run["configuration"] == {
        "prompt_id": "no-prompt",
        "model_id": "rules-only",
        "model_version": "engine",
        "quantization": "none",
    }
    assert run["documents"][0]["source_sha256"] == source_hash
    assert run["quality"] == {
        "status": "complete",
        "coverage_percent": 100.0,
        "timeout_chunks": 0,
        "invalid_output_chunks": 0,
        "truncated_outputs": None,
    }
    assert {item["source_text"] for item in findings} == {"sát nhập", "  ", "xử lí"}
    assert {item["source_text"]: item["confidence"] for item in findings} == {
        "sát nhập": 0.98,
        "  ": 1.0,
        "xử lí": 0.98,
    }
    assert annotated.is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash

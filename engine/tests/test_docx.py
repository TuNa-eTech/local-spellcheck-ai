from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from soatvan.checking import Preset, RuleEngine
from soatvan.document import DocxPackage

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def test_split_run_annotation_preserves_source_and_unrelated_parts(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["Văn bản sát ", "nhập  nội dung."]])
    original = source.read_bytes()
    package = DocxPackage()
    blocks = package.read_blocks(source)
    findings = RuleEngine().check(blocks, Preset.STANDARD)
    output = tmp_path / "output.docx"

    result = package.write_annotations(source, output, findings)
    assert result.count == 2
    assert source.read_bytes() == original
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert archive.read("word/media/image1.png").startswith(b"\x89PNG")
        document = etree.fromstring(archive.read("word/document.xml"))
        comments = etree.fromstring(archive.read("word/comments.xml"))
        assert (
            "".join(document.xpath("//w:t/text()", namespaces=NS)) == "Văn bản sát nhập  nội dung."
        )
        assert len(document.xpath("//w:highlight[@w:val='yellow']", namespaces=NS)) == 3
        assert len(comments.xpath("//w:comment", namespaces=NS)) == 2


def test_no_findings_creates_no_output(make_docx, tmp_path: Path) -> None:
    source = make_docx([["Văn bản hợp lệ."]])
    output = tmp_path / "output.docx"
    assert DocxPackage().write_annotations(source, output, []).count == 0
    assert not output.exists()


def test_golden_package_preserves_unsupported_parts_and_existing_annotations(
    make_golden_docx, tmp_path: Path
) -> None:
    source = make_golden_docx()
    package = DocxPackage()
    blocks = package.read_blocks(source)
    assert any(block.kind == "table_cell" for block in blocks)
    findings = RuleEngine().check(blocks, Preset.STANDARD)
    output = tmp_path / "golden-output.docx"
    result = package.write_annotations(source, output, findings)
    assert result.count == 3

    mutable_parts = {
        "[Content_Types].xml",
        "word/document.xml",
        "word/comments.xml",
        "word/_rels/document.xml.rels",
    }
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(output) as after:
        assert set(before.namelist()) == set(after.namelist())
        for name in set(before.namelist()) - mutable_parts:
            assert after.read(name) == before.read(name), name
        document = etree.fromstring(after.read("word/document.xml"))
        comments = etree.fromstring(after.read("word/comments.xml"))
        assert document.xpath("count(//w:hyperlink[@r:id='rId3'])", namespaces={**NS, "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}) == 1
        assert document.xpath("string(//w:ins//w:t)", namespaces=NS) == "Tracked text"
        assert document.xpath("string(//w:sdtPr/w:tag/@w:val)", namespaces=NS) == "preserved"
        assert comments.xpath("string(//w:comment[@w:id='4']//w:t)", namespaces=NS) == "Existing comment"
        assert len(comments.xpath("//w:comment", namespaces=NS)) == 4
    acceptance_output = os.environ.get("SOATVAN_ACCEPTANCE_OUTPUT")
    if acceptance_output:
        destination = Path(acceptance_output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output, destination)


def test_revalidation_fails_closed(make_docx, tmp_path: Path) -> None:
    source = make_docx([["sát nhập"]])
    finding = RuleEngine().check(DocxPackage().read_blocks(source), Preset.STANDARD)[0]
    tampered = finding.__class__(**{**finding.as_dict(), "source_text": "không khớp"})
    output = tmp_path / "output.docx"
    assert DocxPackage().write_annotations(source, output, [tampered]).count == 0
    assert not output.exists()


def test_cancel_during_export_leaves_source_and_target_unchanged(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["sát nhập  xử lí"]])
    original = source.read_bytes()
    findings = RuleEngine().check(DocxPackage().read_blocks(source), Preset.STANDARD)
    output = tmp_path / "cancelled.docx"

    class CancelDuringWrite:
        calls = 0

        def raise_if_cancelled(self) -> None:
            self.calls += 1
            if self.calls >= 3:
                raise RuntimeError("JOB_CANCELLED")

    with pytest.raises(RuntimeError, match="JOB_CANCELLED"):
        DocxPackage().write_annotations(source, output, findings, CancelDuringWrite())
    assert source.read_bytes() == original
    assert not output.exists()


def test_adapter_refuses_source_as_output(make_docx) -> None:
    source = make_docx([["sát nhập"]])
    original = source.read_bytes()
    findings = RuleEngine().check(DocxPackage().read_blocks(source), Preset.STANDARD)
    with pytest.raises(ValueError, match="OUTPUT_SOURCE_CONFLICT"):
        DocxPackage().write_annotations(source, source, findings)
    assert source.read_bytes() == original


def test_atomic_replace_failure_preserves_existing_target(
    make_docx, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = make_docx([["sát nhập"]])
    output = tmp_path / "existing.docx"
    output.write_bytes(b"existing")
    findings = RuleEngine().check(DocxPackage().read_blocks(source), Preset.STANDARD)

    def fail_replace(stage: Path, target: Path) -> None:
        del stage, target
        raise OSError("disk full")

    monkeypatch.setattr("soatvan.document.ooxml.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        DocxPackage().write_annotations(source, output, findings)
    assert output.read_bytes() == b"existing"

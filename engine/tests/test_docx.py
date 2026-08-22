from __future__ import annotations

import zipfile
from pathlib import Path

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

    assert package.write_annotations(source, output, findings) == 2
    assert source.read_bytes() == original
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert archive.read("word/media/image1.png") == b"preserved-image"
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
    assert DocxPackage().write_annotations(source, output, []) == 0
    assert not output.exists()


def test_revalidation_fails_closed(make_docx, tmp_path: Path) -> None:
    source = make_docx([["sát nhập"]])
    finding = RuleEngine().check(DocxPackage().read_blocks(source), Preset.STANDARD)[0]
    tampered = finding.__class__(**{**finding.as_dict(), "source_text": "không khớp"})
    output = tmp_path / "output.docx"
    assert DocxPackage().write_annotations(source, output, [tampered]) == 0
    assert not output.exists()

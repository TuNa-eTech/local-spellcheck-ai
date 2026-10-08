from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from lxml import etree

from soatvan.checking import Preset, RuleEngine
from soatvan.checking.domain import Finding
from soatvan.document import DocxPackage

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def test_inspect_reports_word_count_and_best_effort_saved_page_count(make_docx) -> None:
    source = make_docx([["Một văn bản 2026."], ["Nghiên cứu tốt."]])
    app_properties = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">
  <Pages>7</Pages>
</Properties>"""
    with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("docProps/app.xml", app_properties)
    metadata = DocxPackage().inspect(source)
    assert metadata["word_count"] == 7
    assert metadata["page_count"] == 7


def test_inspect_omits_unavailable_or_invalid_saved_page_count(make_docx) -> None:
    source = make_docx([["Văn bản hợp lệ."]])
    assert DocxPackage().inspect(source)["page_count"] is None


def test_inspect_counts_table_cells_not_paragraphs_inside_cells(make_docx) -> None:
    source = make_docx([["placeholder"]])
    with zipfile.ZipFile(source) as archive:
        parts = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    document_xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Outside table</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc>
      <w:p><w:r><w:t>First cell paragraph</w:t></w:r></w:p>
      <w:p><w:r><w:t>Second cell paragraph</w:t></w:r></w:p>
    </w:tc></w:tr></w:tbl>
    <w:sectPr/>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(source, "w") as archive:
        for name, (info, data) in parts.items():
            archive.writestr(info, document_xml if name == "word/document.xml" else data)

    package = DocxPackage()
    metadata = package.inspect(source)

    assert metadata["table_cell_count"] == 1
    assert metadata["paragraph_count"] == 1
    assert sum(block.kind == "table_cell" for block in package.read_blocks(source)) == 2
    with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "docProps/app.xml",
            b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Pages>stale</Pages></Properties>',
        )
    assert DocxPackage().inspect(source)["page_count"] is None


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
        assert len(document.xpath("//w:highlight[@w:val='red']", namespaces=NS)) == 3
        assert len(comments.xpath("//w:comment", namespaces=NS)) == 2


def test_highlight_color_reflects_finding_confidence(make_docx, tmp_path: Path) -> None:
    source = make_docx([["sát nhập và xử lí"]])
    original = source.read_bytes()
    package = DocxPackage()
    findings = RuleEngine().check(package.read_blocks(source), Preset.STANDARD)
    certain = replace(
        next(item for item in findings if item.source_text == "sát nhập"),
        confidence=0.9,
    )
    uncertain = replace(
        next(item for item in findings if item.source_text == "xử lí"),
        confidence=0.89,
    )
    output = tmp_path / "confidence-colors.docx"

    assert package.write_annotations(source, output, [certain, uncertain]).count == 2
    assert source.read_bytes() == original
    with zipfile.ZipFile(output) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
    highlighted = {
        run.xpath("string(./w:t)", namespaces=NS): run.xpath(
            "string(./w:rPr/w:highlight/@w:val)", namespaces=NS
        )
        for run in document.xpath("//w:r[w:rPr/w:highlight]", namespaces=NS)
    }
    assert highlighted == {"sát nhập": "red", "xử lí": "yellow"}


def test_flat_finding_is_annotated_in_a_paragraph_with_nested_ooxml(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["placeholder"]])
    with zipfile.ZipFile(source) as archive:
        parts = {info.filename: (info, archive.read(info.filename)) for info in archive.infolist()}
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t xml:space="preserve">s\u00e1t nh\u1eadp </w:t></w:r>
      <w:hyperlink><w:r><w:t xml:space="preserve">li\u00ean k\u1ebft </w:t></w:r></w:hyperlink>
      <w:ins w:id="1"><w:r><w:t xml:space="preserve">theo d\u00f5i </w:t></w:r></w:ins>
      <w:sdt><w:sdtPr/><w:sdtContent><w:r><w:t>n\u1ed9i dung</w:t></w:r></w:sdtContent></w:sdt>
    </w:p>
    <w:sectPr/>
  </w:body>
</w:document>""".encode("utf-8")
    with zipfile.ZipFile(source, "w") as archive:
        for name, (info, data) in parts.items():
            archive.writestr(info, document_xml if name == "word/document.xml" else data)

    package = DocxPackage()
    findings = RuleEngine().check(package.read_blocks(source), Preset.STANDARD)
    finding = next(item for item in findings if item.source_text == "s\u00e1t nh\u1eadp")
    output = tmp_path / "mixed-output.docx"

    result = package.write_annotations(source, output, [finding])

    assert result.written_ids == (finding.id,)
    with zipfile.ZipFile(output) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
        comments = etree.fromstring(archive.read("word/comments.xml"))
    assert document.xpath("string(//w:hyperlink//w:t)", namespaces=NS) == "li\u00ean k\u1ebft "
    assert document.xpath("string(//w:ins//w:t)", namespaces=NS) == "theo d\u00f5i "
    assert document.xpath("string(//w:sdt//w:t)", namespaces=NS) == "n\u1ed9i dung"
    assert len(document.xpath("//w:highlight[@w:val='red']", namespaces=NS)) == 1
    assert len(comments.xpath("//w:comment", namespaces=NS)) == 1

    crossing_text = "s\u00e1t nh\u1eadp li\u00ean"
    crossing = replace(
        finding,
        id="crossing-nested-anchor",
        end=len(crossing_text),
        source_text=crossing_text,
    )
    rejected_output = tmp_path / "crossing-output.docx"
    assert package.write_annotations(source, rejected_output, [crossing]).count == 0
    assert not rejected_output.exists()


def test_break_and_tab_projection_keeps_rule_and_writer_offsets_aligned(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["placeholder"]])
    with zipfile.ZipFile(source) as archive:
        parts = {
            info.filename: (info, archive.read(info.filename))
            for info in archive.infolist()
        }
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t>Trước</w:t><w:br/></w:r>
      <w:r><w:t>sát nhập</w:t></w:r>
      <w:r><w:tab/></w:r>
      <w:r><w:t>cuối</w:t></w:r>
    </w:p>
    <w:sectPr/>
  </w:body>
</w:document>""".encode()
    with zipfile.ZipFile(source, "w") as archive:
        for name, (info, data) in parts.items():
            archive.writestr(info, document_xml if name == "word/document.xml" else data)

    package = DocxPackage()
    blocks = package.read_blocks(source)
    assert [block.text for block in blocks] == ["Trước\nsát nhập\tcuối"]
    prefix = replace(
        RuleEngine().check(blocks, Preset.STANDARD)[0],
        id="prefix-before-break",
        start=0,
        end=len("Trước"),
        source_text="Trước",
        suggestion="Trước đây",
    )
    prefix_output = tmp_path / "prefix-before-break.docx"
    assert package.write_annotations(source, prefix_output, [prefix]).written_ids == (
        prefix.id,
    )
    assert package.read_blocks(prefix_output)[0].text == blocks[0].text

    finding = RuleEngine().check(blocks, Preset.STANDARD)[0]
    assert (finding.start, finding.source_text) == (len("Trước\n"), "sát nhập")

    output = tmp_path / "break-tab-output.docx"
    assert package.write_annotations(source, output, [finding]).written_ids == (finding.id,)
    assert package.read_blocks(output)[0].text == blocks[0].text

    crossing = replace(
        finding,
        id="crossing-break",
        start=0,
        end=len("Trước\nsát"),
        source_text="Trước\nsát",
    )
    rejected_output = tmp_path / "crossing-break.docx"
    assert package.write_annotations(source, rejected_output, [crossing]).count == 0
    assert not rejected_output.exists()


def test_generated_comment_shows_original_and_suggestion_without_double_periods(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["sát nhập"]])
    package = DocxPackage()
    finding = RuleEngine().check(package.read_blocks(source), Preset.STANDARD)[0]
    finding = replace(finding, suggestion="sáp nhập.", reason="Lý do thử nghiệm..")
    output = tmp_path / "comment-output.docx"

    assert package.write_annotations(source, output, [finding]).count == 1
    with zipfile.ZipFile(output) as archive:
        comments = etree.fromstring(archive.read("word/comments.xml"))
    comment = comments.xpath("string(//w:comment//w:t)", namespaces=NS)
    assert comment == "Sai: “sát nhập” → Đề xuất: “sáp nhập.”"


def test_generated_comment_for_deletion_finding_asks_to_remove_marked_text(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["Đây là và và một ví dụ."]])
    package = DocxPackage()
    finding = next(
        f
        for f in RuleEngine().check(package.read_blocks(source), Preset.STANDARD)
        if f.detector_id == "word.repeated.v2"
    )
    assert finding.suggestion == "" and finding.source_text == " và"
    output = tmp_path / "deletion-comment.docx"

    assert package.write_annotations(source, output, [finding]).count == 1
    with zipfile.ZipFile(output) as archive:
        comments = etree.fromstring(archive.read("word/comments.xml"))
    comment = comments.xpath("string(//w:comment//w:t)", namespaces=NS)
    assert comment == "Sai: “và” → Đề xuất: xoá phần được bôi màu"
    assert "(xoá)" not in comment


def test_comment_text_for_leading_space_punctuation_preserves_space(tmp_path: Path) -> None:
    from soatvan.checking.domain import Finding
    from soatvan.document.ooxml import _comment_text

    finding = Finding(
        id="test:1",
        category="technical",
        origin="rule",
        detector_id="punctuation.leading_space.v2",
        block_id="document:p0",
        start=3,
        end=5,
        source_text=" :",
        suggestion=":",
        reason="Không đặt khoảng trắng trước dấu câu.",
        rule_version="1.0",
    )
    comment = _comment_text(finding)
    assert "Đề xuất: “:”" in comment
    assert comment != "Sai: “:” → Đề xuất: “:”"


def test_comment_text_for_unknown_word_does_not_ask_to_delete() -> None:
    from soatvan.checking.domain import Finding
    from soatvan.document.ooxml import _comment_text

    finding = Finding(
        id="test:2",
        category="spelling",
        origin="rule",
        detector_id="dictionary.unknown.v1",
        block_id="document:p0",
        start=0,
        end=4,
        source_text="Ngọk",
        suggestion="",
        reason="Từ “Ngọk” không có trong từ điển tiếng Việt.",
        rule_version="1.0",
    )
    comment = _comment_text(finding)
    assert "xoá phần được bôi màu" not in comment
    assert "chưa có trong từ điển" in comment


def test_docx_annotates_runs_with_line_breaks_and_multiple_text_nodes(make_golden_docx, tmp_path: Path) -> None:
    source = make_golden_docx()
    with zipfile.ZipFile(source, "r") as zin:
        names = zin.namelist()
        data = {name: zin.read(name) for name in names}

    p_xml = (
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:r><w:t>Phát triển kinh tế.</w:t><w:br/>'
        '<w:t>Lỗi sát nhập cần sửa.</w:t></w:r></w:p>'
    ).encode("utf-8")
    data["word/document.xml"] = data["word/document.xml"].replace(b"</w:body>", p_xml + b"</w:body>")

    test_source = tmp_path / "multi-run-source.docx"
    with zipfile.ZipFile(test_source, "w") as zout:
        for name, d in data.items():
            zout.writestr(name, d)

    package = DocxPackage()
    blocks = package.read_blocks(test_source)
    last_block = blocks[-1]
    idx = last_block.text.index("sát nhập")
    finding = Finding(
        id=f"{last_block.id}:{idx}:{idx+8}:test",
        category="word_choice",
        origin="rule",
        detector_id="test.detector",
        block_id=last_block.id,
        start=idx,
        end=idx + 8,
        source_text="sát nhập",
        suggestion="sáp nhập",
        reason="Từ đúng là sáp nhập",
        rule_version="1.0",
    )
    output = tmp_path / "compound-run-output.docx"
    result = package.write_annotations(test_source, output, [finding])
    assert result.count == 1
    assert result.written_ids == (finding.id,)


def test_docx_annotates_hyperlinks(make_golden_docx, tmp_path: Path) -> None:
    source = make_golden_docx()
    with zipfile.ZipFile(source, "r") as zin:
        names = zin.namelist()
        data = {name: zin.read(name) for name in names}

    p_xml = (
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:r><w:t>Xem tài liệu </w:t></w:r>'
        '<w:hyperlink r:id="rId1"><w:r><w:t>sát nhập</w:t></w:r></w:hyperlink>'
        '<w:r><w:t> tại đây.</w:t></w:r></w:p>'
    ).encode("utf-8")
    data["word/document.xml"] = data["word/document.xml"].replace(b"</w:body>", p_xml + b"</w:body>")

    test_source = tmp_path / "hyperlink-source.docx"
    with zipfile.ZipFile(test_source, "w") as zout:
        for name, d in data.items():
            zout.writestr(name, d)

    package = DocxPackage()
    blocks = package.read_blocks(test_source)
    last_block = blocks[-1]
    idx = last_block.text.index("sát nhập")
    finding = Finding(
        id=f"{last_block.id}:{idx}:{idx+8}:hl",
        category="word_choice",
        origin="rule",
        detector_id="test.hl",
        block_id=last_block.id,
        start=idx,
        end=idx + 8,
        source_text="sát nhập",
        suggestion="sáp nhập",
        reason="test",
        rule_version="1.0",
    )
    output = tmp_path / "hyperlink-output.docx"
    result = package.write_annotations(test_source, output, [finding])
    assert result.count == 1
    assert result.written_ids == (finding.id,)


def test_docx_annotates_tracked_insertions_and_content_controls(make_golden_docx, tmp_path: Path) -> None:
    source = make_golden_docx()
    with zipfile.ZipFile(source, "r") as zin:
        names = zin.namelist()
        data = {name: zin.read(name) for name in names}

    p_xml = (
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:ins w:id="1" w:author="user"><w:r><w:t>sát nhập</w:t></w:r></w:ins>'
        '<w:sdt><w:sdtContent><w:r><w:t>kế họach</w:t></w:r></w:sdtContent></w:sdt>'
        '</w:p>'
    ).encode("utf-8")
    data["word/document.xml"] = data["word/document.xml"].replace(b"</w:body>", p_xml + b"</w:body>")

    test_source = tmp_path / "ins-sdt-source.docx"
    with zipfile.ZipFile(test_source, "w") as zout:
        for name, d in data.items():
            zout.writestr(name, d)

    package = DocxPackage()
    blocks = package.read_blocks(test_source)
    last_block = blocks[-1]
    idx1 = last_block.text.index("sát nhập")
    f1 = Finding(
        id=f"{last_block.id}:{idx1}:{idx1+8}:ins",
        category="word_choice",
        origin="rule",
        detector_id="test.ins",
        block_id=last_block.id,
        start=idx1,
        end=idx1 + 8,
        source_text="sát nhập",
        suggestion="sáp nhập",
        reason="test",
        rule_version="1.0",
    )
    idx2 = last_block.text.index("kế họach")
    f2 = Finding(
        id=f"{last_block.id}:{idx2}:{idx2+8}:sdt",
        category="spelling",
        origin="rule",
        detector_id="test.sdt",
        block_id=last_block.id,
        start=idx2,
        end=idx2 + 8,
        source_text="kế họach",
        suggestion="kế hoạch",
        reason="test",
        rule_version="1.0",
    )
    output = tmp_path / "ins-sdt-output.docx"
    result = package.write_annotations(test_source, output, [f1, f2])
    assert result.count == 2


def test_docx_annotates_runs_with_footnote_references(make_golden_docx, tmp_path: Path) -> None:
    source = make_golden_docx()
    with zipfile.ZipFile(source, "r") as zin:
        names = zin.namelist()
        data = {name: zin.read(name) for name in names}

    p_xml = (
        '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:r><w:t>kinh tế sát nhập</w:t><w:footnoteReference w:id="1"/></w:r>'
        '</w:p>'
    ).encode("utf-8")
    data["word/document.xml"] = data["word/document.xml"].replace(b"</w:body>", p_xml + b"</w:body>")

    test_source = tmp_path / "footnote-run-source.docx"
    with zipfile.ZipFile(test_source, "w") as zout:
        for name, d in data.items():
            zout.writestr(name, d)

    package = DocxPackage()
    blocks = package.read_blocks(test_source)
    last_block = blocks[-1]
    idx = last_block.text.index("sát nhập")
    f = Finding(
        id=f"{last_block.id}:{idx}:{idx+8}:fn",
        category="word_choice",
        origin="rule",
        detector_id="test.fn",
        block_id=last_block.id,
        start=idx,
        end=idx + 8,
        source_text="sát nhập",
        suggestion="sáp nhập",
        reason="test",
        rule_version="1.0",
    )
    output = tmp_path / "footnote-run-output.docx"
    result = package.write_annotations(test_source, output, [f])
    assert result.count == 1


def test_comment_text_sanitizes_illegal_xml_control_characters() -> None:
    from soatvan.document.ooxml import _comment_text

    finding = Finding(
        id="test:ctl",
        category="spelling",
        origin="rule",
        detector_id="test",
        block_id="document:p0",
        start=0,
        end=5,
        source_text="từ\x00\x08lạ\x1b",
        suggestion="từ\x0cđúng",
        reason="lý\x0bdo",
        rule_version="1.0",
    )
    comment = _comment_text(finding)
    assert "\x00" not in comment
    assert "\x08" not in comment
    assert "\x1b" not in comment
    assert "\x0c" not in comment
    assert "từlạ" in comment
    assert "từđúng" in comment



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

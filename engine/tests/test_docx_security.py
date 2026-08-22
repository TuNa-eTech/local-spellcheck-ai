from __future__ import annotations

import warnings
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from soatvan.checking import Preset, RuleEngine
from soatvan.document import DocxPackage, InvalidDocument

REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _rewrite(source: Path, target: Path, replacements: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(
        target, "w", zipfile.ZIP_DEFLATED
    ) as output:
        for info in archive.infolist():
            output.writestr(info, replacements.get(info.filename, archive.read(info.filename)))
        for name, value in replacements.items():
            if name not in archive.namelist():
                output.writestr(name, value)
    return target


@pytest.mark.parametrize("unsafe_name", ["../secret.xml", "word\\evil.xml", "C:/evil.xml"])
def test_rejects_unsafe_zip_paths(make_docx, tmp_path: Path, unsafe_name: str) -> None:
    source = make_docx([["Nội dung"]])
    target = tmp_path / "unsafe.docx"
    _rewrite(source, target, {unsafe_name: b"unsafe"})
    with pytest.raises(InvalidDocument, match="DOCUMENT_UNSAFE_ZIP_ENTRY"):
        DocxPackage().inspect(target)


def test_rejects_duplicate_zip_entries(make_docx, tmp_path: Path) -> None:
    source = make_docx([["Nội dung"]])
    target = tmp_path / "duplicate.docx"
    with zipfile.ZipFile(source) as archive, zipfile.ZipFile(target, "w") as output:
        for info in archive.infolist():
            output.writestr(info, archive.read(info.filename))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            output.writestr("word/document.xml", archive.read("word/document.xml"))
    with pytest.raises(InvalidDocument, match="DOCUMENT_UNSAFE_ZIP_ENTRY"):
        DocxPackage().inspect(target)


def test_rejects_high_compression_ratio(make_docx, tmp_path: Path) -> None:
    source = make_docx([["Nội dung"]])
    target = tmp_path / "bomb.docx"
    _rewrite(source, target, {"word/media/bomb.bin": b"0" * (1024 * 1024)})
    with pytest.raises(InvalidDocument, match="DOCUMENT_ARCHIVE_LIMIT"):
        DocxPackage().inspect(target)


def test_rejects_malformed_or_entity_xml(make_docx, tmp_path: Path) -> None:
    source = make_docx([["Nội dung"]])
    malformed = _rewrite(
        source,
        tmp_path / "malformed.docx",
        {"word/document.xml": b"<w:document>"},
    )
    with pytest.raises(InvalidDocument, match="DOCUMENT_INVALID_PACKAGE"):
        DocxPackage().inspect(malformed)

    entity = _rewrite(
        source,
        tmp_path / "entity.docx",
        {
            "word/document.xml": (
                b'<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                b'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
                b"&leak;</w:t></w:r></w:p></w:body></w:document>"
            )
        },
    )
    with pytest.raises(InvalidDocument, match="DOCUMENT_INVALID_PACKAGE"):
        DocxPackage().inspect(entity)


def test_external_relationship_is_never_followed_and_is_preserved(
    make_docx, tmp_path: Path
) -> None:
    source = make_docx([["sát nhập"]])
    with zipfile.ZipFile(source) as archive:
        rels = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
    relationship = etree.SubElement(rels, f"{{{REL}}}Relationship")
    relationship.set("Id", "rIdExternal")
    relationship.set(
        "Type",
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
    )
    relationship.set("Target", "https://invalid.example.test/never-requested")
    relationship.set("TargetMode", "External")
    source = _rewrite(
        source,
        tmp_path / "external.docx",
        {"word/_rels/document.xml.rels": etree.tostring(rels)},
    )
    package = DocxPackage()
    findings = RuleEngine().check(package.read_blocks(source), Preset.STANDARD)
    output = tmp_path / "external-output.docx"
    assert package.write_annotations(source, output, findings).count == 1
    with zipfile.ZipFile(output) as archive:
        output_rels = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
    saved = output_rels.xpath(
        "./rel:Relationship[@Id='rIdExternal']", namespaces={"rel": REL}
    )[0]
    assert saved.get("Target") == "https://invalid.example.test/never-requested"
    assert saved.get("TargetMode") == "External"


def test_archive_size_limit_is_enforced_before_opening(
    make_docx, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_docx([["Nội dung"]])
    monkeypatch.setattr("soatvan.document.ooxml.MAX_ARCHIVE_BYTES", source.stat().st_size - 1)
    with pytest.raises(InvalidDocument, match="DOCUMENT_ARCHIVE_LIMIT"):
        DocxPackage().inspect(source)

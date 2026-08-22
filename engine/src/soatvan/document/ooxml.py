from __future__ import annotations

import copy
import os
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from lxml import etree

from soatvan.checking.domain import Block, Finding

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R}
MAX_ENTRIES = 10_000
MAX_UNCOMPRESSED = 256 * 1024 * 1024
MAX_RATIO = 200


class InvalidDocument(ValueError):
    pass


class DocxPackage:
    """OOXML adapter. Only document.xml and comment relationship parts are changed."""

    def inspect(self, source: Path) -> dict[str, object]:
        self._validate_archive(source)
        blocks = self.read_blocks(source)
        return {
            "name": source.name,
            "size": source.stat().st_size,
            "paragraph_count": sum(block.kind == "paragraph" for block in blocks),
            "table_cell_count": sum(block.kind == "table_cell" for block in blocks),
            "character_count": sum(len(block.text) for block in blocks),
        }

    def read_blocks(self, source: Path) -> list[Block]:
        self._validate_archive(source)
        with zipfile.ZipFile(source) as archive:
            root = etree.fromstring(archive.read("word/document.xml"), parser=self._parser())
        blocks: list[Block] = []
        for index, paragraph in enumerate(root.xpath("//w:body//w:p", namespaces=NS)):
            text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))
            if text:
                in_table = bool(paragraph.xpath("ancestor::w:tc", namespaces=NS))
                blocks.append(
                    Block(f"document:p{index}", text, "table_cell" if in_table else "paragraph")
                )
        return blocks

    def write_annotations(self, source: Path, target: Path, findings: Iterable[Finding]) -> int:
        self._validate_archive(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as folder:
            stage = Path(folder) / "annotated.docx"
            written = self._rewrite(source, stage, list(findings))
            if written:
                with zipfile.ZipFile(stage) as check:
                    check.testzip()
                    etree.fromstring(check.read("word/document.xml"), parser=self._parser())
                os.replace(stage, target)
            else:
                target.unlink(missing_ok=True)
            return written

    def _rewrite(self, source: Path, target: Path, findings: list[Finding]) -> int:
        with zipfile.ZipFile(source) as archive:
            document = etree.fromstring(archive.read("word/document.xml"), parser=self._parser())
            comments = self._load_comments(archive)
            rels = self._load_rels(archive)
            content_types = etree.fromstring(
                archive.read("[Content_Types].xml"), parser=self._parser()
            )
            replacements: dict[str, bytes] = {}
            written = self._annotate(document, comments, findings)
            if not written:
                return 0
            self._ensure_comment_relationship(rels)
            self._ensure_comment_content_type(content_types)
            replacements["word/document.xml"] = self._xml(document)
            replacements["word/comments.xml"] = self._xml(comments)
            replacements["word/_rels/document.xml.rels"] = self._xml(rels)
            replacements["[Content_Types].xml"] = self._xml(content_types)
            with zipfile.ZipFile(target, "w") as output:
                for info in archive.infolist():
                    if info.filename not in replacements:
                        output.writestr(info, archive.read(info.filename))
                for name, data in replacements.items():
                    output.writestr(name, data)
        return written

    def _annotate(
        self, document: etree._Element, comments: etree._Element, findings: list[Finding]
    ) -> int:
        paragraphs = document.xpath("//w:body//w:p", namespaces=NS)
        grouped: dict[int, list[Finding]] = {}
        for finding in findings:
            try:
                index = int(finding.block_id.rsplit("p", 1)[1])
            except (IndexError, ValueError):
                continue
            grouped.setdefault(index, []).append(finding)
        next_id = 0
        existing = comments.xpath("./w:comment/@w:id", namespaces=NS)
        if existing:
            next_id = max(int(value) for value in existing) + 1
        written = 0
        for index, items in grouped.items():
            if index >= len(paragraphs):
                continue
            paragraph = paragraphs[index]
            for finding in sorted(items, key=lambda item: item.start, reverse=True):
                if self._annotate_one(paragraph, comments, finding, next_id):
                    next_id += 1
                    written += 1
        return written

    def _annotate_one(
        self, paragraph: etree._Element, comments: etree._Element, finding: Finding, comment_id: int
    ) -> bool:
        runs = paragraph.xpath("./w:r", namespaces=NS)
        spans: list[tuple[etree._Element, int, int, int, int]] = []
        cursor = 0
        for run in runs:
            texts = run.xpath("./w:t", namespaces=NS)
            if len(texts) != 1:
                cursor += sum(len(text.text or "") for text in texts)
                continue
            value = texts[0].text or ""
            spans.append((run, cursor, cursor + len(value), 0, len(value)))
            cursor += len(value)
        full_text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))
        if (
            finding.end > len(full_text)
            or full_text[finding.start : finding.end] != finding.source_text
        ):
            return False
        selected: list[etree._Element] = []
        for run, global_start, global_end, _, _ in spans:
            left = max(finding.start, global_start)
            right = min(finding.end, global_end)
            if left >= right:
                continue
            selected.append(self._isolate_run(run, left - global_start, right - global_start))
        if not selected:
            return False
        for run in selected:
            props = run.find(f"{{{W}}}rPr")
            if props is None:
                props = etree.Element(f"{{{W}}}rPr")
                run.insert(0, props)
            highlight = props.find(f"{{{W}}}highlight")
            if highlight is None:
                highlight = etree.SubElement(props, f"{{{W}}}highlight")
            highlight.set(f"{{{W}}}val", "yellow")
        first, last = selected[0], selected[-1]
        start_marker = etree.Element(f"{{{W}}}commentRangeStart")
        start_marker.set(f"{{{W}}}id", str(comment_id))
        first.addprevious(start_marker)
        end_marker = etree.Element(f"{{{W}}}commentRangeEnd")
        end_marker.set(f"{{{W}}}id", str(comment_id))
        last.addnext(end_marker)
        reference_run = etree.Element(f"{{{W}}}r")
        reference_props = etree.SubElement(reference_run, f"{{{W}}}rPr")
        etree.SubElement(reference_props, f"{{{W}}}rStyle").set(f"{{{W}}}val", "CommentReference")
        etree.SubElement(reference_run, f"{{{W}}}commentReference").set(
            f"{{{W}}}id", str(comment_id)
        )
        end_marker.addnext(reference_run)
        comment = etree.SubElement(comments, f"{{{W}}}comment")
        comment.set(f"{{{W}}}id", str(comment_id))
        comment.set(f"{{{W}}}author", "SoátVăn")
        p = etree.SubElement(comment, f"{{{W}}}p")
        r = etree.SubElement(p, f"{{{W}}}r")
        text = etree.SubElement(r, f"{{{W}}}t")
        text.text = (
            f"Gợi ý: {finding.suggestion or '(xoá)'}. {finding.reason} [{finding.rule_version}]"
        )
        return True

    @staticmethod
    def _isolate_run(run: etree._Element, start: int, end: int) -> etree._Element:
        text = run.find(f"{{{W}}}t")
        assert text is not None
        value = text.text or ""
        parent = run.getparent()
        assert parent is not None
        index = parent.index(run)
        pieces: list[tuple[str, bool]] = []
        if start:
            pieces.append((value[:start], False))
        pieces.append((value[start:end], True))
        if end < len(value):
            pieces.append((value[end:], False))
        target: etree._Element | None = None
        parent.remove(run)
        for offset, (piece, selected) in enumerate(pieces):
            clone = copy.deepcopy(run)
            clone_text = clone.find(f"{{{W}}}t")
            assert clone_text is not None
            clone_text.text = piece
            if piece.startswith(" ") or piece.endswith(" "):
                clone_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            parent.insert(index + offset, clone)
            if selected:
                target = clone
        assert target is not None
        return target

    def _validate_archive(self, source: Path) -> None:
        if source.suffix.lower() != ".docx" or not source.is_file():
            raise InvalidDocument("DOCUMENT_INVALID_TYPE")
        try:
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ENTRIES or "word/document.xml" not in archive.namelist():
                    raise InvalidDocument("DOCUMENT_INVALID_PACKAGE")
                seen: set[str] = set()
                total = 0
                for info in infos:
                    name = PurePosixPath(info.filename)
                    if info.filename in seen or name.is_absolute() or ".." in name.parts:
                        raise InvalidDocument("DOCUMENT_UNSAFE_ZIP_ENTRY")
                    seen.add(info.filename)
                    total += info.file_size
                    if total > MAX_UNCOMPRESSED or (
                        info.compress_size and info.file_size / info.compress_size > MAX_RATIO
                    ):
                        raise InvalidDocument("DOCUMENT_ARCHIVE_LIMIT")
                etree.fromstring(archive.read("word/document.xml"), parser=self._parser())
        except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as error:
            raise InvalidDocument("DOCUMENT_INVALID_PACKAGE") from error

    @staticmethod
    def _parser() -> etree.XMLParser:
        return etree.XMLParser(
            resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=False
        )

    def _load_comments(self, archive: zipfile.ZipFile) -> etree._Element:
        if "word/comments.xml" in archive.namelist():
            return etree.fromstring(archive.read("word/comments.xml"), parser=self._parser())
        return etree.Element(f"{{{W}}}comments", nsmap={"w": W})

    def _load_rels(self, archive: zipfile.ZipFile) -> etree._Element:
        name = "word/_rels/document.xml.rels"
        if name in archive.namelist():
            return etree.fromstring(archive.read(name), parser=self._parser())
        return etree.Element(f"{{{REL}}}Relationships", nsmap={None: REL})

    @staticmethod
    def _ensure_comment_relationship(rels: etree._Element) -> None:
        relationship_type = (
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
        )
        if rels.xpath(
            "./rel:Relationship[@Type=$kind]", namespaces={"rel": REL}, kind=relationship_type
        ):
            return
        used = {node.get("Id") for node in rels}
        number = 1
        while f"rId{number}" in used:
            number += 1
        node = etree.SubElement(rels, f"{{{REL}}}Relationship")
        node.set("Id", f"rId{number}")
        node.set("Type", relationship_type)
        node.set("Target", "comments.xml")

    @staticmethod
    def _ensure_comment_content_type(types: etree._Element) -> None:
        part = "/word/comments.xml"
        if types.xpath("./ct:Override[@PartName=$part]", namespaces={"ct": CT}, part=part):
            return
        node = etree.SubElement(types, f"{{{CT}}}Override")
        node.set("PartName", part)
        node.set(
            "ContentType",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        )

    @staticmethod
    def _xml(root: etree._Element) -> bytes:
        serialized: bytes = etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        return serialized

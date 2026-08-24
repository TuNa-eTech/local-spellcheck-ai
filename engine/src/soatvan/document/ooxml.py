from __future__ import annotations

import copy
import os
import re
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from lxml import etree

from soatvan.checking.domain import Block, Finding
from soatvan.workflow.ports import AnnotationResult, CancellationToken

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R}
MAX_ENTRIES = 10_000
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNCOMPRESSED = 256 * 1024 * 1024
MAX_RATIO = 200
REQUIRED_PARTS = frozenset({"[Content_Types].xml", "_rels/.rels", "word/document.xml"})


class InvalidDocument(ValueError):
    pass


class DocxPackage:
    """OOXML adapter. Only document.xml and comment relationship parts are changed."""

    def inspect(self, source: Path) -> dict[str, object]:
        self._validate_archive(source)
        blocks = self.read_blocks(source)
        text = "\n".join(block.text for block in blocks)
        return {
            "name": source.name,
            "size": source.stat().st_size,
            "paragraph_count": sum(block.kind == "paragraph" for block in blocks),
            "table_cell_count": sum(block.kind == "table_cell" for block in blocks),
            "character_count": sum(len(block.text) for block in blocks),
            "word_count": len(re.findall(r"[^\W_]+", text, flags=re.UNICODE)),
            "page_count": self._page_count(source),
        }

    def _page_count(self, source: Path) -> int | None:
        """Return Word's last-saved page count when present; it is best-effort metadata."""
        try:
            with zipfile.ZipFile(source) as archive:
                if "docProps/app.xml" not in archive.namelist():
                    return None
                root = self._parse_xml(archive.read("docProps/app.xml"))
            value = root.findtext(
                "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Pages"
            )
            pages = int(value) if value is not None else 0
            return pages if pages > 0 else None
        except (InvalidDocument, KeyError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError):
            return None

    def read_blocks(self, source: Path) -> list[Block]:
        self._validate_archive(source)
        with zipfile.ZipFile(source) as archive:
            root = self._parse_xml(archive.read("word/document.xml"))
        blocks: list[Block] = []
        for index, paragraph in enumerate(root.xpath("//w:body//w:p", namespaces=NS)):
            text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))
            if text:
                in_table = bool(paragraph.xpath("ancestor::w:tc", namespaces=NS))
                blocks.append(
                    Block(f"document:p{index}", text, "table_cell" if in_table else "paragraph")
                )
        return blocks

    def write_annotations(
        self,
        source: Path,
        target: Path,
        findings: Iterable[Finding],
        cancellation: CancellationToken | None = None,
    ) -> AnnotationResult:
        self._validate_archive(source)
        if source.resolve() == target.resolve():
            raise InvalidDocument("OUTPUT_SOURCE_CONFLICT")
        if cancellation:
            cancellation.raise_if_cancelled()
        target_existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as folder:
            stage = Path(folder) / "annotated.docx"
            written_ids = self._rewrite(source, stage, list(findings), cancellation)
            if written_ids:
                with zipfile.ZipFile(stage) as check:
                    check.testzip()
                    self._parse_xml(check.read("word/document.xml"))
                if cancellation:
                    cancellation.raise_if_cancelled()
                os.replace(stage, target)
            else:
                if not target_existed:
                    target.unlink(missing_ok=True)
            return AnnotationResult(tuple(written_ids))

    def _rewrite(
        self,
        source: Path,
        target: Path,
        findings: list[Finding],
        cancellation: CancellationToken | None,
    ) -> list[str]:
        with zipfile.ZipFile(source) as archive:
            document = self._parse_xml(archive.read("word/document.xml"))
            comments = self._load_comments(archive)
            rels = self._load_rels(archive)
            content_types = self._parse_xml(archive.read("[Content_Types].xml"))
            replacements: dict[str, bytes] = {}
            written_ids = self._annotate(document, comments, findings, cancellation)
            if not written_ids:
                return []
            self._ensure_comment_relationship(rels)
            self._ensure_comment_content_type(content_types)
            replacements["word/document.xml"] = self._xml(document)
            replacements["word/comments.xml"] = self._xml(comments)
            replacements["word/_rels/document.xml.rels"] = self._xml(rels)
            replacements["[Content_Types].xml"] = self._xml(content_types)
            with zipfile.ZipFile(target, "w") as output:
                for info in archive.infolist():
                    if cancellation:
                        cancellation.raise_if_cancelled()
                    if info.filename not in replacements:
                        output.writestr(info, archive.read(info.filename))
                for name, data in replacements.items():
                    output.writestr(name, data)
        return written_ids

    def _annotate(
        self,
        document: etree._Element,
        comments: etree._Element,
        findings: list[Finding],
        cancellation: CancellationToken | None,
    ) -> list[str]:
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
        written_ids: list[str] = []
        for index, items in grouped.items():
            if index >= len(paragraphs):
                continue
            paragraph = paragraphs[index]
            for finding in sorted(items, key=lambda item: item.start, reverse=True):
                if cancellation:
                    cancellation.raise_if_cancelled()
                if self._annotate_one(paragraph, comments, finding, next_id):
                    next_id += 1
                    written_ids.append(finding.id)
        return written_ids

    def _annotate_one(
        self, paragraph: etree._Element, comments: etree._Element, finding: Finding, comment_id: int
    ) -> bool:
        runs = paragraph.xpath("./w:r", namespaces=NS)
        all_texts = paragraph.xpath(".//w:t", namespaces=NS)
        if any(
            text.getparent() is None or text.getparent().getparent() is not paragraph
            for text in all_texts
        ):
            # Hyperlinks, tracked changes and content controls are preserved but
            # intentionally not mutated in M1 because their anchors are not flat.
            return False
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
        if source.stat().st_size > MAX_ARCHIVE_BYTES:
            raise InvalidDocument("DOCUMENT_ARCHIVE_LIMIT")
        try:
            with zipfile.ZipFile(source) as archive:
                infos = archive.infolist()
                names = set(archive.namelist())
                if len(infos) > MAX_ENTRIES or not REQUIRED_PARTS.issubset(names):
                    raise InvalidDocument("DOCUMENT_INVALID_PACKAGE")
                seen: set[str] = set()
                total = 0
                for info in infos:
                    # ZipInfo normalizes the platform separator in ``filename``
                    # on Windows. ``orig_filename`` retains the archive spelling,
                    # which is required to reject crafted backslash paths.
                    raw_name = info.orig_filename
                    normalized_name = info.filename
                    name = PurePosixPath(normalized_name)
                    unsafe_name = (
                        not raw_name
                        or "\\" in raw_name
                        or "\x00" in raw_name
                        or re.match(r"^[A-Za-z]:", raw_name) is not None
                        or normalized_name in seen
                        or name.is_absolute()
                        or ".." in name.parts
                        or info.flag_bits & 0x1
                    )
                    if unsafe_name:
                        raise InvalidDocument("DOCUMENT_UNSAFE_ZIP_ENTRY")
                    seen.add(normalized_name)
                    total += info.file_size
                    if total > MAX_UNCOMPRESSED or (
                        info.file_size
                        and (
                            not info.compress_size
                            or info.file_size / info.compress_size > MAX_RATIO
                        )
                    ):
                        raise InvalidDocument("DOCUMENT_ARCHIVE_LIMIT")
                content_types = self._parse_xml(archive.read("[Content_Types].xml"))
                root_rels = self._parse_xml(archive.read("_rels/.rels"))
                document = self._parse_xml(archive.read("word/document.xml"))
                if (
                    content_types.tag != f"{{{CT}}}Types"
                    or root_rels.tag != f"{{{REL}}}Relationships"
                    or document.tag != f"{{{W}}}document"
                ):
                    raise InvalidDocument("DOCUMENT_INVALID_PACKAGE")
                office_document_type = (
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                    "officeDocument"
                )
                if not root_rels.xpath(
                    "./rel:Relationship[@Type=$kind and @Target='word/document.xml']",
                    namespaces={"rel": REL},
                    kind=office_document_type,
                ):
                    raise InvalidDocument("DOCUMENT_INVALID_PACKAGE")
        except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError) as error:
            raise InvalidDocument("DOCUMENT_INVALID_PACKAGE") from error

    @staticmethod
    def _parser() -> etree.XMLParser:
        return etree.XMLParser(
            resolve_entities=False,
            load_dtd=False,
            no_network=True,
            huge_tree=False,
            remove_blank_text=False,
        )

    def _parse_xml(self, data: bytes) -> etree._Element:
        lowered = data.lstrip().lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            raise InvalidDocument("DOCUMENT_INVALID_PACKAGE")
        return etree.fromstring(data, parser=self._parser())

    def _load_comments(self, archive: zipfile.ZipFile) -> etree._Element:
        if "word/comments.xml" in archive.namelist():
            return self._parse_xml(archive.read("word/comments.xml"))
        return etree.Element(f"{{{W}}}comments", nsmap={"w": W})

    def _load_rels(self, archive: zipfile.ZipFile) -> etree._Element:
        name = "word/_rels/document.xml.rels"
        if name in archive.namelist():
            return self._parse_xml(archive.read(name))
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

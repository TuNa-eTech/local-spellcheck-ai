from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import replace

from .domain import Block, Finding, Preset

RULE_VERSION = "rules-0.1.0"

CONFUSIONS: dict[str, tuple[str, str]] = {
    "sát nhập": ("sáp nhập", "Cụm từ chuẩn là “sáp nhập”."),
    "chỉnh chu": ("chỉn chu", "Từ đúng chính tả là “chỉn chu”."),
    "xử lí": ("xử lý", "Khuyến nghị dùng “xử lý” thống nhất trong văn bản."),
    "qui định": ("quy định", "Khuyến nghị dùng “quy định” thống nhất trong văn bản."),
}


class RuleEngine:
    """Pure, deterministic candidate generator. It never reads files or databases."""

    def check(
        self,
        blocks: Iterable[Block],
        preset: Preset,
        ignored_words: frozenset[str] = frozenset(),
        limit: int = 200,
    ) -> list[Finding]:
        candidates: list[Finding] = []
        ignored = {unicodedata.normalize("NFC", word).casefold() for word in ignored_words}
        for block in blocks:
            normalized, source_map = _normalize_with_source_map(block.text)
            normalized_block = Block(block.id, normalized, block.kind)
            block_candidates = self._technical(normalized_block, normalized)
            block_candidates.extend(self._repeated_words(normalized_block, normalized, ignored))
            block_candidates.extend(self._confusions(normalized_block, normalized, ignored))
            if preset is Preset.ADMINISTRATIVE:
                block_candidates.extend(self._administrative(normalized_block, normalized))
            candidates.extend(
                _to_source_coordinates(item, block.text, source_map) for item in block_candidates
            )
            if len(candidates) >= limit:
                break
        return self._resolve_overlaps(candidates)[:limit]

    def _make(
        self,
        block: Block,
        start: int,
        end: int,
        category: str,
        detector: str,
        suggestion: str,
        reason: str,
        confidence: float = 1.0,
    ) -> Finding:
        return Finding(
            id=f"{block.id}:{start}:{end}:{detector}",
            category=category,
            origin="rule",
            detector_id=detector,
            block_id=block.id,
            start=start,
            end=end,
            source_text=block.text[start:end],
            suggestion=suggestion,
            reason=reason,
            rule_version=RULE_VERSION,
            confidence=confidence,
        )

    def _technical(self, block: Block, text: str) -> list[Finding]:
        found: list[Finding] = []
        for match in re.finditer(r" {2,}", text):
            found.append(
                self._make(
                    block,
                    *match.span(),
                    "technical",
                    "spacing.multiple.v1",
                    " ",
                    "Có nhiều khoảng trắng liên tiếp.",
                )
            )
        for match in re.finditer(r"\s+([,.;:!?])", text):
            found.append(
                self._make(
                    block,
                    *match.span(),
                    "technical",
                    "punctuation.leading_space.v1",
                    match.group(1),
                    "Không đặt khoảng trắng trước dấu câu.",
                )
            )
        for match in re.finditer(r"([,.;:!?])(?=[^\s\d])", text):
            found.append(
                self._make(
                    block,
                    *match.span(),
                    "technical",
                    "punctuation.missing_space.v1",
                    f"{match.group(1)} ",
                    "Nên có khoảng trắng sau dấu câu.",
                )
            )
        return found

    def _repeated_words(self, block: Block, text: str, ignored: set[str]) -> list[Finding]:
        found: list[Finding] = []
        pattern = re.compile(r"\b([A-Za-zÀ-ỹĐđ]+)(\s+)\1\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            if match.group(1).casefold() not in ignored:
                start = match.start(1) + len(match.group(1))
                found.append(
                    self._make(
                        block,
                        start,
                        match.end(),
                        "technical",
                        "word.repeated.v1",
                        "",
                        "Từ bị lặp liên tiếp.",
                    )
                )
        return found

    def _confusions(self, block: Block, text: str, ignored: set[str]) -> list[Finding]:
        found: list[Finding] = []
        lowered = text.casefold()
        for wrong, (right, reason) in CONFUSIONS.items():
            if wrong.casefold() in ignored:
                continue
            for match in re.finditer(rf"(?<!\w){re.escape(wrong)}(?!\w)", lowered):
                found.append(
                    self._make(
                        block,
                        *match.span(),
                        "spelling",
                        f"confusion.{wrong.replace(' ', '_')}.v1",
                        right,
                        reason,
                        0.98,
                    )
                )
        return found

    def _administrative(self, block: Block, text: str) -> list[Finding]:
        found: list[Finding] = []
        for match in re.finditer(r"(?<!\w)(ủy ban nhân dân)(?!\w)", text, re.IGNORECASE):
            if match.group(1) == match.group(1).lower():
                found.append(
                    self._make(
                        block,
                        *match.span(),
                        "capitalization",
                        "capitalization.agency.v1",
                        "Ủy ban nhân dân",
                        "Tên cơ quan nên được viết hoa.",
                    )
                )
        return found

    @staticmethod
    def _resolve_overlaps(findings: list[Finding]) -> list[Finding]:
        priority = {"spelling": 4, "compound_word": 3, "capitalization": 2, "technical": 1}
        ordered = sorted(
            findings,
            key=lambda item: (
                item.block_id,
                item.start,
                -priority.get(item.category, 0),
                -(item.end - item.start),
            ),
        )
        accepted: list[Finding] = []
        last_end: dict[str, int] = {}
        for finding in ordered:
            if finding.start >= last_end.get(finding.block_id, -1):
                accepted.append(finding)
                last_end[finding.block_id] = finding.end
        return accepted


def _normalize_with_source_map(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Map each NFC code point back to its original combining-character cluster."""
    normalized_parts: list[str] = []
    source_map: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        end = index + 1
        while end < len(text) and unicodedata.combining(text[end]):
            end += 1
        normalized_cluster = unicodedata.normalize("NFC", text[index:end])
        normalized_parts.append(normalized_cluster)
        source_map.extend((index, end) for _ in normalized_cluster)
        index = end
    return "".join(normalized_parts), source_map


def _to_source_coordinates(
    finding: Finding, original: str, source_map: list[tuple[int, int]]
) -> Finding:
    if not source_map or finding.start >= len(source_map) or finding.end <= 0:
        return finding
    source_start = source_map[finding.start][0]
    source_end = source_map[min(finding.end, len(source_map)) - 1][1]
    return replace(
        finding,
        start=source_start,
        end=source_end,
        source_text=original[source_start:source_end],
        id=f"{finding.block_id}:{source_start}:{source_end}:{finding.detector_id}",
    )

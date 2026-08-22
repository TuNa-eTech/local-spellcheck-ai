from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import replace

from .domain import Block, Finding, Preset, RuleConfig

RULE_VERSION = "rules-0.1.0"

CONFUSIONS: dict[str, tuple[str, str]] = {
    "sát nhập": ("sáp nhập", "Cụm từ chuẩn là “sáp nhập”."),
    "chỉnh chu": ("chỉn chu", "Từ đúng chính tả là “chỉn chu”."),
    "xử lí": ("xử lý", "Khuyến nghị dùng “xử lý” thống nhất trong văn bản."),
    "qui định": ("quy định", "Khuyến nghị dùng “quy định” thống nhất trong văn bản."),
    "ngiên cứu": ("nghiên cứu", "Âm tiết đứng trước i, e, ê phải dùng phụ âm đầu “ngh”."),
}

WORD_PATTERN = re.compile(r"[A-Za-zÀ-ỹĐđ]+")
FRONT_VOWELS = frozenset("iíìỉĩịeéèẻẽẹêếềểễệyýỳỷỹỵ")


def _syllable_repair(word: str) -> tuple[str, str] | None:
    """Return only high-confidence Vietnamese onset repairs.

    This is deliberately conservative: M1 must flag deterministic orthographic
    violations without treating every foreign name or acronym as a typo.
    """
    lowered = word.casefold()
    vietnamese_markers = (
        "đăằắẳẵặâầấẩẫậêềếểễệôồốổỗộơờớởỡợưừứửữự"
        "àáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ"
    )
    if len(lowered) < 2 or not any(character in vietnamese_markers for character in lowered):
        return None
    replacement: str | None = None
    reason = ""
    if (
        lowered.startswith("ng")
        and not lowered.startswith("ngh")
        and lowered[2:3] in FRONT_VOWELS
    ):
        replacement = word[:2] + ("H" if word[:2].isupper() else "h") + word[2:]
        reason = "Âm tiết đứng trước i, e, ê phải dùng phụ âm đầu “ngh”."
    elif (
        lowered.startswith("g")
        and not lowered.startswith(("gh", "gi"))
        and lowered[1:2] in FRONT_VOWELS
    ):
        replacement = word[:1] + ("H" if word[:1].isupper() else "h") + word[1:]
        reason = "Âm tiết đứng trước i, e, ê phải dùng phụ âm đầu “gh”."
    elif lowered.startswith("c") and lowered[1:2] in FRONT_VOWELS:
        replacement = ("K" if word[0].isupper() else "k") + word[1:]
        reason = "Âm tiết đứng trước i, e, ê phải dùng phụ âm đầu “k”."
    elif lowered.startswith("q") and lowered[1:2] != "u":
        replacement = word[:1] + ("U" if word[:1].isupper() else "u") + word[1:]
        reason = "Phụ âm đầu “q” trong âm tiết tiếng Việt phải đi cùng “u”."
    if replacement is None:
        return None
    return replacement, reason


def _contains_ignored(value: str, ignored: set[str]) -> bool:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return normalized in ignored or any(
        match.group(0).casefold() in ignored for match in WORD_PATTERN.finditer(normalized)
    )


class RuleEngine:
    """Pure, deterministic candidate generator. It never reads files or databases."""

    def check(
        self,
        blocks: Iterable[Block],
        preset: Preset,
        ignored_words: frozenset[str] = frozenset(),
        limit: int = 200,
        cancellation: Callable[[], None] | None = None,
    ) -> list[Finding]:
        candidates: list[Finding] = []
        config = RuleConfig.for_preset(preset)
        ignored = {unicodedata.normalize("NFC", word).casefold() for word in ignored_words}
        for block in blocks:
            if cancellation:
                cancellation()
            normalized, source_map = _normalize_with_source_map(block.text, cancellation)
            normalized_block = Block(block.id, normalized, block.kind)
            block_candidates: list[Finding] = []
            if config.technical:
                block_candidates.extend(self._technical(normalized_block, normalized, limit))
            if config.repeated_words:
                if cancellation:
                    cancellation()
                block_candidates.extend(
                    self._repeated_words(normalized_block, normalized, ignored, limit)
                )
            if cancellation:
                cancellation()
            if config.confusions:
                block_candidates.extend(
                    self._confusions(normalized_block, normalized, ignored, limit)
                )
            if cancellation:
                cancellation()
            if config.syllables:
                block_candidates.extend(
                    self._syllables(normalized_block, normalized, ignored, limit)
                )
            if config.administrative_capitalization:
                block_candidates.extend(
                    self._administrative(normalized_block, normalized, ignored, limit)
                )
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

    def _technical(self, block: Block, text: str, limit: int) -> list[Finding]:
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
            if len(found) >= limit:
                return found
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
            if len(found) >= limit:
                return found
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
            if len(found) >= limit:
                return found
        return found

    def _syllables(
        self, block: Block, text: str, ignored: set[str], limit: int
    ) -> list[Finding]:
        found: list[Finding] = []
        for match in WORD_PATTERN.finditer(text):
            word = match.group(0)
            if word.casefold() in ignored:
                continue
            repair = _syllable_repair(word)
            if repair is None:
                continue
            suggestion, reason = repair
            found.append(
                self._make(
                    block,
                    *match.span(),
                    "spelling",
                    "syllable.onset.v1",
                    suggestion,
                    reason,
                    0.99,
                )
            )
            if len(found) >= limit:
                return found
        return found

    def _repeated_words(
        self, block: Block, text: str, ignored: set[str], limit: int
    ) -> list[Finding]:
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
                if len(found) >= limit:
                    return found
        return found

    def _confusions(
        self, block: Block, text: str, ignored: set[str], limit: int
    ) -> list[Finding]:
        found: list[Finding] = []
        lowered = text.casefold()
        for wrong, (right, reason) in CONFUSIONS.items():
            if _contains_ignored(wrong, ignored):
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
                if len(found) >= limit:
                    return found
        return found

    def _administrative(
        self, block: Block, text: str, ignored: set[str], limit: int
    ) -> list[Finding]:
        found: list[Finding] = []
        for match in re.finditer(r"(?<!\w)(ủy ban nhân dân)(?!\w)", text, re.IGNORECASE):
            if _contains_ignored(match.group(1), ignored):
                continue
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
                if len(found) >= limit:
                    return found
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


def _normalize_with_source_map(
    text: str, cancellation: Callable[[], None] | None = None
) -> tuple[str, list[tuple[int, int]]]:
    """Map each NFC code point back to its original combining-character cluster."""
    normalized_parts: list[str] = []
    source_map: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if cancellation and index % 4096 == 0:
            cancellation()
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

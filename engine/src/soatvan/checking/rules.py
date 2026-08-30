from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import replace

from .administrative import scan_administrative_capitalization
from .domain import Block, Finding, Preset, RuleConfig
from .vocabulary import VietnameseVocabulary

RULE_VERSION = "rules-0.2.0"

CONFUSIONS: dict[str, tuple[str, str]] = {
    # === Từ gây nhầm lẫn (cả hai từ đều hợp lệ nhưng cụm sai) ===
    "sát nhập": ("sáp nhập", "Cụm từ chuẩn là \u201csáp nhập\u201d."),
    "chỉnh chu": ("chỉn chu", "Từ đúng chính tả là \u201cchỉn chu\u201d."),
    # === Lỗi vị trí dấu thanh (Unikey/Telex, không thể sửa bằng tone-swap) ===
    "qủa": ("quả", "Dấu thanh trong âm tiết \u201cquả\u201d phải đặt ở nguyên âm chính."),
    "kế họach": ("kế hoạch", "Dấu thanh phải đặt ở nguyên âm chính a: \u201ckế hoạch\u201d."),
    "tìm kíêm": ("tìm kiếm", "Dấu thanh phải đặt ở nguyên âm chính: \u201ctìm kiếm\u201d."),
    "điện thọai": ("điện thoại", "Dấu thanh phải đặt ở nguyên âm chính a: \u201cđiện thoại\u201d."),
    "rà sóat": ("rà soát", "Dấu thanh trong âm tiết \u201csoát\u201d phải đặt ở nguyên âm chính a."),
    # === Phương ngữ hợp lệ nhưng không chuẩn văn bản hành chính ===
    "hành chánh": ("hành chính", "Khuyến nghị dùng “hành chính” chuẩn văn bản quản lý nhà nước."),
    "nhơn dân": ("nhân dân", "Khuyến nghị dùng “nhân dân” chuẩn văn bản quản lý nhà nước."),
    "cá nhơn": ("cá nhân", "Khuyến nghị dùng “cá nhân” chuẩn văn bản quản lý nhà nước."),
    "nguyên nhơn": ("nguyên nhân", "Khuyến nghị dùng “nguyên nhân” chuẩn văn bản quản lý nhà nước."),
    "thống nhứt": ("thống nhất", "Khuyến nghị dùng “thống nhất” chuẩn văn bản quản lý nhà nước."),
    "cập nhựt": ("cập nhật", "Khuyến nghị dùng “cập nhật” chuẩn văn bản quản lý nhà nước."),
    "gởi": ("gửi", "Khuyến nghị dùng “gửi” thống nhất trong văn bản hành chính."),
    # === Lỗi dính phím / thiếu khoảng trắng / mất dấu ===
    "giayphep": ("giấy phép", "Thiếu dấu cách và dấu thanh: “giấy phép”."),
    "donnghi": ("đơn nghỉ", "Thiếu dấu cách và dấu thanh: “đơn nghỉ”."),
    "xacnhan": ("xác nhận", "Thiếu dấu cách và dấu thanh: “xác nhận”."),
    "hopdong": ("hợp đồng", "Thiếu dấu cách và dấu thanh: “hợp đồng”."),
    "baocao": ("báo cáo", "Thiếu dấu cách và dấu thanh: “báo cáo”."),
    "kehoach": ("kế hoạch", "Thiếu dấu cách và dấu thanh: “kế hoạch”."),
    "quyetdinh": ("quyết định", "Thiếu dấu cách và dấu thanh: “quyết định”."),
    "thongbao": ("thông báo", "Thiếu dấu cách và dấu thanh: “thông báo”."),
    "kiemtra": ("kiểm tra", "Thiếu dấu cách và dấu thanh: “kiểm tra”."),
    "vanban": ("văn bản", "Thiếu dấu cách và dấu thanh: “văn bản”."),
    "tochuc": ("tổ chức", "Thiếu dấu cách và dấu thanh: “tổ chức”."),
    "hằngtháng": ("hằng tháng", "Thiếu khoảng trắng giữa hai từ: “hằng tháng”."),
    "kịpthời": ("kịp thời", "Thiếu khoảng trắng giữa hai từ: “kịp thời”."),
    # === Chuẩn hoá lí/lý, qui/quy, kỉ/kỷ ===
    "kỉ cương": ("kỷ cương", "Khuyến nghị dùng “kỷ cương” thống nhất trong văn bản."),
    "kỉ luật": ("kỷ luật", "Khuyến nghị dùng “kỷ luật” thống nhất trong văn bản."),
    "kỉ niệm": ("kỷ niệm", "Khuyến nghị dùng “kỷ niệm” thống nhất trong văn bản."),
    "kỉ lục": ("kỷ lục", "Khuyến nghị dùng “kỷ lục” thống nhất trong văn bản."),
    "kỉ thuật": ("kỹ thuật", "Từ đúng chính tả là “kỹ thuật” (dấu ngã)."),
    "kỉ năng": ("kỹ năng", "Từ đúng chính tả là “kỹ năng” (dấu ngã)."),
    "xử lí": ("xử lý", "Khuyến nghị dùng “xử lý” thống nhất trong văn bản."),
    "qui định": ("quy định", "Khuyến nghị dùng “quy định” thống nhất trong văn bản."),
    "qui trình": ("quy trình", "Khuyến nghị dùng “quy trình” thống nhất trong văn bản."),
    "quản lí": ("quản lý", "Khuyến nghị dùng “quản lý” thống nhất trong văn bản."),
    # === Hạn / Hạng (cả hai hợp lệ nhưng khác nghĩa) ===
    "đúng hạng": ("đúng hạn", "Cụm từ đúng là “đúng hạn”."),
    "thời hạng": ("thời hạn", "Cụm từ đúng là “thời hạn”."),
    "quá hạng": ("quá hạn", "Cụm từ đúng là “quá hạn”."),
    "trể hạng": ("trễ hạn", "Cụm từ đúng là “trễ hạn”."),
    "hạng chế": ("hạn chế", "Từ đúng chính tả là “hạn chế”."),
}

WORD_PATTERN = re.compile(r"[A-Za-zÀ-ỹĐđ]+")
FRONT_VOWELS = frozenset("iíìỉĩịeéèẻẽẹêếềểễệyýỳỷỹỵ")
PUNCTUATION_WITHOUT_TRAILING_SPACE = frozenset("/\\)]}»”’\"'")
_VIETNAMESE_MARKERS = frozenset(
    "đăằắẳẵặâầấẩẫậêềếểễệôồốổỗộơờớởỡợưừứửữự"
    "àáảãạèéẻẽẹìíỉĩịòóỏõọùúủũụỳýỷỹỵ"
)


def _match_case(source: str, target: str) -> str:
    """Preserve casing (ALL CAPS or TitleCase) from source to target."""
    if not source or not target:
        return target
    if source.isupper() and len(source) > 1:
        return target.upper()
    if source[0].isupper():
        return target[0].upper() + target[1:]
    return target


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
    elif lowered.startswith("q") and _base_character(lowered[1:2]) != "u":
        replacement = word[:1] + ("U" if word[:1].isupper() else "u") + word[1:]
        reason = "Phụ âm đầu “q” trong âm tiết tiếng Việt phải đi cùng “u”."
    if replacement is None:
        return None
    return replacement, reason


def _base_character(value: str) -> str:
    return unicodedata.normalize("NFD", value)[:1]


def _high_confidence_missing_space(text: str, punctuation_index: int) -> bool:
    punctuation = text[punctuation_index]
    previous = text[punctuation_index - 1] if punctuation_index else ""
    following = text[punctuation_index + 1 : punctuation_index + 2]
    if not following or following in PUNCTUATION_WITHOUT_TRAILING_SPACE:
        return False
    if punctuation == "." and previous.isalnum() and following.isalpha():
        # Domains, e-mail addresses and abbreviations such as TP.HCM are more
        # common than a reliably detectable missing sentence space here.
        return False
    if punctuation == "." and previous in ",.;:!?":
        return False
    return not (punctuation == ":" and following in "/\\")


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
        limit: int = 1_000,
        cancellation: Callable[[], None] | None = None,
        config: RuleConfig | None = None,
    ) -> list[Finding]:
        candidates: list[Finding] = []
        config = config or RuleConfig.for_preset(preset)
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
            if config.dictionary:
                if cancellation:
                    cancellation()
                block_candidates.extend(
                    self._dictionary_check(normalized_block, normalized, ignored, limit)
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
        for match in re.finditer(r"[ \u00a0]+([,.;:!?])", text):
            if match.group(1) == "." and text[match.end() : match.end() + 1] == ".":
                continue
            found.append(
                self._make(
                    block,
                    *match.span(),
                    "technical",
                    "punctuation.leading_space.v2",
                    match.group(1),
                    "Không đặt khoảng trắng trước dấu câu.",
                )
            )
            if len(found) >= limit:
                return found
        for match in re.finditer(r"([,.;:!?])(?=[^\s\d,.;:!?])", text):
            if not _high_confidence_missing_space(text, match.start()):
                continue
            found.append(
                self._make(
                    block,
                    *match.span(),
                    "technical",
                    "punctuation.missing_space.v2",
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
                    "syllable.onset.v2",
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
        pattern = re.compile(
            r"\b([A-Za-zÀ-ỹĐđ]+)([ \u00a0]+)\1\b", re.IGNORECASE
        )
        for match in pattern.finditer(text):
            if match.group(1).casefold() not in ignored:
                start = match.start(1) + len(match.group(1))
                found.append(
                    self._make(
                        block,
                        start,
                        match.end(),
                        "technical",
                        "word.repeated.v2",
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
        vocab = VietnameseVocabulary()
        all_confusions = {**CONFUSIONS, **vocab.confusions}
        for wrong, (right, reason) in all_confusions.items():
            if _contains_ignored(wrong, ignored):
                continue
            for match in re.finditer(rf"(?<!\w){re.escape(wrong)}(?!\w)", lowered):
                source_slice = text[match.start() : match.end()]
                matched_right = _match_case(source_slice, right)
                found.append(
                    self._make(
                        block,
                        *match.span(),
                        "spelling",
                        f"confusion.{wrong.replace(' ', '_')}.v1",
                        matched_right,
                        reason,
                        0.98,
                    )
                )
                if len(found) >= limit:
                    return found

        # Dynamic bigram compound confusion check
        words = list(WORD_PATTERN.finditer(text))
        for i in range(len(words) - 1):
            m1, m2 = words[i], words[i + 1]
            if text[m1.end() : m2.start()].strip() != "":
                continue
            w1, w2 = m1.group(0), m2.group(0)
            pair_text = text[m1.start() : m2.end()]
            if (
                _contains_ignored(pair_text, ignored)
                or _contains_ignored(w1, ignored)
                or _contains_ignored(w2, ignored)
            ):
                continue
            pair_lowered = f"{w1.casefold()} {w2.casefold()}"
            if pair_lowered in all_confusions:
                continue
            confusion = vocab.find_compound_confusion(w1, w2)
            if confusion is not None:
                suggested_compound, reason = confusion
                matched_suggestion = _match_case(pair_text, suggested_compound)
                found.append(
                    self._make(
                        block,
                        m1.start(),
                        m2.end(),
                        "compound_word",
                        f"confusion.{pair_lowered.replace(' ', '_')}.v1",
                        matched_suggestion,
                        reason,
                        0.95,
                    )
                )
                if len(found) >= limit:
                    return found
        return found

    def _administrative(
        self, block: Block, text: str, ignored: set[str], limit: int
    ) -> list[Finding]:
        """Comprehensive administrative capitalization check under Decree 30/2020/ND-CP Appendix II."""
        found: list[Finding] = []
        for match in scan_administrative_capitalization(text):
            if _contains_ignored(match.source, ignored):
                continue
            found.append(
                self._make(
                    block,
                    match.span[0],
                    match.span[1],
                    "capitalization",
                    f"administrative.{match.rule_id}.v1",
                    match.suggestion,
                    match.reason,
                    0.98,
                )
            )
            if len(found) >= limit:
                return found
        return found

    def _dictionary_check(
        self, block: Block, text: str, ignored: set[str], limit: int
    ) -> list[Finding]:
        """Check syllables against the Vietnamese vocabulary dictionary.

        For each word that is NOT in the syllable set and contains Vietnamese
        diacritics, flag it as a spelling error and suggest corrections using
        tone-swap (hỏi↔ngã) and REP rules (ch↔tr, s↔x, d↔gi, ng↔ngh).
        """
        found: list[Finding] = []
        vocab = VietnameseVocabulary()
        words = list(WORD_PATTERN.finditer(text))
        # Collect already-flagged spans to avoid duplicates with _confusions / _syllables
        flagged_spans: set[tuple[int, int]] = set()
        for f in found:
            flagged_spans.add((f.start, f.end))

        for i, match in enumerate(words):
            word = match.group(0)
            lowered = word.casefold()
            if lowered in ignored:
                continue
            # Skip words without Vietnamese diacritics (ASCII, abbreviations, foreign words)
            if not any(c in _VIETNAMESE_MARKERS for c in lowered):
                continue
            # Skip if already a valid syllable
            if vocab.is_valid_syllable(word):
                continue

            # Word is NOT in the dictionary → likely a typo
            prev_word = words[i - 1].group(0) if i > 0 else ""
            suggestion = vocab.suggest_for_compound(prev_word, word) if prev_word else None
            if suggestion is None:
                suggestions = vocab.suggest_corrections(word)
                suggestion = suggestions[0] if suggestions else ""
            if not suggestion:
                # A glued word ("bổsung") yields no substitution candidate;
                # recover the missing space when the dictionary confirms it.
                suggestion = vocab.suggest_split(word) or ""

            if suggestion:
                suggestion = _match_case(word, suggestion)
                found.append(
                    self._make(
                        block,
                        *match.span(),
                        "spelling",
                        "dictionary.syllable.v1",
                        suggestion,
                        f"Từ \u201c{word}\u201d không có trong từ điển. Đề xuất: \u201c{suggestion}\u201d.",
                        0.95,
                    )
                )
            else:
                found.append(
                    self._make(
                        block,
                        *match.span(),
                        "spelling",
                        "dictionary.unknown.v1",
                        "",
                        f"Từ \u201c{word}\u201d không có trong từ điển tiếng Việt.",
                        0.80,
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

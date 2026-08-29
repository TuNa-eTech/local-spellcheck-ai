from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import replace

from .domain import Block, Finding, Preset, RuleConfig

RULE_VERSION = "rules-0.2.0"

CONFUSIONS: dict[str, tuple[str, str]] = {
    # Từ gây nhầm lẫn chuẩn & chính tả
    "sát nhập": ("sáp nhập", "Cụm từ chuẩn là “sáp nhập”."),
    "chỉnh chu": ("chỉn chu", "Từ đúng chính tả là “chỉn chu”."),
    "xử lí": ("xử lý", "Khuyến nghị dùng “xử lý” thống nhất trong văn bản."),
    "qui định": ("quy định", "Khuyến nghị dùng “quy định” thống nhất trong văn bản."),
    "qui trình": ("quy trình", "Khuyến nghị dùng “quy trình” thống nhất trong văn bản."),
    "ngiên cứu": ("nghiên cứu", "Âm tiết đứng trước i, e, ê phải dùng phụ âm đầu “ngh”."),
    "qủa": ("quả", "Dấu thanh trong âm tiết “quả” phải đặt ở nguyên âm chính."),
    # Dấu hỏi / ngã
    "xữ lý": ("xử lý", "Từ đúng chính tả là “xử lý” (dấu hỏi)."),
    "lưu trử": ("lưu trữ", "Từ đúng chính tả là “lưu trữ” (dấu ngã)."),
    "dử liệu": ("dữ liệu", "Từ đúng chính tả là “dữ liệu” (dấu ngã)."),
    "hướng dẩn": ("hướng dẫn", "Từ đúng chính tả là “hướng dẫn” (dấu ngã)."),
    "biểu mẩu": ("biểu mẫu", "Từ đúng chính tả là “biểu mẫu” (dấu ngã)."),
    "vẩn": ("vẫn", "Từ đúng chính tả là “vẫn” (dấu ngã)."),
    "xãy ra": ("xảy ra", "Từ đúng chính tả là “xảy ra” (dấu hỏi)."),
    "làm rỏ": ("làm rõ", "Từ đúng chính tả là “làm rõ” (dấu ngã)."),
    "nêu rỏ": ("nêu rõ", "Từ đúng chính tả là “nêu rõ” (dấu ngã)."),
    "rỏ ràng": ("rõ ràng", "Từ đúng chính tả là “rõ ràng” (dấu ngã)."),
    "rỏ": ("rõ", "Từ đúng chính tả là “rõ” (dấu ngã)."),
    "cụ thễ": ("cụ thể", "Từ đúng chính tả là “cụ thể” (dấu hỏi)."),
    "đùn đẫy": ("đùn đẩy", "Từ đúng chính tả là “đùn đẩy” (dấu hỏi)."),
    "bị củ": ("bị cũ", "Từ đúng chính tả là “bị cũ” (dấu ngã)."),
    "chử ký": ("chữ ký", "Từ đúng chính tả là “chữ ký” (dấu ngã)."),
    "chử kí": ("chữ ký", "Từ đúng chính tả là “chữ ký”."),
    "chử": ("chữ", "Từ đúng chính tả là “chữ” (dấu ngã)."),
    "ngẩu nhiên": ("ngẫu nhiên", "Từ đúng chính tả là “ngẫu nhiên” (dấu ngã)."),
    "không rỏ": ("không rõ", "Từ đúng chính tả là “không rõ” (dấu ngã)."),
    "lổi": ("lỗi", "Từ đúng chính tả là “lỗi” (dấu ngã)."),
    "xin lổi": ("xin lỗi", "Từ đúng chính tả là “xin lỗi” (dấu ngã)."),
    "chuyễn": ("chuyển", "Từ đúng chính tả là “chuyển” (dấu hỏi)."),
    "luân chuyễn": ("luân chuyển", "Từ đúng chính tả là “luân chuyển” (dấu hỏi)."),
    "bản vẻ": ("bản vẽ", "Từ đúng chính tả là “bản vẽ” (dấu ngã)."),
    "nỗi bật": ("nổi bật", "Từ đúng chính tả là “nổi bật” (dấu hỏi)."),
    "nắm vửng": ("nắm vững", "Từ đúng chính tả là “nắm vững” (dấu ngã)."),
    "văn bãn": ("văn bản", "Từ đúng chính tả là “văn bản” (dấu hỏi)."),
    "biên bãn": ("biên bản", "Từ đúng chính tả là “biên bản” (dấu hỏi)."),
    "hình ãnh": ("hình ảnh", "Từ đúng chính tả là “hình ảnh” (dấu hỏi)."),
    "sữa chửa": ("sửa chữa", "Từ đúng chính tả là “sửa chữa”."),
    "dể hiểu": ("dễ hiểu", "Từ đúng chính tả là “dễ hiểu” (dấu ngã)."),
    "dể quan sát": ("dễ quan sát", "Từ đúng chính tả là “dễ quan sát” (dấu ngã)."),
    "dể": ("dễ", "Từ đúng chính tả là “dễ” (dấu ngã)."),
    "hổ trợ": ("hỗ trợ", "Từ đúng chính tả là “hỗ trợ” (dấu ngã)."),
    "trể": ("trễ", "Từ đúng chính tả là “trễ” (dấu ngã)."),
    "mổi ngày": ("mỗi ngày", "Từ đúng chính tả là “mỗi ngày” (dấu ngã)."),
    "mổi tháng": ("mỗi tháng", "Từ đúng chính tả là “mỗi tháng” (dấu ngã)."),
    "mổi": ("mỗi", "Từ đúng chính tả là “mỗi” (dấu ngã)."),
    "lỉnh vực": ("lĩnh vực", "Từ đúng chính tả là “lĩnh vực” (dấu ngã)."),
    "kỷ thuật": ("kỹ thuật", "Từ đúng chính tả là “kỹ thuật” (dấu ngã)."),
    "kỷ năng": ("kỹ năng", "Từ đúng chính tả là “kỹ năng” (dấu ngã)."),
    "kỉ cương": ("kỷ cương", "Khuyến nghị dùng “kỷ cương” thống nhất trong văn bản."),
    "quản lí": ("quản lý", "Khuyến nghị dùng “quản lý” thống nhất trong văn bản."),
    "dẩn đến": ("dẫn đến", "Từ đúng chính tả là “dẫn đến” (dấu ngã)."),
    "dẩn": ("dẫn", "Từ đúng chính tả là “dẫn” (dấu ngã)."),
    "đã củ": ("đã cũ", "Từ đúng chính tả là “đã cũ” (dấu ngã)."),
    "sẻ": ("sẽ", "Từ đúng chính tả là “sẽ” (dấu ngã)."),
    "tẩy xoá": ("tẩy xóa", "Dấu thanh nên đặt ở nguyên âm chính a: “tẩy xóa”."),
    "chuyển vòng": ("chuyển vòng", "Từ đúng chính tả là “chuyển vòng” (dấu hỏi)."),
    "chuyển trả": ("chuyển trả", "Từ đúng chính tả là “chuyển trả” (dấu hỏi)."),
    "mật khẫu": ("mật khẩu", "Từ đúng chính tả là “mật khẩu” (dấu hỏi)."),
    "tái diển": ("tái diễn", "Từ đúng chính tả là “tái diễn” (dấu ngã)."),
    "chấn chĩnh": ("chấn chỉnh", "Từ đúng chính tả là “chấn chỉnh” (dấu hỏi)."),
    "giãi quyết": ("giải quyết", "Từ đúng chính tả là “giải quyết” (dấu hỏi)."),
    "thừơng xuyên": ("thường xuyên", "Từ đúng chính tả là “thường xuyên”."),
    "thừơng": ("thường", "Từ đúng chính tả là “thường”."),
    "rà sóat": ("rà soát", "Dấu thanh trong âm tiết “soát” phải đặt ở nguyên âm chính a."),
    "một cữa": ("một cửa", "Từ đúng chính tả là “một cửa” (dấu hỏi)."),
    "giửa": ("giữa", "Từ đúng chính tả là “giữa” (dấu ngã)."),
    "sở nội vụ": ("Sở Nội vụ", "Theo Nghị định 30/2020/NĐ-CP, tên cơ quan nên được viết hoa."),
    "phòng nội vụ": ("Phòng Nội vụ", "Theo Nghị định 30/2020/NĐ-CP, tên cơ quan nên được viết hoa."),
    # Phụ âm đầu / vần / chữ cái
    "bố chí": ("bố trí", "Từ đúng chính tả là “bố trí” (ch/tr)."),
    "đề suất": ("đề xuất", "Từ đúng chính tả là “đề xuất” (s/x)."),
    "che dấu": ("che giấu", "Từ đúng chính tả là “che giấu” (d/gi)."),
    "sắp sếp": ("sắp xếp", "Từ đúng chính tả là “sắp xếp” (s/x)."),
    "kinh ngiệm": ("kinh nghiệm", "Âm tiết đứng trước i, e, ê phải dùng phụ âm đầu “ngh”."),
    "theo giỏi": ("theo dõi", "Từ đúng chính tả là “theo dõi” (gi/d)."),
    "hoàn thàh": ("hoàn thành", "Từ đúng chính tả là “hoàn thành”."),
    "hài lồng": ("hài lòng", "Từ đúng chính tả là “hài lòng”."),
    "công dâng": ("công dân", "Từ đúng chính tả là “công dân” (n/ng)."),
    "thị chấn": ("thị trấn", "Từ đúng chính tả là “thị trấn” (ch/tr)."),
    "nghiệp vu": ("nghiệp vụ", "Từ đúng chính tả là “nghiệp vụ”."),
    "bỏ xót": ("bỏ sót", "Từ đúng chính tả là “bỏ sót” (x/s)."),
    "quan trọn": ("quan trọng", "Từ đúng chính tả là “quan trọng” (n/ng)."),
    "nhắt nhở": ("nhắc nhở", "Từ đúng chính tả là “nhắc nhở” (t/c)."),
    "đột suất": ("đột xuất", "Từ đúng chính tả là “đột xuất” (s/x)."),
    "khó khăng": ("khó khăn", "Từ đúng chính tả là “khó khăn” (ng/n)."),
    "đôn đôc": ("đôn đốc", "Từ đúng chính tả là “đôn đốc”."),
    "tình hìng": ("tình hình", "Từ đúng chính tả là “tình hình”."),
    "thực trạn": ("thực trạng", "Từ đúng chính tả là “thực trạng”."),
    # Hạn / Hạng
    "đúng hạng": ("đúng hạn", "Cụm từ đúng là “đúng hạn”."),
    "thời hạng": ("thời hạn", "Cụm từ đúng là “thời hạn”."),
    "quá hạng": ("quá hạn", "Cụm từ đúng là “quá hạn”."),
    "trể hạng": ("trễ hạn", "Cụm từ đúng là “trễ hạn”."),
    "hạng chế": ("hạn chế", "Từ đúng chính tả là “hạn chế”."),
    "xếp lọai": ("xếp loại", "Từ đúng chính tả là “xếp loại”."),
    "định giạng": ("định dạng", "Từ đúng chính tả là “định dạng”."),
    # Phương ngữ hành chính
    "hành chánh": ("hành chính", "Khuyến nghị dùng “hành chính” chuẩn văn bản quản lý nhà nước."),
    "nhơn dân": ("nhân dân", "Khuyến nghị dùng “nhân dân” chuẩn văn bản quản lý nhà nước."),
    "cá nhơn": ("cá nhân", "Khuyến nghị dùng “cá nhân” chuẩn văn bản quản lý nhà nước."),
    "nguyên nhơn": ("nguyên nhân", "Khuyến nghị dùng “nguyên nhân” chuẩn văn bản quản lý nhà nước."),
    "thống nhứt": ("thống nhất", "Khuyến nghị dùng “thống nhất” chuẩn văn bản quản lý nhà nước."),
    "cập nhựt": ("cập nhật", "Khuyến nghị dùng “cập nhật” chuẩn văn bản quản lý nhà nước."),
    "gởi": ("gửi", "Khuyến nghị dùng “gửi” thống nhất trong văn bản hành chính."),
    # Bộ gõ / dính phím / vị trí dấu
    "đựơc": ("được", "Lỗi gõ dấu: viết đúng là “được”."),
    "số liêu": ("số liệu", "Từ đúng chính tả là “số liệu”."),
    "cung câp": ("cung cấp", "Từ đúng chính tả là “cung cấp”."),
    "nâng câp": ("nâng cấp", "Từ đúng chính tả là “nâng cấp”."),
    "thòi gian": ("thời gian", "Từ đúng chính tả là “thời gian”."),
    "thòi": ("thời", "Từ đúng chính tả là “thời”."),
    "trừơng": ("trường", "Lỗi gõ dấu: viết đúng là “trường”."),
    "kế họach": ("kế hoạch", "Dấu thanh phải đặt ở nguyên âm chính a: “kế hoạch”."),
    "hằngtháng": ("hằng tháng", "Thiếu khoảng trắng giữa hai từ: “hằng tháng”."),
    "kịpthời": ("kịp thời", "Thiếu khoảng trắng giữa hai từ: “kịp thời”."),
    "tìm kíêm": ("tìm kiếm", "Dấu thanh phải đặt ở nguyên âm chính: “tìm kiếm”."),
    "điện thọai": ("điện thoại", "Dấu thanh phải đặt ở nguyên âm chính a: “điện thoại”."),
    "đi lạị": ("đi lại", "Từ đúng chính tả là “đi lại”."),
    "nhìêu": ("nhiều", "Dấu thanh phải đặt ở nguyên âm chính: “nhiều”."),
}

WORD_PATTERN = re.compile(r"[A-Za-zÀ-ỹĐđ]+")
FRONT_VOWELS = frozenset("iíìỉĩịeéèẻẽẹêếềểễệyýỳỷỹỵ")
PUNCTUATION_WITHOUT_TRAILING_SPACE = frozenset("/\\)]}»”’\"'")


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
                        "Theo Nghị định 30/2020/NĐ-CP, Phụ lục II, tên cơ quan nên được viết hoa.",
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

"""Vietnamese administrative document capitalization rules based on Decree 30/2020/ND-CP Appendix II."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

REASON_AGENCY = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên cơ quan, tổ chức phải được viết hoa theo quy tắc thể thức."
REASON_UNIT = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), đơn vị hành chính kết hợp chữ số phải viết hoa cả danh từ chung."
REASON_CITATION = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), khi viện dẫn điều, khoản, điểm, phụ lục cụ thể phải viết hoa chữ cái đầu."
REASON_POSITION = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), chức vụ cấp cao và danh hiệu cao quý của Nhà nước phải được viết hoa."
REASON_REVERENCE = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), danh từ chung dùng làm tên riêng để tỏ sự tôn kính phải được viết hoa."
REASON_HOLIDAY = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên ngày lễ kỷ niệm, sự kiện lịch sử phải được viết hoa."

_DATA_PATH = Path(__file__).parent / "data" / "administrative_entities.json"


@dataclass(frozen=True, slots=True)
class AdminMatch:
    span: tuple[int, int]
    source: str
    suggestion: str
    rule_id: str
    reason: str


class _AdminEntitiesRegistry:
    """Singleton-style registry for precompiled administrative entity patterns."""

    _instance: _AdminEntitiesRegistry | None = None

    def __init__(self) -> None:
        self.central_patterns: list[tuple[re.Pattern[str], str]] = []
        self.ministry_patterns: list[tuple[re.Pattern[str], str]] = []
        self.local_agency_patterns: list[tuple[re.Pattern[str], str]] = []
        self.position_patterns: list[tuple[re.Pattern[str], str]] = []
        self.holiday_replacements: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not _DATA_PATH.exists():
            return
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))

        # 1. Central agencies
        self.central_patterns = [
            (re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)", re.IGNORECASE), right)
            for wrong, right in data.get("central_agencies", [])
        ]

        # 2. Ministry fields
        self.ministry_patterns = [
            (re.compile(rf"(?<!\w)bộ\s+{re.escape(field)}(?!\w)", re.IGNORECASE), f"Bộ {right}")
            for field, right in data.get("ministry_fields", [])
        ]

        # 3. Local agency prefixes & fields
        local_fields = data.get("local_fields", [])
        for prefix_lower, prefix_proper in [
            ("sở", "Sở"),
            ("phòng", "Phòng"),
            ("chi cục", "Chi cục"),
            ("cục", "Cục"),
            ("ban", "Ban"),
        ]:
            for field_lower, field_proper in local_fields:
                self.local_agency_patterns.append((
                    re.compile(rf"(?<!\w){re.escape(prefix_lower)}\s+{re.escape(field_lower)}(?!\w)", re.IGNORECASE),
                    f"{prefix_proper} {field_proper}",
                ))

        # 4. Positions, titles, honors, reverence, events
        combined_positions = (
            data.get("high_level_positions", [])
            + data.get("titles_and_honors", [])
            + data.get("reverence_and_events", [])
        )
        self.position_patterns = [
            (re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)", re.IGNORECASE), right)
            for wrong, right in combined_positions
        ]

        # 5. Holiday dictionary
        self.holiday_replacements = data.get("holiday_replacements", {})

    @classmethod
    def get(cls) -> _AdminEntitiesRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# Regex for Committee: Ủy ban nhân dân, Hội đồng nhân dân (also catch erroneous "Ủy Ban Nhân Dân")
_COMMITTEE_PATTERN = re.compile(
    r"(?<!\w)(ủy\s+ban\s+nhân\s+dân|hội\s+đồng\s+nhân\s+dân)(?!\w)",
    re.IGNORECASE,
)

# Regex for administrative unit with numbers: Quận 1, Phường 5, v.v.
_UNIT_NUMBER_PATTERN = re.compile(
    r"(?<!\w)(quận|phường|tổ\s+dân\s+phố|thôn|ấp|xóm|khu\s+phố)\s+(\d+|[A-ZĐ])(?!\w)",
    re.IGNORECASE,
)

# Regex for legal citation: Điều 12, Khoản 2, Điểm a, Phụ lục II, Chương IV, Mục 1, Phần 3
_CITATION_PATTERN = re.compile(
    r"(?<!\w)(điều|khoản|điểm|phụ\s+lục|chương|mục|phần)\s+([0-9ivxlcdmIVXLCDM]+|[a-zđ])(?!\w)",
    re.IGNORECASE,
)

# Regex for specific holidays: ngày Quốc khánh 2/9, ngày Nhà giáo Việt Nam 20/11...
_HOLIDAY_WITH_DATE_PATTERN = re.compile(
    r"(?<!\w)ngày\s+(quốc\s+khánh\s+2/9|quốc\s+tế\s+lao\s+động\s+1/5|giải\s+phóng\s+miền\s+nam\s+30/4|"
    r"thương\s+binh\s*-\s*liệt\s+sĩ\s+27/7|nhà\s+giáo\s+việt\s+nam\s+20/11|thầy\s+thuốc\s+việt\s+nam\s+27/2|"
    r"phụ\s+nữ\s+việt\s+nam\s+20/10|quốc\s+tế\s+phụ\s+nữ\s+8/3|thành\s+lập\s+quân\s+đội\s+nhân\s+dân\s+việt\s+nam\s+22/12)(?!\w)",
    re.IGNORECASE,
)


def _is_all_caps(text: str) -> bool:
    """Return True if text consists of uppercase letters (allowing spaces/punctuation)."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def scan_administrative_capitalization(text: str) -> Iterator[AdminMatch]:
    """Scan text for all administrative capitalization rule violations under Decree 30/2020/ND-CP."""
    registry = _AdminEntitiesRegistry.get()
    seen_spans: set[tuple[int, int]] = set()

    def _should_yield(span: tuple[int, int], source: str, suggestion: str) -> bool:
        if re.sub(r"\s+", " ", source) == suggestion:
            return False
        if _is_all_caps(source):
            return False
        for start, end in seen_spans:
            if not (span[1] <= start or span[0] >= end):
                return False
        seen_spans.add(span)
        return True

    # 1. Central Agencies
    for pattern, suggestion in registry.central_patterns:
        for match in pattern.finditer(text):
            span = match.span()
            source = match.group(0)
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, "agency_central", REASON_AGENCY)

    # 2. Ministries
    for pattern, suggestion in registry.ministry_patterns:
        for match in pattern.finditer(text):
            span = match.span()
            source = match.group(0)
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, "ministry", REASON_AGENCY)

    # 3. Local Agencies (Sở, Phòng, Chi cục, Cục, Ban)
    for pattern, suggestion in registry.local_agency_patterns:
        for match in pattern.finditer(text):
            span = match.span()
            source = match.group(0)
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, "agency_local", REASON_AGENCY)

    # 4. Committee: Ủy ban nhân dân, Hội đồng nhân dân
    for match in _COMMITTEE_PATTERN.finditer(text):
        span = match.span()
        source = match.group(0)
        lowered = source.casefold()
        if "ủy ban" in lowered:
            suggestion = "Ủy ban nhân dân"
        else:
            suggestion = "Hội đồng nhân dân"
        if _should_yield(span, source, suggestion):
            yield AdminMatch(span, source, suggestion, "committee", REASON_AGENCY)

    # 5. Administrative Units with Numbers: Quận 1, Phường 5, Tổ dân phố 3
    for match in _UNIT_NUMBER_PATTERN.finditer(text):
        span = match.span()
        source = match.group(0)
        unit_type = match.group(1)
        number = match.group(2)
        unit_words = unit_type.split()
        unit_proper = unit_words[0].capitalize() + (" " + " ".join(unit_words[1:]) if len(unit_words) > 1 else "")
        suggestion = f"{unit_proper} {number}"
        if _should_yield(span, source, suggestion):
            yield AdminMatch(span, source, suggestion, "unit_with_number", REASON_UNIT)

    # 6. Legal Citations: Điều 12, Khoản 2, Điểm a, Phụ lục II
    for match in _CITATION_PATTERN.finditer(text):
        span = match.span()
        source = match.group(0)
        term = match.group(1).casefold()
        sub = match.group(2)

        term_map = {
            "điều": "Điều",
            "khoản": "Khoản",
            "điểm": "Điểm",
            "phụ lục": "Phụ lục",
            "chương": "Chương",
            "mục": "Mục",
            "phần": "Phần",
        }
        proper_term = term_map.get(term, term.capitalize())
        if term in ("phụ lục", "chương", "phần") and re.match(r"^[ivxlcdm]+$", sub, re.IGNORECASE):
            sub_formatted = sub.upper()
        elif term == "điểm":
            sub_formatted = sub.lower()
        else:
            sub_formatted = sub

        suggestion = f"{proper_term} {sub_formatted}"
        if _should_yield(span, source, suggestion):
            yield AdminMatch(span, source, suggestion, "citation", REASON_CITATION)

    # 7. High-level positions, titles, honors, reverence
    for pattern, suggestion in registry.position_patterns:
        for match in pattern.finditer(text):
            span = match.span()
            source = match.group(0)
            rule_id = "position_or_honor"
            reason = REASON_POSITION
            if any(k in suggestion.casefold() for k in ("đảng", "bác hồ", "hồ chí minh", "tết", "cách mạng", "chiến dịch")):
                rule_id = "reverence_or_event"
                reason = REASON_REVERENCE if "đảng" in suggestion.casefold() or "bác" in suggestion.casefold() else REASON_HOLIDAY
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, rule_id, reason)

    # 8. Holidays with specific dates
    for match in _HOLIDAY_WITH_DATE_PATTERN.finditer(text):
        span = match.span()
        source = match.group(0)
        holiday_part = match.group(1).casefold().strip()
        holiday_clean = re.sub(r"\s+", " ", holiday_part)
        proper_holiday = registry.holiday_replacements.get(holiday_clean)
        if proper_holiday:
            suggestion = f"ngày {proper_holiday}"
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, "holiday", REASON_HOLIDAY)

"""Vietnamese administrative document capitalization rules based on Decree 30/2020/ND-CP Appendix II."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

REASON_AGENCY = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên cơ quan, tổ chức, doanh nghiệp nhà nước phải được viết hoa đúng thể thức."
REASON_INSTITUTION = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên trường đại học, học viện, bệnh viện công lập phải được viết hoa đúng thể thức."
REASON_LAW = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên văn bản quy phạm pháp luật (Hiến pháp, Bộ luật, Luật) phải được viết hoa."
REASON_GEOGRAPHY = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên địa danh, vùng địa lý, đơn vị hành chính đặc thù phải được viết hoa."
REASON_UNIT = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), đơn vị hành chính kết hợp chữ số phải viết hoa cả danh từ chung."
REASON_CITATION = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), khi viện dẫn điều, khoản, điểm, phụ lục cụ thể phải viết hoa chữ cái đầu."
REASON_POSITION = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), chức vụ lãnh đạo và danh hiệu cao quý phải được viết hoa."
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


def _make_flexible_pattern(phrase: str) -> re.Pattern[str]:
    """Compile phrase into a regex tolerant of variable whitespace and hyphen spacing."""
    escaped = re.escape(phrase)
    escaped = re.sub(r"\\\s+", r"\\s+", escaped)
    escaped = re.sub(r"\\s*\\-\\s*", r"\\s*-\\s*", escaped)
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


class _AdminEntitiesRegistry:
    """Singleton-style registry for precompiled administrative entity patterns."""

    _instance: _AdminEntitiesRegistry | None = None

    def __init__(self) -> None:
        self.central_patterns: list[tuple[re.Pattern[str], str]] = []
        self.ministry_patterns: list[tuple[re.Pattern[str], str]] = []
        self.local_agency_patterns: list[tuple[re.Pattern[str], str]] = []
        self.position_patterns: list[tuple[re.Pattern[str], str]] = []
        self.corporation_patterns: list[tuple[re.Pattern[str], str]] = []
        self.institution_patterns: list[tuple[re.Pattern[str], str]] = []
        self.law_patterns: list[tuple[re.Pattern[str], str]] = []
        self.geography_patterns: list[tuple[re.Pattern[str], str]] = []
        self.holiday_replacements: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not _DATA_PATH.exists():
            return
        data = json.loads(_DATA_PATH.read_text(encoding="utf-8"))

        # 1. Central agencies
        self.central_patterns = [
            (_make_flexible_pattern(wrong), right)
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
                    _make_flexible_pattern(f"{prefix_lower} {field_lower}"),
                    f"{prefix_proper} {field_proper}",
                ))

        # 4. Positions, titles, honors, reverence, events
        combined_positions = (
            data.get("high_level_positions", [])
            + data.get("titles_and_honors", [])
            + data.get("reverence_and_events", [])
        )
        self.position_patterns = [
            (_make_flexible_pattern(wrong), right)
            for wrong, right in combined_positions
        ]

        # 5. State Corporations
        self.corporation_patterns = [
            (_make_flexible_pattern(wrong), right)
            for wrong, right in data.get("state_corporations", [])
        ]

        # 6. Major Institutions (Đại học, Học viện, Bệnh viện)
        self.institution_patterns = [
            (_make_flexible_pattern(wrong), right)
            for wrong, right in data.get("major_institutions", [])
        ]

        # 7. Laws and Codes
        self.law_patterns = [
            (_make_flexible_pattern(wrong), right)
            for wrong, right in data.get("laws_and_codes", [])
        ]

        # 8. Geographic Regions & Distinct Place Names
        self.geography_patterns = [
            (_make_flexible_pattern(wrong), right)
            for wrong, right in data.get("geographic_regions", [])
        ]

        # 9. Holiday dictionary
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
    r"(?<!\w)(quận|phường|tổ\s+dân\s+phố|thôn|ấp|xóm|khu\s+phố|quân\s+khu|quân\s+đoàn)\s+(\d+|[A-ZĐ])(?!\w)",
    re.IGNORECASE,
)

# Regex for legal citation: Điều 12, Khoản 2, Điểm a, Phụ lục II, Chương IV, Mục 1, Phần 3
_CITATION_PATTERN = re.compile(
    r"(?<!\w)(điều|khoản|điểm|phụ\s+lục|chương|mục|phần)\s+([0-9ivxlcdmIVXLCDM]+|[a-zđ])(?!\w)",
    re.IGNORECASE,
)

# Regex for National Assembly / Party congress terms: Quốc hội khóa XV, Đại hội đại biểu toàn quốc lần thứ XIII...
_CONGRESS_TERM_PATTERN = re.compile(
    r"(?<!\w)(quốc\s+hội\s+khóa|đại\s+hội\s+đại\s+biểu\s+toàn\s+quốc\s+lần\s+thứ|đại\s+hội\s+đảng\s+toàn\s+quốc\s+lần\s+thứ|"
    r"ban\s+chấp\s+hành\s+trung\s+ương\s+đảng\s+khóa|hội\s+đồng\s+nhân\s+dân\s+khóa)\s+([0-9ivxlcdmIVXLCDM]+)(?!\w)",
    re.IGNORECASE,
)

# Regex for specific holidays: ngày Quốc khánh 2/9, ngày Nhà giáo Việt Nam 20/11...
_HOLIDAY_WITH_DATE_PATTERN = re.compile(
    r"(?<!\w)ngày\s+(quốc\s+khánh\s+2/9|quốc\s+tế\s+lao\s+động\s+1/5|giải\s+phóng\s+miền\s+nam\s+30/4|"
    r"thương\s+binh\s*-\s*liệt\s+sĩ\s+27/7|nhà\s+giáo\s+việt\s+nam\s+20/11|thầy\s+thuốc\s+việt\s+nam\s+27/2|"
    r"phụ\s+nữ\s+việt\s+nam\s+20/10|quốc\s+tế\s+phụ\s+nữ\s+8/3|thành\s+lập\s+quân\s+đội\s+nhân\s+dân\s+việt\s+nam\s+22/12|"
    r"giỗ\s+tổ\s+hùng\s+vương|toàn\s+quốc\s+kháng\s+chiến\s+19/12|bác\s+hồ\s+ra\s+đi\s+tìm\s+đường\s+cứu\s+nước\s+5/6)(?!\w)",
    re.IGNORECASE,
)


def _is_all_caps(text: str) -> bool:
    """Return True if text consists of uppercase letters (allowing spaces/punctuation)."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def scan_administrative_capitalization(text: str) -> Iterator[AdminMatch]:
    """Scan text for all administrative capitalization rule violations under Decree 30/2020/ND-CP."""
    registry = _AdminEntitiesRegistry.get()
    raw_matches: list[AdminMatch] = []

    # 1. Central Agencies
    for pattern, suggestion in registry.central_patterns:
        for match in pattern.finditer(text):
            raw_matches.append(AdminMatch(match.span(), match.group(0), suggestion, "agency_central", REASON_AGENCY))

    # 2. Ministries
    for pattern, suggestion in registry.ministry_patterns:
        for match in pattern.finditer(text):
            raw_matches.append(AdminMatch(match.span(), match.group(0), suggestion, "ministry", REASON_AGENCY))

    # 3. Local Agencies (Sở, Phòng, Chi cục, Cục, Ban)
    for pattern, suggestion in registry.local_agency_patterns:
        for match in pattern.finditer(text):
            raw_matches.append(AdminMatch(match.span(), match.group(0), suggestion, "agency_local", REASON_AGENCY))

    # 4. State Corporations
    for pattern, suggestion in registry.corporation_patterns:
        for match in pattern.finditer(text):
            raw_matches.append(AdminMatch(match.span(), match.group(0), suggestion, "corporation", REASON_AGENCY))

    # 5. Major Institutions (Đại học, Học viện, Bệnh viện)
    for pattern, suggestion in registry.institution_patterns:
        for match in pattern.finditer(text):
            raw_matches.append(AdminMatch(match.span(), match.group(0), suggestion, "institution", REASON_INSTITUTION))

    # 6. Laws and Codes
    for pattern, suggestion in registry.law_patterns:
        for match in pattern.finditer(text):
            raw_matches.append(AdminMatch(match.span(), match.group(0), suggestion, "law", REASON_LAW))

    # 7. Geographic Regions & Place Names
    for pattern, suggestion in registry.geography_patterns:
        for match in pattern.finditer(text):
            raw_matches.append(AdminMatch(match.span(), match.group(0), suggestion, "geography", REASON_GEOGRAPHY))

    # 8. High-level positions, titles, honors, reverence
    for pattern, suggestion in registry.position_patterns:
        for match in pattern.finditer(text):
            source = match.group(0)
            rule_id = "position_or_honor"
            reason = REASON_POSITION
            if any(k in suggestion.casefold() for k in ("đảng", "bác hồ", "hồ chí minh", "tết", "cách mạng", "chiến dịch", "quân đội nhân dân", "công an nhân dân")):
                rule_id = "reverence_or_event"
                reason = REASON_REVERENCE if any(k in suggestion.casefold() for k in ("đảng", "bác", "quân đội", "công an")) else REASON_HOLIDAY
            raw_matches.append(AdminMatch(match.span(), source, suggestion, rule_id, reason))

    # 9. Committee: Ủy ban nhân dân, Hội đồng nhân dân
    for match in _COMMITTEE_PATTERN.finditer(text):
        source = match.group(0)
        lowered = source.casefold()
        suggestion = "Ủy ban nhân dân" if "ủy ban" in lowered else "Hội đồng nhân dân"
        raw_matches.append(AdminMatch(match.span(), source, suggestion, "committee", REASON_AGENCY))

    # 10. Administrative Units with Numbers / Military regions: Quận 1, Phường 5, Quân khu 7
    for match in _UNIT_NUMBER_PATTERN.finditer(text):
        source = match.group(0)
        unit_type = match.group(1).casefold()
        number = match.group(2)
        unit_map = {
            "quận": "Quận",
            "phường": "Phường",
            "tổ dân phố": "Tổ dân phố",
            "thôn": "Thôn",
            "ấp": "Ấp",
            "xóm": "Xóm",
            "khu phố": "Khu phố",
            "quân khu": "Quân khu",
            "quân đoàn": "Quân đoàn",
        }
        unit_proper = unit_map.get(unit_type, unit_type.capitalize())
        suggestion = f"{unit_proper} {number}"
        raw_matches.append(AdminMatch(match.span(), source, suggestion, "unit_with_number", REASON_UNIT))

    # 11. Legal Citations: Điều 12, Khoản 2, Điểm a, Phụ lục II
    for match in _CITATION_PATTERN.finditer(text):
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
        raw_matches.append(AdminMatch(match.span(), source, suggestion, "citation", REASON_CITATION))

    # 12. National Assembly / Party Terms: Quốc hội khóa XV, Đại hội đại biểu toàn quốc lần thứ XIII...
    for match in _CONGRESS_TERM_PATTERN.finditer(text):
        source = match.group(0)
        prefix = match.group(1).casefold()
        term_num = match.group(2).upper()
        prefix_clean = re.sub(r"\s+", " ", prefix)
        prefix_map = {
            "quốc hội khóa": "Quốc hội khóa",
            "đại hội đại biểu toàn quốc lần thứ": "Đại hội đại biểu toàn quốc lần thứ",
            "đại hội đảng toàn quốc lần thứ": "Đại hội Đảng toàn quốc lần thứ",
            "ban chấp hành trung ương đảng khóa": "Ban Chấp hành Trung ương Đảng khóa",
            "hội đồng nhân dân khóa": "Hội đồng nhân dân khóa",
        }
        proper_prefix = prefix_map.get(prefix_clean, prefix_clean.capitalize())
        suggestion = f"{proper_prefix} {term_num}"
        raw_matches.append(AdminMatch(match.span(), source, suggestion, "congress_term", REASON_REVERENCE))

    # 13. Holidays with specific dates
    for match in _HOLIDAY_WITH_DATE_PATTERN.finditer(text):
        source = match.group(0)
        holiday_part = match.group(1).casefold().strip()
        holiday_clean = re.sub(r"\s+", " ", holiday_part)
        proper_holiday = registry.holiday_replacements.get(holiday_clean)
        if proper_holiday:
            suggestion = f"ngày {proper_holiday}"
            raw_matches.append(AdminMatch(match.span(), source, suggestion, "holiday", REASON_HOLIDAY))

    # Filter out invalid / ALL CAPS / already-correct matches
    valid_candidates: list[AdminMatch] = []
    for m in raw_matches:
        if re.sub(r"\s+", " ", m.source) == m.suggestion:
            continue
        if _is_all_caps(m.source):
            continue
        valid_candidates.append(m)

    # Sort longest match first, then by start offset
    valid_candidates.sort(key=lambda m: (-(m.span[1] - m.span[0]), m.span[0]))

    # Greedily resolve overlapping spans
    accepted: list[AdminMatch] = []
    seen_spans: list[tuple[int, int]] = []
    for m in valid_candidates:
        span = m.span
        if any(not (span[1] <= s[0] or span[0] >= s[1]) for s in seen_spans):
            continue
        seen_spans.append(span)
        accepted.append(m)

    # Yield in document position order
    accepted.sort(key=lambda m: m.span[0])
    for m in accepted:
        yield m

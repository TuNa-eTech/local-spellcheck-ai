"""Vietnamese administrative document capitalization rules based on Decree 30/2020/ND-CP Appendix II."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

REASON_AGENCY = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên cơ quan, tổ chức phải được viết hoa theo quy tắc thể thức."
REASON_UNIT = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), đơn vị hành chính kết hợp chữ số phải viết hoa cả danh từ chung."
REASON_CITATION = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), khi viện dẫn điều, khoản, điểm, phụ lục cụ thể phải viết hoa chữ cái đầu."
REASON_POSITION = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), chức vụ cấp cao và danh hiệu cao quý của Nhà nước phải được viết hoa."
REASON_REVERENCE = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), danh từ chung dùng làm tên riêng để tỏ sự tôn kính phải được viết hoa."
REASON_HOLIDAY = "Theo Nghị định 30/2020/NĐ-CP (Phụ lục II), tên ngày lễ kỷ niệm, sự kiện lịch sử phải được viết hoa."


@dataclass(frozen=True, slots=True)
class AdminMatch:
    span: tuple[int, int]
    source: str
    suggestion: str
    rule_id: str
    reason: str


# 1. BỘ, CƠ QUAN NGANG BỘ, CƠ QUAN THUỘC CHÍNH PHỦ & TRUNG ƯƠNG
CENTRAL_AGENCIES: list[tuple[str, str]] = [
    ("văn phòng chính phủ", "Văn phòng Chính phủ"),
    ("văn phòng quốc hội", "Văn phòng Quốc hội"),
    ("văn phòng chủ tịch nước", "Văn phòng Chủ tịch nước"),
    ("văn phòng trung ương đảng", "Văn phòng Trung ương Đảng"),
    ("thanh tra chính phủ", "Thanh tra Chính phủ"),
    ("ngân hàng nhà nước việt nam", "Ngân hàng Nhà nước Việt Nam"),
    ("ủy ban dân tộc", "Ủy ban Dân tộc"),
    ("bảo hiểm xã hội việt nam", "Bảo hiểm Xã hội Việt Nam"),
    ("thông tấn xã việt nam", "Thông tấn xã Việt Nam"),
    ("đài tiếng nói việt nam", "Đài Tiếng nói Việt Nam"),
    ("đài truyền hình việt nam", "Đài Truyền hình Việt Nam"),
    ("học viện chính trị quốc gia hồ chí minh", "Học viện Chính trị quốc gia Hồ Chí Minh"),
    ("viện hàn lâm khoa học xã hội việt nam", "Viện Hàn lâm Khoa học xã hội Việt Nam"),
    ("viện hàn lâm khoa học và công nghệ việt nam", "Viện Hàn lâm Khoa học và Công nghệ Việt Nam"),
    ("ban quản lý lăng chủ tịch hồ chí minh", "Ban Quản lý Lăng Chủ tịch Hồ Chí Minh"),
    ("ủy ban quản lý vốn nhà nước tại doanh nghiệp", "Ủy ban Quản lý vốn nhà nước tại doanh nghiệp"),
    ("ban cơ yếu chính phủ", "Ban Cơ yếu Chính phủ"),
    # Tư pháp
    ("tòa án nhân dân tối cao", "Tòa án nhân dân tối cao"),
    ("tòa án nhân dân", "Tòa án nhân dân"),
    ("viện kiểm sát nhân dân tối cao", "Viện Kiểm sát nhân dân tối cao"),
    ("viện kiểm sát nhân dân", "Viện Kiểm sát nhân dân"),
    # Ban Đảng & Đoàn thể
    ("ban tuyên giáo trung ương", "Ban Tuyên giáo Trung ương"),
    ("ban tổ chức trung ương", "Ban Tổ chức Trung ương"),
    ("ban nội chính trung ương", "Ban Nội chính Trung ương"),
    ("ban dân vận trung ương", "Ban Dân vận Trung ương"),
    ("ban kinh tế trung ương", "Ban Kinh tế Trung ương"),
    ("ban đối ngoại trung ương", "Ban Đối ngoại Trung ương"),
    ("ủy ban kiểm tra trung ương", "Ủy ban Kiểm tra Trung ương"),
    ("mặt trận tổ quốc việt nam", "Mặt trận Tổ quốc Việt Nam"),
    ("tổng liên đoàn lao động việt nam", "Tổng Liên đoàn Lao động Việt Nam"),
    ("đoàn thanh niên cộng sản hồ chí minh", "Đoàn Thanh niên Cộng sản Hồ Chí Minh"),
    ("hội liên hiệp phụ nữ việt nam", "Hội Liên hiệp Phụ nữ Việt Nam"),
    ("hội nông dân việt nam", "Hội Nông dân Việt Nam"),
    ("hội cựu chiến binh việt nam", "Hội Cựu chiến binh Việt Nam"),
]

# Các Bộ
MINISTRY_FIELDS: list[tuple[str, str]] = [
    ("công an", "Công an"),
    ("quốc phòng", "Quốc phòng"),
    ("ngoại giao", "Ngoại giao"),
    ("nội vụ", "Nội vụ"),
    ("tư pháp", "Tư pháp"),
    ("tài chính", "Tài chính"),
    ("công thương", "Công Thương"),
    ("kế hoạch và đầu tư", "Kế hoạch và Đầu tư"),
    ("nông nghiệp và phát triển nông thôn", "Nông nghiệp và Phát triển nông thôn"),
    ("giao thông vận tải", "Giao thông vận tải"),
    ("xây dựng", "Xây dựng"),
    ("thông tin và truyền thông", "Thông tin và Truyền thông"),
    ("lao động - thương binh và xã hội", "Lao động - Thương binh và Xã hội"),
    ("văn hóa, thể thao và du lịch", "Văn hóa, Thể thao và Du lịch"),
    ("khoa học và công nghệ", "Khoa học và Công nghệ"),
    ("giáo dục và đào tạo", "Giáo dục và Đào tạo"),
    ("y tế", "Y tế"),
    ("tài nguyên và môi trường", "Tài nguyên và Môi trường"),
]

# Cơ quan địa phương: Sở, Phòng, Chi cục, Cục
LOCAL_FIELDS: list[tuple[str, str]] = [
    ("nội vụ", "Nội vụ"),
    ("tư pháp", "Tư pháp"),
    ("tài chính", "Tài chính"),
    ("kế hoạch và đầu tư", "Kế hoạch và Đầu tư"),
    ("công thương", "Công Thương"),
    ("nông nghiệp và phát triển nông thôn", "Nông nghiệp và Phát triển nông thôn"),
    ("giao thông vận tải", "Giao thông vận tải"),
    ("xây dựng", "Xây dựng"),
    ("tài nguyên và môi trường", "Tài nguyên và Môi trường"),
    ("thông tin và truyền thông", "Thông tin và Truyền thông"),
    ("lao động - thương binh và xã hội", "Lao động - Thương binh và Xã hội"),
    ("văn hóa và thể thao", "Văn hóa và Thể thao"),
    ("văn hóa, thể thao và du lịch", "Văn hóa, Thể thao và Du lịch"),
    ("văn hóa và thông tin", "Văn hóa và Thông tin"),
    ("khoa học và công nghệ", "Khoa học và Công nghệ"),
    ("giáo dục và đào tạo", "Giáo dục và Đào tạo"),
    ("y tế", "Y tế"),
    ("du lịch", "Du lịch"),
    ("ngoại vụ", "Ngoại vụ"),
    ("dân tộc", "Dân tộc"),
    ("quy hoạch - kiến trúc", "Quy hoạch - Kiến trúc"),
    ("tài chính - kế hoạch", "Tài chính - Kế hoạch"),
    ("kinh tế", "Kinh tế"),
    ("quản lý đô thị", "Quản lý đô thị"),
    ("kinh tế và hạ tầng", "Kinh tế và Hạ tầng"),
    ("thuế", "Thuế"),
    ("hải quan", "Hải quan"),
    ("kiểm lâm", "Kiểm lâm"),
    ("quản lý thị trường", "Quản lý thị trường"),
    ("dân số", "Dân số"),
    ("thống kê", "Thống kê"),
    ("an toàn thực phẩm", "An toàn thực phẩm"),
    ("tiêu chuẩn đo lường chất lượng", "Tiêu chuẩn Đo lường Chất lượng"),
    ("cảnh sát giao thông", "Cảnh sát giao thông"),
]

# 2. CHỨC VỤ CẤP CAO & DANH HIỆU
HIGH_LEVEL_POSITIONS: list[tuple[str, str]] = [
    ("tổng bí thư", "Tổng Bí thư"),
    ("chủ tịch nước", "Chủ tịch nước"),
    ("phó chủ tịch nước", "Phó Chủ tịch nước"),
    ("thủ tướng chính phủ", "Thủ tướng Chính phủ"),
    ("phó thủ tướng chính phủ", "Phó Thủ tướng Chính phủ"),
    ("chủ tịch quốc hội", "Chủ tịch Quốc hội"),
    ("phó chủ tịch quốc hội", "Phó Chủ tịch Quốc hội"),
    ("thường trực ban bí thư", "Thường trực Ban Bí thư"),
    ("chủ nhiệm ủy ban kiểm tra trung ương", "Chủ nhiệm Ủy ban Kiểm tra Trung ương"),
]

TITLES_AND_HONORS: list[tuple[str, str]] = [
    ("anh hùng lực lượng vũ trang nhân dân", "Anh hùng Lực lượng vũ trang nhân dân"),
    ("anh hùng lao động", "Anh hùng Lao động"),
    ("nhà giáo nhân dân", "Nhà giáo Nhân dân"),
    ("nhà giáo ưu tú", "Nhà giáo Ưu tú"),
    ("thầy thuốc nhân dân", "Thầy thuốc Nhân dân"),
    ("thầy thuốc ưu tú", "Thầy thuốc Ưu tú"),
    ("nghệ sĩ nhân dân", "Nghệ sĩ Nhân dân"),
    ("nghệ sĩ ưu tú", "Nghệ sĩ Ưu tú"),
    ("huân chương sao vàng", "Huân chương Sao vàng"),
    ("huân chương hồ chí minh", "Huân chương Hồ Chí Minh"),
    ("huân chương độc lập", "Huân chương Độc lập"),
    ("huân chương quân công", "Huân chương Quân công"),
    ("huân chương lao động", "Huân chương Lao động"),
    ("huân chương bảo vệ tổ quốc", "Huân chương Bảo vệ Tổ quốc"),
    ("huân chương chiến công", "Huân chương Chiến công"),
]

# 3. TÔN KÍNH & NGÀY LỄ / SỰ KIỆN LỊCH SỬ
REVERENCE_AND_EVENTS: list[tuple[str, str]] = [
    ("đảng cộng sản việt nam", "Đảng Cộng sản Việt Nam"),
    ("bác hồ", "Bác Hồ"),
    ("chủ tịch hồ chí minh", "Chủ tịch Hồ Chí Minh"),
    ("tết nguyên đán", "Tết Nguyên đán"),
    ("tết âm lịch", "Tết Âm lịch"),
    ("tết dương lịch", "Tết Dương lịch"),
    ("tết trung thu", "Tết Trung thu"),
    ("cách mạng tháng tám", "Cách mạng tháng Tám"),
    ("chiến dịch điện biên phủ", "Chiến dịch Điện Biên Phủ"),
    ("chiến dịch hồ chí minh", "Chiến dịch Hồ Chí Minh"),
]


# Compile regex patterns
_CENTRAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)", re.IGNORECASE), right)
    for wrong, right in CENTRAL_AGENCIES
]

_MINISTRY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<!\w)bộ\s+{re.escape(field)}(?!\w)", re.IGNORECASE), f"Bộ {right}")
    for field, right in MINISTRY_FIELDS
]

_LOCAL_AGENCY_PATTERNS: list[tuple[re.Pattern[str], str]] = []
for prefix_lower, prefix_proper in [
    ("sở", "Sở"),
    ("phòng", "Phòng"),
    ("chi cục", "Chi cục"),
    ("cục", "Cục"),
    ("ban", "Ban"),
]:
    for field_lower, field_proper in LOCAL_FIELDS:
        _LOCAL_AGENCY_PATTERNS.append((
            re.compile(rf"(?<!\w){re.escape(prefix_lower)}\s+{re.escape(field_lower)}(?!\w)", re.IGNORECASE),
            f"{prefix_proper} {field_proper}",
        ))

_POSITION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?<!\w){re.escape(wrong)}(?!\w)", re.IGNORECASE), right)
    for wrong, right in HIGH_LEVEL_POSITIONS + TITLES_AND_HONORS + REVERENCE_AND_EVENTS
]

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

_HOLIDAY_REPLACEMENTS = {
    "quốc khánh 2/9": "Quốc khánh 2/9",
    "quốc tế lao động 1/5": "Quốc tế Lao động 1/5",
    "giải phóng miền nam 30/4": "Giải phóng miền Nam 30/4",
    "thương binh - liệt sĩ 27/7": "Thương binh - Liệt sĩ 27/7",
    "thương binh-liệt sĩ 27/7": "Thương binh - Liệt sĩ 27/7",
    "nhà giáo việt nam 20/11": "Nhà giáo Việt Nam 20/11",
    "thầy thuốc việt nam 27/2": "Thầy thuốc Việt Nam 27/2",
    "phụ nữ việt nam 20/10": "Phụ nữ Việt Nam 20/10",
    "quốc tế phụ nữ 8/3": "Quốc tế Phụ nữ 8/3",
    "thành lập quân đội nhân dân việt nam 22/12": "Thành lập Quân đội nhân dân Việt Nam 22/12",
}


def _is_all_caps(text: str) -> bool:
    """Return True if text consists of uppercase letters (allowing spaces/punctuation)."""
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def scan_administrative_capitalization(text: str) -> Iterator[AdminMatch]:
    """Scan text for all administrative capitalization rule violations under Decree 30/2020/ND-CP."""
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
    for pattern, suggestion in _CENTRAL_PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            source = match.group(0)
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, "agency_central", REASON_AGENCY)

    # 2. Ministries
    for pattern, suggestion in _MINISTRY_PATTERNS:
        for match in pattern.finditer(text):
            span = match.span()
            source = match.group(0)
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, "ministry", REASON_AGENCY)

    # 3. Local Agencies (Sở, Phòng, Chi cục, Cục, Ban)
    for pattern, suggestion in _LOCAL_AGENCY_PATTERNS:
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
        # Capitalize unit type (e.g. quận -> Quận, tổ dân phố -> Tổ dân phố)
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
        # Roman numerals uppercase if in phụ lục/chương/phần
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
    for pattern, suggestion in _POSITION_PATTERNS:
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
        proper_holiday = _HOLIDAY_REPLACEMENTS.get(holiday_clean)
        if proper_holiday:
            suggestion = f"ngày {proper_holiday}"
            if _should_yield(span, source, suggestion):
                yield AdminMatch(span, source, suggestion, "holiday", REASON_HOLIDAY)

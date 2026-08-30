"""Tests for Vietnamese administrative document capitalization rules (Decree 30/2020/ND-CP)."""
from __future__ import annotations

from soatvan.checking.administrative import scan_administrative_capitalization


def _scan(text: str) -> list[tuple[str, str, str]]:
    """Helper returning list of (source, suggestion, rule_id)."""
    return [(m.source, m.suggestion, m.rule_id) for m in scan_administrative_capitalization(text)]


def test_ministries_capitalization() -> None:
    text = "Văn bản gửi bộ tài chính, bộ công an và bộ kế hoạch và đầu tư xem xét."
    matches = _scan(text)
    assert ("bộ tài chính", "Bộ Tài chính", "ministry") in matches
    assert ("bộ công an", "Bộ Công an", "ministry") in matches
    assert ("bộ kế hoạch và đầu tư", "Bộ Kế hoạch và Đầu tư", "ministry") in matches


def test_central_agencies_capitalization() -> None:
    text = "Trình văn phòng chính phủ, văn phòng quốc hội và thanh tra chính phủ."
    matches = _scan(text)
    assert ("văn phòng chính phủ", "Văn phòng Chính phủ", "agency_central") in matches
    assert ("văn phòng quốc hội", "Văn phòng Quốc hội", "agency_central") in matches
    assert ("thanh tra chính phủ", "Thanh tra Chính phủ", "agency_central") in matches


def test_local_departments_capitalization() -> None:
    text = "Giao sở nội vụ chủ trì, phối hợp với sở tư pháp và phòng giáo dục và đào tạo."
    matches = _scan(text)
    assert ("sở nội vụ", "Sở Nội vụ", "agency_local") in matches
    assert ("sở tư pháp", "Sở Tư pháp", "agency_local") in matches
    assert ("phòng giáo dục và đào tạo", "Phòng Giáo dục và Đào tạo", "agency_local") in matches


def test_committee_capitalization_and_erroneous_uppercase_fix() -> None:
    # 1. Lowercase to standard
    text1 = "Ủy ban nhân dân tỉnh phối hợp cùng hội đồng nhân dân huyện."
    # Since "Ủy ban nhân dân" is already correct, it should not be flagged
    assert not any(m[0] == "Ủy ban nhân dân" for m in _scan(text1))

    # 2. Lowercase erroneous
    text2 = "Trình ủy ban nhân dân tỉnh phê duyệt."
    assert ("ủy ban nhân dân", "Ủy ban nhân dân", "committee") in _scan(text2)

    # 3. All title case erroneous ("Ủy Ban Nhân Dân" -> "Ủy ban nhân dân")
    text3 = "Căn cứ quyết định của Ủy Ban Nhân Dân Thành phố."
    assert ("Ủy Ban Nhân Dân", "Ủy ban nhân dân", "committee") in _scan(text3)


def test_administrative_units_with_numbers() -> None:
    text = "Công dân cư trú tại quận 1, phường 5, tổ dân phố 3."
    matches = _scan(text)
    assert ("quận 1", "Quận 1", "unit_with_number") in matches
    assert ("phường 5", "Phường 5", "unit_with_number") in matches
    assert ("tổ dân phố 3", "Tổ dân phố 3", "unit_with_number") in matches


def test_legal_citations_capitalization() -> None:
    text = "Căn cứ điều 12, khoản 2, điểm a và phụ lục ii của nghị định."
    matches = _scan(text)
    assert ("điều 12", "Điều 12", "citation") in matches
    assert ("khoản 2", "Khoản 2", "citation") in matches
    assert ("điểm a", "Điểm a", "citation") in matches
    assert ("phụ lục ii", "Phụ lục II", "citation") in matches


def test_high_level_positions_and_honors() -> None:
    text = "Kính trình thủ tướng chính phủ, chủ tịch nước và tổng bí thư."
    matches = _scan(text)
    assert ("thủ tướng chính phủ", "Thủ tướng Chính phủ", "position_or_honor") in matches
    assert ("chủ tịch nước", "Chủ tịch nước", "position_or_honor") in matches
    assert ("tổng bí thư", "Tổng Bí thư", "position_or_honor") in matches


def test_holidays_and_reverence() -> None:
    text = "Kỷ niệm ngày quốc khánh 2/9 theo lời dạy của bác hồ và đường lối của đảng cộng sản việt nam."
    matches = _scan(text)
    assert ("ngày quốc khánh 2/9", "ngày Quốc khánh 2/9", "holiday") in matches
    assert ("bác hồ", "Bác Hồ", "reverence_or_event") in matches
    assert ("đảng cộng sản việt nam", "Đảng Cộng sản Việt Nam", "reverence_or_event") in matches


def test_preserve_all_caps_headings() -> None:
    # ALL CAPS in headers or agency names MUST NOT be flagged as errors
    text = (
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n"
        "BỘ TÀI CHÍNH\n"
        "ỦY BAN NHÂN DÂN TỈNH QUẢNG NINH\n"
        "QUYẾT ĐỊNH\n"
        "ĐIỀU 1. PHẠM VI ĐIỀU CHỈNH\n"
        "THỦ TƯỚNG CHÍNH PHỦ"
    )
    matches = _scan(text)
    assert len(matches) == 0, f"Expected 0 matches on ALL CAPS, got: {matches}"

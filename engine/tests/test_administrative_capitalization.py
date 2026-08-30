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


def test_central_agencies_and_armed_forces_capitalization() -> None:
    text = "Trình văn phòng chính phủ, thanh tra chính phủ, bộ đội biên phòng và cảnh sát biển việt nam."
    matches = _scan(text)
    assert ("văn phòng chính phủ", "Văn phòng Chính phủ", "agency_central") in matches
    assert ("thanh tra chính phủ", "Thanh tra Chính phủ", "agency_central") in matches
    assert ("bộ đội biên phòng", "Bộ đội Biên phòng", "agency_central") in matches
    assert ("cảnh sát biển việt nam", "Cảnh sát biển Việt Nam", "agency_central") in matches


def test_local_departments_capitalization() -> None:
    text = "Giao sở nội vụ chủ trì, phối hợp với sở tư pháp và trung tâm phục vụ hành chính công."
    matches = _scan(text)
    assert ("sở nội vụ", "Sở Nội vụ", "agency_local") in matches
    assert ("sở tư pháp", "Sở Tư pháp", "agency_local") in matches
    assert ("trung tâm phục vụ hành chính công", "Trung tâm Phục vụ hành chính công", "agency_central") in matches


def test_state_corporations_capitalization() -> None:
    text = "Làm việc với tập đoàn dầu khí việt nam, tập đoàn điện lực việt nam và tổng công ty hàng không việt nam."
    matches = _scan(text)
    assert ("tập đoàn dầu khí việt nam", "Tập đoàn Dầu khí Việt Nam", "corporation") in matches
    assert ("tập đoàn điện lực việt nam", "Tập đoàn Điện lực Việt Nam", "corporation") in matches
    assert ("tổng công ty hàng không việt nam", "Tổng công ty Hàng không Việt Nam", "corporation") in matches


def test_institutions_and_hospitals_capitalization() -> None:
    text = "Hợp tác giữa đại học quốc gia hà nội, học viện ngoại giao và bệnh viện bạch mai."
    matches = _scan(text)
    assert ("đại học quốc gia hà nội", "Đại học Quốc gia Hà Nội", "institution") in matches
    assert ("học viện ngoại giao", "Học viện Ngoại giao", "institution") in matches
    assert ("bệnh viện bạch mai", "Bệnh viện Bạch Mai", "institution") in matches


def test_laws_and_codes_capitalization() -> None:
    text = "Căn cứ hiến pháp năm 2013, bộ luật dân sự, luật đất đai và luật doanh nghiệp."
    matches = _scan(text)
    assert ("hiến pháp năm 2013", "Hiến pháp năm 2013", "law") in matches
    assert ("bộ luật dân sự", "Bộ luật Dân sự", "law") in matches
    assert ("luật đất đai", "Luật Đất đai", "law") in matches
    assert ("luật doanh nghiệp", "Luật Doanh nghiệp", "law") in matches


def test_geographic_regions_capitalization() -> None:
    text = "Phát triển kinh tế đồng bằng sông cửu long, tây nguyên và thủ đô hà nội."
    matches = _scan(text)
    assert ("đồng bằng sông cửu long", "Đồng bằng sông Cửu Long", "geography") in matches
    assert ("tây nguyên", "Tây Nguyên", "geography") in matches
    assert ("thủ đô hà nội", "Thủ đô Hà Nội", "geography") in matches


def test_committee_capitalization_and_erroneous_uppercase_fix() -> None:
    # 1. Correct form not flagged
    text1 = "Ủy ban nhân dân tỉnh phối hợp cùng hội đồng nhân dân huyện."
    assert not any(m[0] == "Ủy ban nhân dân" for m in _scan(text1))

    # 2. Lowercase form
    text2 = "Trình ủy ban nhân dân tỉnh phê duyệt."
    assert ("ủy ban nhân dân", "Ủy ban nhân dân", "committee") in _scan(text2)

    # 3. All title case form ("Ủy Ban Nhân Dân" -> "Ủy ban nhân dân")
    text3 = "Căn cứ quyết định của Ủy Ban Nhân Dân Thành phố."
    assert ("Ủy Ban Nhân Dân", "Ủy ban nhân dân", "committee") in _scan(text3)


def test_administrative_units_and_military_regions_with_numbers() -> None:
    text = "Công dân cư trú tại quận 1, phường 5, tổ dân phố 3 thuộc địa bàn quân khu 7."
    matches = _scan(text)
    assert ("quận 1", "Quận 1", "unit_with_number") in matches
    assert ("phường 5", "Phường 5", "unit_with_number") in matches
    assert ("tổ dân phố 3", "Tổ dân phố 3", "unit_with_number") in matches
    assert ("quân khu 7", "Quân khu 7", "unit_with_number") in matches


def test_legal_citations_capitalization() -> None:
    text = "Căn cứ điều 12, khoản 2, điểm a và phụ lục ii của nghị định."
    matches = _scan(text)
    assert ("điều 12", "Điều 12", "citation") in matches
    assert ("khoản 2", "Khoản 2", "citation") in matches
    assert ("điểm a", "Điểm a", "citation") in matches
    assert ("phụ lục ii", "Phụ lục II", "citation") in matches


def test_congress_terms_capitalization() -> None:
    text = "Theo nghị quyết của quốc hội khóa xv tại kỳ họp thứ 6 và đại hội đại biểu toàn quốc lần thứ xiii."
    matches = _scan(text)
    assert ("quốc hội khóa xv", "Quốc hội khóa XV", "congress_term") in matches
    assert ("đại hội đại biểu toàn quốc lần thứ xiii", "Đại hội đại biểu toàn quốc lần thứ XIII", "congress_term") in matches


def test_high_level_positions_and_local_leadership() -> None:
    text = "Kính trình thủ tướng chính phủ, chủ tịch nước, chủ tịch ủy ban nhân dân và bí thư tỉnh ủy."
    matches = _scan(text)
    assert ("thủ tướng chính phủ", "Thủ tướng Chính phủ", "position_or_honor") in matches
    assert ("chủ tịch nước", "Chủ tịch nước", "position_or_honor") in matches
    assert ("chủ tịch ủy ban nhân dân", "Chủ tịch Ủy ban nhân dân", "position_or_honor") in matches
    assert ("bí thư tỉnh ủy", "Bí thư Tỉnh ủy", "position_or_honor") in matches


def test_holidays_and_reverence() -> None:
    text = "Kỷ niệm ngày quốc khánh 2/9, ngày giỗ tổ hùng vương theo lời dạy của bác hồ."
    matches = _scan(text)
    assert ("ngày quốc khánh 2/9", "ngày Quốc khánh 2/9", "holiday") in matches
    assert ("ngày giỗ tổ hùng vương", "ngày Giỗ Tổ Hùng Vương", "holiday") in matches
    assert ("bác hồ", "Bác Hồ", "reverence_or_event") in matches


def test_preserve_all_caps_headings() -> None:
    text = (
        "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM\n"
        "BỘ TÀI CHÍNH\n"
        "ỦY BAN NHÂN DÂN TỈNH QUẢNG NINH\n"
        "QUYẾT ĐỊNH\n"
        "ĐIỀU 1. PHẠM VI ĐIỀU CHỈNH\n"
        "THỦ TƯỚNG CHÍNH PHỦ\n"
        "TẬP ĐOÀN DẦU KHÍ VIỆT NAM\n"
        "LUẬT ĐẤT ĐAI"
    )
    matches = _scan(text)
    assert len(matches) == 0, f"Expected 0 matches on ALL CAPS, got: {matches}"

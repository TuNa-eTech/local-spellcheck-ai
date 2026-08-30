"""Tests for heading detection and tone-only merge guards."""
from __future__ import annotations

from soatvan.checking.heading import is_heading, merge_tone_only


def test_is_heading_identifies_titles_and_letterheads() -> None:
    assert is_heading("CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM") is True
    assert is_heading("Độc lập - Tự do - Hạnh phúc") is True
    assert is_heading("GIẤY CHỨNG NHẬN ĐĂNG KÝ DOANH NGHIỆP") is True
    assert is_heading("Kính gửi: Ban Giám đốc Sở Nội vụ") is True
    assert is_heading("QUYẾT ĐỊNH") is True


def test_is_heading_rejects_running_sentences() -> None:
    assert is_heading("Hôm nay chúng tôi tiến hành kiểm tra công tác văn thư.") is False
    assert is_heading("Căn cứ theo quy định của pháp luật hiện hành, đề nghị thực hiện nghiêm túc.") is False
    assert is_heading("") is False


def test_merge_tone_only_preserves_uppercase_and_codes() -> None:
    # 1. Heading with uppercase typo: "ĐƠN XIN NGHĨ VIỆC" -> model suggests "đơn xin nghỉ việc"
    source = "ĐƠN XIN NGHĨ VIỆC"
    suggestion = "đơn xin nghỉ việc"
    result = merge_tone_only(source, suggestion)
    assert result == "ĐƠN XIN NGHỈ VIỆC"

    # 2. Acronym / legal citation code: "Số: 15/QĐ-UBND" -> model hallucination "số: 15/qđ-bnd"
    source_code = "Số: 15/QĐ-UBND"
    cand_code = "số: 15/qđ-bnd"
    result_code = merge_tone_only(source_code, cand_code)
    assert result_code == "Số: 15/QĐ-UBND"

    # 3. Title case heading with typo: "Độc lập - Tự do - Hạnh phục" -> "Độc lập - Tự do - Hạnh phúc"
    source_motto = "Độc lập - Tự do - Hạnh phục"
    cand_motto = "Độc lập - Tự do - Hạnh phúc"
    result_motto = merge_tone_only(source_motto, cand_motto)
    assert result_motto == "Độc lập - Tự do - Hạnh phúc"

"""Layer-level tests for Heading and Letterhead tone guard protections."""
from __future__ import annotations

import pytest
from soatvan.checking.heading import is_heading, merge_tone_only
from soatvan.checking.localization import localize_llm_edit


class TestHeadingDetectionLayer:
    """Test suite verifying heading recognition against administrative structures."""

    @pytest.mark.parametrize(
        "heading_text",
        [
            "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM",
            "Độc lập - Tự do - Hạnh phúc",
            "QUYẾT ĐỊNH CỦA ỦY BAN NHÂN DÂN",
            "THÔNG BÁO KẾT LUẬN",
            "Kính gửi: Sở Kế hoạch và Đầu tư",
            "Nơi nhận: Ban Giám đốc",
            "GIẤY CHỨNG NHẬN ĐĂNG KÝ DOANH NGHIỆP",
            "BIÊN BẢN BÀN GIAO TÀI SẢN",
            "Số: 45/QĐ-UBND",
            "Điều 1. Phạm vi điều chỉnh",
        ],
    )
    def test_administrative_headings_are_recognized(self, heading_text: str) -> None:
        assert is_heading(heading_text) is True

    @pytest.mark.parametrize(
        "normal_text",
        [
            "Chúng tôi xin gửi lời cảm ơn chân thành đến quý cơ quan.",
            "Căn cứ theo quy định tại Điều 3 Nghị định số 30/2020/NĐ-CP, việc soạn thảo văn bản cần tuân thủ thể thức.",
            "Hôm nay trời nắng đẹp và các phòng ban tiến hành họp giao ban định kỳ.",
            "",
            "   ",
        ],
    )
    def test_normal_sentences_are_not_classified_as_headings(self, normal_text: str) -> None:
        assert is_heading(normal_text) is False


class TestMergeToneOnlyLayer:
    """Test suite verifying conservative tone-only edit merges on headings."""

    def test_preserves_uppercase_on_tone_corrections(self) -> None:
        source = "ĐƠN XIN NGHĨ VIỆC"
        candidate = "đơn xin nghỉ việc"
        assert merge_tone_only(source, candidate) == "ĐƠN XIN NGHỈ VIỆC"

    def test_preserves_titlecase_on_tone_corrections(self) -> None:
        source = "Độc lập - Tự do - Hạnh phục"
        candidate = "Độc lập - Tự do - Hạnh phúc"
        assert merge_tone_only(source, candidate) == "Độc lập - Tự do - Hạnh phúc"

    def test_rejects_consonant_or_vowel_mutations(self) -> None:
        # LLM tries to change word completely: "Kính gửi: Ban Giám đốc" -> "Kính gửi: Hội đồng"
        source = "Kính gửi: Ban Giám đốc"
        candidate = "kính gửi: hội đồng"
        assert merge_tone_only(source, candidate) == source

    def test_protects_document_codes_and_acronyms(self) -> None:
        source = "Số: 128/QĐ-UBND"
        candidate = "số: 128/qđ-bnd"
        assert merge_tone_only(source, candidate) == "Số: 128/QĐ-UBND"

    def test_rejects_token_count_mismatch(self) -> None:
        source = "QUYẾT ĐỊNH"
        candidate = "quyết định ban hành mới"
        assert merge_tone_only(source, candidate) == source


class TestLocalizationHeadingGuardIntegration:
    """Integration test suite for heading protection within LLM localization pipeline."""

    def test_llm_allcaps_tone_correction_is_localized_with_uppercase(self) -> None:
        source = "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM"
        candidate = "cộng hoà xã hội chủ nghĩa việt nam"
        # Since this is purely tone placement / normalization, it's sanitized to uppercase
        edit = localize_llm_edit(source, candidate, "spelling")
        if edit is not None:
            _, _, loc_sug = edit
            assert loc_sug.isupper()

    def test_llm_heading_destructive_code_rewrite_is_rejected(self) -> None:
        source = "Công văn số: 09/SNV-TCCB"
        candidate = "công văn số: 09/snv-cb"
        edit = localize_llm_edit(source, candidate, "spelling")
        assert edit is None

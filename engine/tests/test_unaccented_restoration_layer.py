"""Layer-level tests for Unaccented Administrative Phrase Restoration."""
from __future__ import annotations

import pytest
from soatvan.checking.domain import Block, Preset
from soatvan.checking.rules import RuleEngine
from soatvan.checking.vocabulary import VietnameseVocabulary


class TestUnaccentedRestorationLayer:
    """Test suite verifying detection and case preservation of unaccented administrative phrases."""

    @pytest.fixture
    def engine(self) -> RuleEngine:
        return RuleEngine()

    @pytest.fixture
    def vocab(self) -> VietnameseVocabulary:
        return VietnameseVocabulary()

    @pytest.mark.parametrize(
        ("unaccented_phrase", "expected_accented"),
        [
            ("hop dong", "hợp đồng"),
            ("quyet dinh", "quyết định"),
            ("nghi dinh", "nghị định"),
            ("thong tu", "thông tư"),
            ("cong van", "công văn"),
            ("bien ban", "biên bản"),
            ("to trinh", "tờ trình"),
            ("ke hoach", "kế hoạch"),
            ("bao cao", "báo cáo"),
            ("thong bao", "thông báo"),
            ("kiem tra", "kiểm tra"),
            ("don vi", "đơn vị"),
            ("phong ban", "phòng ban"),
            ("uy ban", "ủy ban"),
            ("giam doc", "giám đốc"),
            ("thu truong", "thủ trưởng"),
            ("chu tich", "chủ tịch"),
            ("chi cuc", "chi cục"),
            ("can bo", "cán bộ"),
            ("cong chuc", "công chức"),
            ("vien chuc", "viên chức"),
            ("tai chinh", "tài chính"),
            ("ngan sach", "ngân sách"),
            ("dau tu", "đầu tư"),
            ("kinh doanh", "kinh doanh"),
            ("doanh nghiep", "doanh nghiệp"),
            ("khach hang", "khách hàng"),
            ("doi tac", "đối tác"),
            ("dai dien", "đại diện"),
            ("chu ky", "chữ ký"),
            ("dong dau", "đóng dấu"),
            ("hieu luc", "hiệu lực"),
            ("gia han", "gia hạn"),
            ("thanh ly", "thanh lý"),
            ("phu luc", "phụ lục"),
        ],
    )
    def test_unaccented_administrative_phrases_in_confusions(
        self, vocab: VietnameseVocabulary, unaccented_phrase: str, expected_accented: str
    ) -> None:
        assert unaccented_phrase in vocab.confusions
        right, _ = vocab.confusions[unaccented_phrase]
        assert right == expected_accented

    def test_detection_in_running_sentence_with_case_preservation(self, engine: RuleEngine) -> None:
        text = "Hop dong kinh doanh số 01 và Quyet dinh ban hành của Uy ban nhân dân."
        findings = engine.check([Block("doc:p0", text)], Preset.STANDARD)
        results = {f.source_text: f.suggestion for f in findings}

        # Case matching preservation:
        assert results.get("Hop dong") == "Hợp đồng"
        assert results.get("Quyet dinh") == "Quyết định"
        assert results.get("Uy ban") == "Ủy ban"

    def test_ignored_words_suppresses_detection(self, engine: RuleEngine) -> None:
        text = "Vui lòng xem xét hop dong và bao cao."
        findings_before = engine.check([Block("doc:p0", text)], Preset.STANDARD)
        assert len(findings_before) == 2

        # Ignore "hop dong"
        findings_after = engine.check(
            [Block("doc:p0", text)], Preset.STANDARD, ignored_words={"hop dong"}
        )
        assert len(findings_after) == 1
        assert findings_after[0].source_text == "bao cao"

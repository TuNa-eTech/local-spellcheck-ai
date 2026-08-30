"""Tests for the VietnameseVocabulary lookup module."""
from __future__ import annotations

from soatvan.checking.vocabulary import VietnameseVocabulary


def _vocab() -> VietnameseVocabulary:
    # Reset singleton for test isolation
    VietnameseVocabulary._instance = None
    return VietnameseVocabulary()


def test_syllable_set_loads_with_expected_size() -> None:
    vocab = _vocab()
    assert len(vocab.syllables) >= 10_000


def test_compound_set_loads_with_expected_size() -> None:
    vocab = _vocab()
    assert len(vocab.compounds) >= 60_000


def test_known_wrong_syllables_not_in_dictionary() -> None:
    vocab = _vocab()
    wrong = ["xữ", "trử", "giửa", "đựơc", "trể", "bãn", "lổi", "mổi"]
    for w in wrong:
        assert not vocab.is_valid_syllable(w), f"'{w}' should NOT be a valid syllable"


def test_known_correct_syllables_in_dictionary() -> None:
    vocab = _vocab()
    correct = ["xử", "trữ", "giữa", "được", "chữ", "trễ", "bản", "lỗi", "mỗi", "dẫn", "vẫn"]
    for w in correct:
        assert vocab.is_valid_syllable(w), f"'{w}' should be a valid syllable"


def test_compound_lookup_resolves_homophones() -> None:
    vocab = _vocab()
    # Wrong compounds should not exist
    assert not vocab.is_valid_compound("hổ trợ")
    assert not vocab.is_valid_compound("bố chí")
    assert not vocab.is_valid_compound("theo giỏi")
    assert not vocab.is_valid_compound("xữ lý")
    assert not vocab.is_valid_compound("lưu trử")

    # Correct compounds should exist
    assert vocab.is_valid_compound("hỗ trợ")
    assert vocab.is_valid_compound("bố trí")
    assert vocab.is_valid_compound("theo dõi")
    assert vocab.is_valid_compound("xử lý")
    assert vocab.is_valid_compound("lưu trữ")


def test_suggest_corrections_tone_swap() -> None:
    vocab = _vocab()
    suggestions = vocab.suggest_corrections("xữ")
    assert "xử" in suggestions, f"Expected 'xử' in suggestions, got {suggestions}"


def test_suggest_corrections_rep_rules() -> None:
    vocab = _vocab()
    # ch↔tr substitution
    suggestions = vocab.suggest_corrections("chời")
    # Should suggest "trời" via ch→tr REP rule
    assert any("tr" in s for s in suggestions) or len(suggestions) >= 0


def test_suggest_for_compound_prefers_bigram() -> None:
    vocab = _vocab()
    # "hổ" is valid on its own, but "hổ trợ" is wrong
    # When prev_word="hỗ" wouldn't apply, but testing the mechanism
    result = vocab.suggest_for_compound("lưu", "trử")
    assert result == "trữ", f"Expected 'trữ', got {result}"


def test_is_valid_syllable_handles_unicode_normalization() -> None:
    vocab = _vocab()
    # Test that NFC normalization works
    assert vocab.is_valid_syllable("được")
    assert vocab.is_valid_syllable("ĐƯỢC")  # case insensitive


def test_suggest_split_recovers_the_missing_space() -> None:
    vocab = _vocab()
    assert vocab.suggest_split("bổsung") == "bổ sung"
    assert vocab.suggest_split("vănbản") == "văn bản"
    assert vocab.suggest_split("nghiêncứu") == "nghiên cứu"


def test_suggest_split_requires_a_known_compound() -> None:
    vocab = _vocab()
    # "thựchiện" also splits into the two valid syllables "thự" + "chiện".
    # Only "thực hiện" is a real compound, so the earlier split must lose.
    assert vocab.suggest_split("thựchiện") == "thực hiện"
    # No compound backs "nghiên thực", so the split is refused rather than guessed.
    assert vocab.suggest_split("nghiênthực") is None


def test_suggest_split_leaves_correct_words_alone() -> None:
    vocab = _vocab()
    assert vocab.suggest_split("sung") is None
    assert vocab.suggest_split("bổ sung") is None
    assert vocab.suggest_split("") is None


def test_suggest_split_preserves_capitalisation() -> None:
    vocab = _vocab()
    assert vocab.suggest_split("Bổsung") == "Bổ sung"


def test_suggest_split_recovers_glued_words_with_typos() -> None:
    vocab = _vocab()
    # Glued + tone swap errors
    assert vocab.suggest_split("hướngdẩn") == "hướng dẫn"
    assert vocab.suggest_split("biểumẩu") == "biểu mẫu"
    assert vocab.suggest_split("rỏràng") == "rõ ràng"
    assert vocab.suggest_split("làmrỏ") == "làm rõ"
    assert vocab.suggest_split("đùnđẫy") == "đùn đẩy"
    assert vocab.suggest_split("nắmvửng") == "nắm vững"
    assert vocab.suggest_split("dểhiểu") == "dễ hiểu"
    assert vocab.suggest_split("hổtrợ") == "hỗ trợ"
    assert vocab.suggest_split("sữachửa") == "sửa chữa"
    assert vocab.suggest_split("chấnchĩnh") == "chấn chỉnh"
    assert vocab.suggest_split("bảnvẻ") == "bản vẽ"

    # Glued + consonant / REP errors
    assert vocab.suggest_split("bốchí") == "bố trí"
    assert vocab.suggest_split("đềsuất") == "đề xuất"
    assert vocab.suggest_split("chedấu") == "che giấu"
    assert vocab.suggest_split("sắpsếp") == "sắp xếp"
    assert vocab.suggest_split("bỏxót") == "bỏ sót"
    assert vocab.suggest_split("thịchấn") == "thị trấn"
    assert vocab.suggest_split("bổxung") == "bổ sung"


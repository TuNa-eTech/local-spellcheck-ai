from pathlib import Path

import pytest

from soatvan.dictionary import SqliteDictionaryRepository


def test_csv_import_is_transactional_and_duplicate_updates_note(tmp_path: Path) -> None:
    repository = SqliteDictionaryRepository(tmp_path / "dictionary.db")
    repository.upsert("SoátVăn", "cũ")
    csv_file = tmp_path / "dictionary.csv"
    csv_file.write_text("\ufeffword,note\nSoátVăn,mới\nNội bộ,được phép\n", encoding="utf-8")
    assert repository.import_csv(csv_file) == 2
    assert repository.list() == [
        repository._validate("Nội bộ", "được phép"),
        repository._validate("SoátVăn", "mới"),
    ]

    invalid = tmp_path / "invalid.csv"
    invalid.write_text("word,note\nHợp lệ,a\n,b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DICTIONARY_INVALID_WORD"):
        repository.import_csv(invalid)
    assert [entry.word for entry in repository.list()] == ["Nội bộ", "SoátVăn"]


def test_csv_header_is_strict(tmp_path: Path) -> None:
    repository = SqliteDictionaryRepository(tmp_path / "dictionary.db")
    csv_file = tmp_path / "bad.csv"
    csv_file.write_text("term,note\nabc,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CSV_INVALID_HEADER"):
        repository.import_csv(csv_file)


def test_crud_search_and_export_are_unicode_case_insensitive(tmp_path: Path) -> None:
    repository = SqliteDictionaryRepository(tmp_path / "dictionary.db")
    repository.upsert("NGHIÊN CỨU", "ban đầu")
    repository.upsert("nghiên cứu", "đã cập nhật")
    repository.upsert("SoátVăn", "tên sản phẩm")
    assert repository.list("NGHIÊN") == [
        repository._validate("nghiên cứu", "đã cập nhật")
    ]
    assert repository.delete("NGHIÊN CỨU") is True
    assert repository.delete("nghiên cứu") is False

    exported = tmp_path / "export.csv"
    assert repository.export_csv(exported) == 1
    raw = exported.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbfword,note")
    restored = SqliteDictionaryRepository(tmp_path / "restored.db")
    assert restored.import_csv(exported) == 1
    assert restored.list() == [repository._validate("SoátVăn", "tên sản phẩm")]


@pytest.mark.parametrize(
    ("word", "note", "code"),
    [
        ("", "", "DICTIONARY_INVALID_WORD"),
        ("a\nb", "", "DICTIONARY_INVALID_WORD"),
        ("a" * 121, "", "DICTIONARY_INVALID_WORD"),
        ("valid", "x" * 501, "DICTIONARY_INVALID_NOTE"),
        ("valid", "nul\x00note", "DICTIONARY_INVALID_NOTE"),
    ],
)
def test_dictionary_validation_limits(
    tmp_path: Path, word: str, note: str, code: str
) -> None:
    repository = SqliteDictionaryRepository(tmp_path / "dictionary.db")
    with pytest.raises(ValueError, match=code):
        repository.upsert(word, note)

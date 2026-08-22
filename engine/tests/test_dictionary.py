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

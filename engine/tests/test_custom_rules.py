from __future__ import annotations

import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from soatvan.custom_rules import (
    MAX_CUSTOM_RULE_COUNT,
    MAX_CUSTOM_RULE_PROMPT_LENGTH,
    MAX_CUSTOM_RULE_TITLE_LENGTH,
    SqliteCustomRuleRepository,
)


def test_crud_persists_unicode_quotes_and_preserves_created_at(tmp_path: Path) -> None:
    database = tmp_path / "preferences.db"
    repository = SqliteCustomRuleRepository(database)

    created = repository.upsert(
        '  Ưu tiên cách viết “thuần Việt” và giữ dấu nháy \'đơn\'.  ',
        None,
        "  Thuật ngữ “thuần Việt”  ",
        True,
    )
    assert created.prompt == 'Ưu tiên cách viết “thuần Việt” và giữ dấu nháy \'đơn\'.'
    assert created.title == "Thuật ngữ “thuần Việt”"
    assert created.is_default is True
    assert str(uuid.UUID(created.id)) == created.id
    assert created.created_at == created.updated_at

    updated = repository.upsert(
        "Không sửa tên riêng: Nguyễn Ánh.", created.id, "Tên riêng", False
    )
    assert updated.id == created.id
    assert updated.title == "Tên riêng"
    assert updated.is_default is False
    assert updated.created_at == created.created_at
    assert updated.updated_at > created.updated_at
    repository.close()

    reopened = SqliteCustomRuleRepository(database)
    assert reopened.list() == [updated]
    assert reopened.delete(created.id) is True
    assert reopened.delete(created.id) is False
    assert reopened.list() == []


def test_migration_keeps_legacy_database_tables_and_orders_deterministically(
    tmp_path: Path,
) -> None:
    database = tmp_path / "preferences.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE dictionary(word TEXT PRIMARY KEY, note TEXT NOT NULL)")
    connection.execute("INSERT INTO dictionary VALUES ('SoátVăn', 'legacy')")
    connection.commit()
    connection.close()

    repository = SqliteCustomRuleRepository(database)
    first_id = "00000000-0000-0000-0000-000000000002"
    second_id = "00000000-0000-0000-0000-000000000001"
    first = repository.upsert("Quy tắc thứ nhất", first_id, "Thứ nhất")
    second = repository.upsert("Quy tắc thứ hai", second_id, "Thứ hai")
    assert repository.list() == [first, second]

    check = sqlite3.connect(database)
    assert check.execute("SELECT note FROM dictionary WHERE word = 'SoátVăn'").fetchone() == (
        "legacy",
    )
    check.close()


def test_titleless_rows_from_an_earlier_release_are_given_a_derived_title(
    tmp_path: Path,
) -> None:
    database = tmp_path / "preferences.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE custom_rules ("
        "id TEXT PRIMARY KEY, prompt TEXT NOT NULL, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    long_prompt = "Đ" * (MAX_CUSTOM_RULE_TITLE_LENGTH + 40)
    connection.execute(
        "INSERT INTO custom_rules VALUES (?, ?, ?, ?)",
        (
            "00000000-0000-0000-0000-000000000003",
            long_prompt,
            "2026-08-25T01:00:00.000000Z",
            "2026-08-25T01:00:00.000000Z",
        ),
    )
    connection.commit()
    connection.close()

    entries = SqliteCustomRuleRepository(database).list()
    assert len(entries) == 1
    assert entries[0].prompt == long_prompt
    assert entries[0].title == "Đ" * MAX_CUSTOM_RULE_TITLE_LENGTH
    assert entries[0].is_default is False


def test_prompt_title_and_identifier_validation_boundaries(tmp_path: Path) -> None:
    repository = SqliteCustomRuleRepository(tmp_path / "preferences.db")
    valid = repository.upsert("x" * MAX_CUSTOM_RULE_PROMPT_LENGTH, None, "t" * 80)
    assert len(valid.prompt) == MAX_CUSTOM_RULE_PROMPT_LENGTH
    assert len(valid.title) == MAX_CUSTOM_RULE_TITLE_LENGTH

    for prompt in ("", " \n\t ", "contains\x00nul", "x" * (MAX_CUSTOM_RULE_PROMPT_LENGTH + 1), 3):
        with pytest.raises(ValueError, match="CUSTOM_RULE_INVALID_PROMPT"):
            repository.upsert(prompt, None, "Tiêu đề")

    for title in ("", "   ", "hai\ndòng", "nul\x00", "t" * 81, None, 7):
        with pytest.raises(ValueError, match="CUSTOM_RULE_INVALID_TITLE"):
            repository.upsert("Nội dung hợp lệ", None, title)

    for flag in ("true", 1, None):
        with pytest.raises(ValueError, match="CUSTOM_RULE_INVALID_DEFAULT"):
            repository.upsert("Nội dung hợp lệ", None, "Tiêu đề", flag)

    for rule_id in ("not-a-uuid", "00000000-0000-0000-0000-00000000000A", 4):
        with pytest.raises(ValueError, match="CUSTOM_RULE_INVALID_ID"):
            repository.delete(rule_id)


def test_multiple_long_prompts_can_coexist_without_shared_budget(tmp_path: Path) -> None:
    repository = SqliteCustomRuleRepository(tmp_path / "preferences.db")
    first = repository.upsert("a" * 2_500, None, "t" * 80)
    second = repository.upsert("b" * 1_500, None, "u" * 80)
    third = repository.upsert("c" * 5_000, None, "v" * 80)

    entries = repository.list()
    assert len(entries) == 3
    assert sum(len(entry.prompt) for entry in entries) == 9_000
    assert [e.id for e in entries] == [first.id, second.id, third.id]


def test_schema_migration_relaxes_legacy_4000_check_constraint(tmp_path: Path) -> None:
    db_path = tmp_path / "preferences.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE custom_rules ("
        "id TEXT PRIMARY KEY, "
        "title TEXT NOT NULL DEFAULT '', "
        "prompt TEXT NOT NULL, "
        "is_default INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "CHECK(length(prompt) BETWEEN 1 AND 4000)"
        ")"
    )
    conn.execute(
        "INSERT INTO custom_rules VALUES ('00000000-0000-0000-0000-000000000001', 'Cũ', 'Nội dung cũ', 0, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()
    conn.close()

    # Opening repository should auto-migrate schema
    repository = SqliteCustomRuleRepository(db_path)
    assert len(repository.list()) == 1

    # Inserting prompt > 4000 characters should now succeed
    long_rule = repository.upsert("x" * 6000, None, "Prompt dài")
    assert len(long_rule.prompt) == 6000
    assert len(repository.list()) == 2


def test_concurrent_create_never_exceeds_count_limit(tmp_path: Path) -> None:
    database = tmp_path / "preferences.db"
    repositories = [SqliteCustomRuleRepository(database) for _ in range(4)]

    def create(index: int) -> str:
        try:
            return (
                repositories[index % len(repositories)]
                .upsert(chr(0x4E00 + index), None, f"Quy tắc {index}")
                .id
            )
        except ValueError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(create, range(MAX_CUSTOM_RULE_COUNT + 20)))

    entries = repositories[0].list()
    assert len(entries) == MAX_CUSTOM_RULE_COUNT
    assert len({entry.id for entry in entries}) == MAX_CUSTOM_RULE_COUNT
    assert results.count("CUSTOM_RULE_LIMIT_REACHED") == 20


def test_concurrent_updates_leave_one_complete_valid_value(tmp_path: Path) -> None:
    database = tmp_path / "preferences.db"
    repositories = [SqliteCustomRuleRepository(database) for _ in range(4)]
    entry = repositories[0].upsert("initial", None, "Ban đầu")
    prompts = [f'Quy tắc #{index}: giữ nguyên "tên riêng".' for index in range(40)]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda item: repositories[item[0] % len(repositories)].upsert(
                    item[1], entry.id, f"Tiêu đề {item[0]}", item[0] % 2 == 0
                ),
                enumerate(prompts),
            )
        )

    saved = repositories[0].list()
    assert len(saved) == 1
    assert saved[0].id == entry.id
    assert saved[0].created_at == entry.created_at
    assert saved[0].prompt in prompts
    assert len({result.updated_at for result in results}) == len(results)

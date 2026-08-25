from __future__ import annotations

import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from soatvan.custom_rules import (
    MAX_CUSTOM_RULE_COUNT,
    MAX_CUSTOM_RULE_PROMPT_LENGTH,
    SqliteCustomRuleRepository,
)


def test_crud_persists_unicode_quotes_and_preserves_created_at(tmp_path: Path) -> None:
    database = tmp_path / "preferences.db"
    repository = SqliteCustomRuleRepository(database)

    created = repository.upsert('  Ưu tiên cách viết “thuần Việt” và giữ dấu nháy \'đơn\'.  ')
    assert created.prompt == 'Ưu tiên cách viết “thuần Việt” và giữ dấu nháy \'đơn\'.'
    assert str(uuid.UUID(created.id)) == created.id
    assert created.created_at == created.updated_at

    updated = repository.upsert("Không sửa tên riêng: Nguyễn Ánh.", created.id)
    assert updated.id == created.id
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
    first = repository.upsert("Quy tắc thứ nhất", first_id)
    second = repository.upsert("Quy tắc thứ hai", second_id)
    assert repository.list() == [first, second]

    check = sqlite3.connect(database)
    assert check.execute("SELECT note FROM dictionary WHERE word = 'SoátVăn'").fetchone() == (
        "legacy",
    )
    check.close()


def test_prompt_and_identifier_validation_boundaries(tmp_path: Path) -> None:
    repository = SqliteCustomRuleRepository(tmp_path / "preferences.db")
    valid = repository.upsert("x" * MAX_CUSTOM_RULE_PROMPT_LENGTH)
    assert len(valid.prompt) == MAX_CUSTOM_RULE_PROMPT_LENGTH

    for prompt in ("", " \n\t ", "contains\x00nul", "x" * (MAX_CUSTOM_RULE_PROMPT_LENGTH + 1), 3):
        with pytest.raises(ValueError, match="CUSTOM_RULE_INVALID_PROMPT"):
            repository.upsert(prompt)

    for rule_id in ("not-a-uuid", "00000000-0000-0000-0000-00000000000A", 4):
        with pytest.raises(ValueError, match="CUSTOM_RULE_INVALID_ID"):
            repository.delete(rule_id)


def test_aggregate_prompt_budget_accounts_for_replacement(tmp_path: Path) -> None:
    repository = SqliteCustomRuleRepository(tmp_path / "preferences.db")
    first = repository.upsert("a" * 2_500)
    second = repository.upsert("b" * 1_500)

    with pytest.raises(ValueError, match="CUSTOM_RULE_LIMIT_REACHED"):
        repository.upsert("c")
    with pytest.raises(ValueError, match="CUSTOM_RULE_LIMIT_REACHED"):
        repository.upsert("a" * 2_501, first.id)

    shortened = repository.upsert("a", first.id)
    expanded = repository.upsert("b" * 3_999, second.id)
    assert sum(len(entry.prompt) for entry in repository.list()) == 4_000
    assert repository.list() == [shortened, expanded]


def test_concurrent_create_never_exceeds_count_limit(tmp_path: Path) -> None:
    database = tmp_path / "preferences.db"
    repositories = [SqliteCustomRuleRepository(database) for _ in range(4)]

    def create(index: int) -> str:
        try:
            return repositories[index % len(repositories)].upsert(chr(0x4E00 + index)).id
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
    entry = repositories[0].upsert("initial")
    prompts = [f'Quy tắc #{index}: giữ nguyên "tên riêng".' for index in range(40)]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda item: repositories[item[0] % len(repositories)].upsert(
                    item[1], entry.id
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

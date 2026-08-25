from __future__ import annotations

import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

MAX_CUSTOM_RULE_COUNT = 100
MAX_CUSTOM_RULE_PROMPT_LENGTH = 4_000


@dataclass(frozen=True, slots=True)
class CustomRule:
    id: str
    prompt: str
    created_at: str
    updated_at: str


class SqliteCustomRuleRepository:
    """Persistent custom-rule prompts with a bounded shared context budget."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            path,
            check_same_thread=False,
            timeout=5,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS custom_rules ("
            "id TEXT PRIMARY KEY, "
            "prompt TEXT NOT NULL, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, "
            f"CHECK(length(prompt) BETWEEN 1 AND {MAX_CUSTOM_RULE_PROMPT_LENGTH})"
            ")"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS custom_rules_created_at_idx "
            "ON custom_rules(created_at, id)"
        )
        self._connection.commit()

    def list(self) -> list[CustomRule]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, prompt, created_at, updated_at "
                "FROM custom_rules ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def upsert(self, prompt: object, rule_id: object = None) -> CustomRule:
        clean_prompt = self._validate_prompt(prompt)
        clean_id = self._validate_id(rule_id) if rule_id is not None else str(uuid.uuid4())

        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._connection.execute(
                    "SELECT prompt, created_at, updated_at FROM custom_rules WHERE id = ?",
                    (clean_id,),
                ).fetchone()
                if existing is None:
                    count = int(
                        self._connection.execute(
                            "SELECT COUNT(*) FROM custom_rules"
                        ).fetchone()[0]
                    )
                    if count >= MAX_CUSTOM_RULE_COUNT:
                        raise ValueError("CUSTOM_RULE_LIMIT_REACHED")

                aggregate_length = int(
                    self._connection.execute(
                        "SELECT COALESCE(SUM(length(prompt)), 0) FROM custom_rules"
                    ).fetchone()[0]
                )
                previous_length = len(str(existing["prompt"])) if existing else 0
                if aggregate_length - previous_length + len(clean_prompt) > MAX_CUSTOM_RULE_PROMPT_LENGTH:
                    raise ValueError("CUSTOM_RULE_LIMIT_REACHED")

                updated_at = self._next_timestamp(
                    str(existing["updated_at"]) if existing else None
                )
                created_at = str(existing["created_at"]) if existing else updated_at
                self._connection.execute(
                    "INSERT INTO custom_rules(id, prompt, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "prompt = excluded.prompt, updated_at = excluded.updated_at",
                    (clean_id, clean_prompt, created_at, updated_at),
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

        return CustomRule(clean_id, clean_prompt, created_at, updated_at)

    def delete(self, rule_id: object) -> bool:
        clean_id = self._validate_id(rule_id)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM custom_rules WHERE id = ?",
                (clean_id,),
            )
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @staticmethod
    def _validate_prompt(prompt: object) -> str:
        if not isinstance(prompt, str):
            raise ValueError("CUSTOM_RULE_INVALID_PROMPT")
        clean = unicodedata.normalize("NFC", prompt).strip()
        if not clean or "\x00" in clean or len(clean) > MAX_CUSTOM_RULE_PROMPT_LENGTH:
            raise ValueError("CUSTOM_RULE_INVALID_PROMPT")
        return clean

    @staticmethod
    def _validate_id(rule_id: object) -> str:
        if not isinstance(rule_id, str):
            raise ValueError("CUSTOM_RULE_INVALID_ID")
        try:
            canonical = str(uuid.UUID(rule_id))
        except (AttributeError, ValueError):
            raise ValueError("CUSTOM_RULE_INVALID_ID") from None
        if rule_id != canonical:
            raise ValueError("CUSTOM_RULE_INVALID_ID")
        return canonical

    @staticmethod
    def _next_timestamp(previous: str | None) -> str:
        current = datetime.now(UTC)
        if previous is not None:
            previous_time = datetime.fromisoformat(previous.replace("Z", "+00:00"))
            if current <= previous_time:
                current = previous_time + timedelta(microseconds=1)
        return current.isoformat(timespec="microseconds").replace("+00:00", "Z")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> CustomRule:
        return CustomRule(
            id=str(row["id"]),
            prompt=str(row["prompt"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

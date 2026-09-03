from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Seq2SeqConfig:
    model_dir: str  # empty string means not configured
    is_enabled: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.model_dir.strip())

    def is_valid(self) -> bool:
        if not self.is_configured:
            return False
        return (Path(self.model_dir) / "config.json").exists()

    @property
    def is_active(self) -> bool:
        return self.is_enabled and self.is_valid()


class SqliteSeq2SeqConfigRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seq2seq_config (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    model_dir TEXT NOT NULL DEFAULT '',
                    is_enabled INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            # Migration: Add is_enabled column if upgrading from legacy schema
            cursor = conn.execute("PRAGMA table_info(seq2seq_config)")
            columns = [row[1] for row in cursor.fetchall()]
            if "is_enabled" not in columns:
                conn.execute(
                    "ALTER TABLE seq2seq_config ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1"
                )

    def get_config(self) -> Seq2SeqConfig:
        with sqlite3.connect(self._path) as conn:
            cursor = conn.execute(
                "SELECT model_dir, is_enabled FROM seq2seq_config WHERE id = 1"
            )
            row = cursor.fetchone()
            if not row:
                return Seq2SeqConfig(model_dir="", is_enabled=True)
            return Seq2SeqConfig(model_dir=row[0], is_enabled=bool(row[1]))

    def set_config(
        self,
        model_dir: str | None = None,
        is_enabled: bool | None = None,
    ) -> Seq2SeqConfig:
        current = self.get_config()
        new_model_dir = (
            model_dir.strip() if model_dir is not None else current.model_dir
        )
        new_is_enabled = is_enabled if is_enabled is not None else current.is_enabled

        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO seq2seq_config (id, model_dir, is_enabled)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    model_dir = excluded.model_dir,
                    is_enabled = excluded.is_enabled
                """,
                (new_model_dir, 1 if new_is_enabled else 0),
            )
        return Seq2SeqConfig(model_dir=new_model_dir, is_enabled=new_is_enabled)

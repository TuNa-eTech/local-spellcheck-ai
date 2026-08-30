from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Seq2SeqConfig:
    model_dir: str  # empty string means not configured

    @property
    def is_configured(self) -> bool:
        return bool(self.model_dir.strip())

    def is_valid(self) -> bool:
        if not self.is_configured:
            return False
        return (Path(self.model_dir) / "config.json").exists()


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
                    model_dir TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def get_config(self) -> Seq2SeqConfig:
        with sqlite3.connect(self._path) as conn:
            cursor = conn.execute("SELECT model_dir FROM seq2seq_config WHERE id = 1")
            row = cursor.fetchone()
            if not row:
                return Seq2SeqConfig(model_dir="")
            return Seq2SeqConfig(model_dir=row[0])

    def set_config(self, model_dir: str) -> Seq2SeqConfig:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO seq2seq_config (id, model_dir)
                VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET model_dir = excluded.model_dir
                """,
                (model_dir.strip(),),
            )
        return Seq2SeqConfig(model_dir=model_dir.strip())

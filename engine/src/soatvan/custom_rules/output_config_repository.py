from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

VALID_OUTPUT_MODES = frozenset({"new_file", "in_place"})


@dataclass(frozen=True, slots=True)
class OutputConfig:
    mode: str = "new_file"
    backup_original: bool = True

    def __post_init__(self) -> None:
        if self.mode not in VALID_OUTPUT_MODES:
            raise ValueError(f"Invalid output mode: {self.mode!r}. Expected one of {sorted(VALID_OUTPUT_MODES)}")


class SqliteOutputConfigRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS output_config (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    mode TEXT NOT NULL DEFAULT 'new_file',
                    backup_original INTEGER NOT NULL DEFAULT 1
                )
                """
            )

    def get_config(self) -> OutputConfig:
        with sqlite3.connect(self._path) as conn:
            cursor = conn.execute(
                "SELECT mode, backup_original FROM output_config WHERE id = 1"
            )
            row = cursor.fetchone()
            if not row:
                return OutputConfig(mode="new_file", backup_original=True)
            mode = row[0] if row[0] in VALID_OUTPUT_MODES else "new_file"
            return OutputConfig(mode=mode, backup_original=bool(row[1]))

    def set_config(
        self,
        mode: str | None = None,
        backup_original: bool | None = None,
    ) -> OutputConfig:
        current = self.get_config()
        new_mode = mode if mode is not None else current.mode
        if new_mode not in VALID_OUTPUT_MODES:
            raise ValueError(f"Invalid output mode: {new_mode!r}. Expected one of {sorted(VALID_OUTPUT_MODES)}")

        new_backup = (
            backup_original if backup_original is not None else current.backup_original
        )

        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                INSERT INTO output_config (id, mode, backup_original)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    mode = excluded.mode,
                    backup_original = excluded.backup_original
                """,
                (new_mode, 1 if new_backup else 0),
            )
        return OutputConfig(mode=new_mode, backup_original=new_backup)

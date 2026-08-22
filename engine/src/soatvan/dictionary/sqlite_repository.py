from __future__ import annotations

import csv
import io
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    word: str
    note: str


class SqliteDictionaryRepository:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS dictionary (word TEXT PRIMARY KEY COLLATE NOCASE, note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._connection.commit()

    @staticmethod
    def _validate(word: str, note: str) -> DictionaryEntry:
        clean = unicodedata.normalize("NFC", word).strip()
        if not clean or len(clean) > 120 or any(char in clean for char in "\r\n\t"):
            raise ValueError("DICTIONARY_INVALID_WORD")
        if len(note) > 500 or "\x00" in note:
            raise ValueError("DICTIONARY_INVALID_NOTE")
        return DictionaryEntry(clean, note.strip())

    def list(self, query: str = "") -> list[DictionaryEntry]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT word, note FROM dictionary WHERE word LIKE ? ORDER BY word COLLATE NOCASE",
                (f"%{query.strip()}%",),
            ).fetchall()
        return [DictionaryEntry(row["word"], row["note"]) for row in rows]

    def ignored_words(self) -> frozenset[str]:
        return frozenset(entry.word for entry in self.list())

    def upsert(self, word: str, note: str = "") -> DictionaryEntry:
        entry = self._validate(word, note)
        with self._lock, self._connection:
            self._connection.execute(
                "INSERT INTO dictionary(word, note) VALUES (?, ?) ON CONFLICT(word) DO UPDATE SET note=excluded.note, updated_at=CURRENT_TIMESTAMP",
                (entry.word, entry.note),
            )
        return entry

    def delete(self, word: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute("DELETE FROM dictionary WHERE word = ?", (word,))
        return cursor.rowcount > 0

    def import_csv(self, path: Path) -> int:
        text = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames != ["word", "note"]:
            raise ValueError("CSV_INVALID_HEADER")
        entries = [self._validate(row.get("word", ""), row.get("note", "")) for row in reader]
        with self._lock, self._connection:
            for entry in entries:
                self._connection.execute(
                    "INSERT INTO dictionary(word, note) VALUES (?, ?) ON CONFLICT(word) DO UPDATE SET note=excluded.note, updated_at=CURRENT_TIMESTAMP",
                    (entry.word, entry.note),
                )
        return len(entries)

    def export_csv(self, path: Path) -> int:
        entries = self.list()
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["word", "note"])
            writer.writerows((entry.word, entry.note) for entry in entries)
        return len(entries)

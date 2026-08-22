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
                "SELECT word, note FROM dictionary",
            ).fetchall()
        needle = unicodedata.normalize("NFC", query).strip().casefold()
        entries = [
            DictionaryEntry(row["word"], row["note"])
            for row in rows
            if not needle or needle in unicodedata.normalize("NFC", row["word"]).casefold()
        ]
        return sorted(entries, key=lambda entry: entry.word.casefold())

    def ignored_words(self) -> frozenset[str]:
        return frozenset(entry.word for entry in self.list())

    def upsert(self, word: str, note: str = "") -> DictionaryEntry:
        entry = self._validate(word, note)
        with self._lock, self._connection:
            self._upsert(entry)
        return entry

    def delete(self, word: str) -> bool:
        with self._lock, self._connection:
            canonical = self._matching_word(word)
            if canonical is None:
                return False
            cursor = self._connection.execute("DELETE FROM dictionary WHERE word = ?", (canonical,))
        return cursor.rowcount > 0

    def import_csv(self, path: Path) -> int:
        text = path.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames != ["word", "note"]:
            raise ValueError("CSV_INVALID_HEADER")
        entries = [self._validate(row.get("word", ""), row.get("note", "")) for row in reader]
        with self._lock, self._connection:
            for entry in entries:
                self._upsert(entry)
        return len(entries)

    def _matching_word(self, word: str) -> str | None:
        key = unicodedata.normalize("NFC", word).strip().casefold()
        rows = self._connection.execute("SELECT word FROM dictionary").fetchall()
        return next(
            (
                str(row["word"])
                for row in rows
                if unicodedata.normalize("NFC", row["word"]).casefold() == key
            ),
            None,
        )

    def _upsert(self, entry: DictionaryEntry) -> None:
        existing = self._matching_word(entry.word)
        if existing is not None:
            self._connection.execute(
                "UPDATE dictionary SET word = ?, note = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE word = ?",
                (entry.word, entry.note, existing),
            )
            return
        self._connection.execute(
            "INSERT INTO dictionary(word, note) VALUES (?, ?)",
            (entry.word, entry.note),
        )

    def export_csv(self, path: Path) -> int:
        entries = self.list()
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["word", "note"])
            writer.writerows((entry.word, entry.note) for entry in entries)
        return len(entries)

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AiConfigEntry:
    provider: str
    api_key: str
    base_url: str
    model_name: str
    temperature: float = 0.0
    timeout_seconds: int = 60
    is_active: bool = False

    def masked_key(self) -> str:
        if not self.api_key:
            return ""
        if len(self.api_key) <= 8:
            return "******"
        return f"{self.api_key[:4]}...{self.api_key[-4:]}"


class SqliteAiConfigRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_config (
                    provider TEXT PRIMARY KEY,
                    api_key TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    temperature REAL DEFAULT 0.0,
                    timeout_seconds INTEGER DEFAULT 60,
                    is_active INTEGER DEFAULT 0
                )
                """
            )

    def list_configs(self) -> list[AiConfigEntry]:
        with sqlite3.connect(self._path) as conn:
            cursor = conn.execute(
                """
                SELECT provider, api_key, base_url, model_name, temperature, timeout_seconds, is_active
                FROM ai_config
                ORDER BY provider ASC
                """
            )
            return [
                AiConfigEntry(
                    provider=row[0],
                    api_key=row[1],
                    base_url=row[2],
                    model_name=row[3],
                    temperature=float(row[4]),
                    timeout_seconds=int(row[5]),
                    is_active=bool(row[6]),
                )
                for row in cursor.fetchall()
            ]

    def get_config(self, provider: str) -> AiConfigEntry | None:
        with sqlite3.connect(self._path) as conn:
            cursor = conn.execute(
                """
                SELECT provider, api_key, base_url, model_name, temperature, timeout_seconds, is_active
                FROM ai_config
                WHERE provider = ?
                """,
                (provider,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return AiConfigEntry(
                provider=row[0],
                api_key=row[1],
                base_url=row[2],
                model_name=row[3],
                temperature=float(row[4]),
                timeout_seconds=int(row[5]),
                is_active=bool(row[6]),
            )

    def get_active_config(self) -> AiConfigEntry | None:
        with sqlite3.connect(self._path) as conn:
            cursor = conn.execute(
                """
                SELECT provider, api_key, base_url, model_name, temperature, timeout_seconds, is_active
                FROM ai_config
                WHERE is_active = 1
                LIMIT 1
                """
            )
            row = cursor.fetchone()
            if not row:
                return None
            return AiConfigEntry(
                provider=row[0],
                api_key=row[1],
                base_url=row[2],
                model_name=row[3],
                temperature=float(row[4]),
                timeout_seconds=int(row[5]),
                is_active=bool(row[6]),
            )

    def upsert_config(
        self,
        provider: str,
        api_key: str,
        base_url: str,
        model_name: str,
        temperature: float = 0.0,
        timeout_seconds: int = 60,
        is_active: bool = False,
    ) -> AiConfigEntry:
        with sqlite3.connect(self._path) as conn:
            if is_active:
                conn.execute("UPDATE ai_config SET is_active = 0")
            conn.execute(
                """
                INSERT INTO ai_config (provider, api_key, base_url, model_name, temperature, timeout_seconds, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    api_key = excluded.api_key,
                    base_url = excluded.base_url,
                    model_name = excluded.model_name,
                    temperature = excluded.temperature,
                    timeout_seconds = excluded.timeout_seconds,
                    is_active = excluded.is_active
                """,
                (
                    provider,
                    api_key,
                    base_url,
                    model_name,
                    temperature,
                    timeout_seconds,
                    1 if is_active else 0,
                ),
            )
        return AiConfigEntry(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            model_name=model_name,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            is_active=is_active,
        )

    def set_active_provider(self, provider: str) -> None:
        with sqlite3.connect(self._path) as conn:
            conn.execute("UPDATE ai_config SET is_active = 0")
            if provider != "local":
                conn.execute(
                    "UPDATE ai_config SET is_active = 1 WHERE provider = ?",
                    (provider,),
                )

# Cloud AI Multi-Backend (OpenAI & Gemini) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a multi-backend AI reviewer supporting OpenAI-compatible and Google Gemini APIs alongside existing local GGUF models, including configuration storage, sidecar IPC methods, and desktop Settings UI.

**Architecture:** Ports & Adapters pattern. Implement `FullTextReviewer` & `ContextClassifier` in `soatvan.models.cloud_api` for OpenAI & Gemini HTTP APIs. Store active AI config in `preferences.db`. Expose `ai_config.*` IPC commands via Python sidecar. Update desktop UI settings tab to configure providers with test-connection capability.

**Tech Stack:** Python 3.12 (`urllib.request`, `sqlite3`, `pytest`), TypeScript, Tauri v2.

## Global Constraints
- Do not introduce heavy extra external dependencies to `pyproject.toml` dependencies if standard library suffices (standard `urllib.request` / `json` handles REST JSON APIs reliably with custom TLS & timeouts).
- Keep document privacy notices transparent in UI when cloud provider is active.
- Ensure `CancellationToken` checks are performed before and during chunk requests to allow clean cancellation.
- All code formatted and type-checked (`ruff`, `mypy`, `cargo clippy`, `npm test`).

---

### Task 1: SQLite Storage for AI Provider Configuration

**Files:**
- Create: `engine/src/soatvan/custom_rules/ai_config_repository.py`
- Modify: `engine/src/soatvan/custom_rules/__init__.py`
- Test: `engine/tests/test_ai_config_repository.py`

**Interfaces:**
- Consumes: SQLite database path (`Path`)
- Produces: `SqliteAiConfigRepository` with methods:
  - `get_active_config() -> AiConfigEntry | None`
  - `list_configs() -> list[AiConfigEntry]`
  - `upsert_config(provider: str, api_key: str, base_url: str, model_name: str, temperature: float, timeout_seconds: int, is_active: bool) -> AiConfigEntry`
  - `set_active_provider(provider: str) -> None`

- [ ] **Step 1: Write failing test for AiConfigRepository**

```python
# engine/tests/test_ai_config_repository.py
from pathlib import Path
from soatvan.custom_rules.ai_config_repository import SqliteAiConfigRepository


def test_ai_config_crud(tmp_path: Path) -> None:
    repo = SqliteAiConfigRepository(tmp_path / "preferences.db")
    assert repo.get_active_config() is None
    assert repo.list_configs() == []

    created = repo.upsert_config(
        provider="openai",
        api_key="sk-test123456789",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
        is_active=True,
    )
    assert created.provider == "openai"
    assert created.is_active is True
    assert repo.get_active_config() == created

    # Add second provider
    gemini = repo.upsert_config(
        provider="gemini",
        api_key="AIzaSyTestKey",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
        is_active=False,
    )
    configs = repo.list_configs()
    assert len(configs) == 2

    # Switch active
    repo.set_active_provider("gemini")
    active = repo.get_active_config()
    assert active is not None
    assert active.provider == "gemini"
    assert active.is_active is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project engine pytest engine/tests/test_ai_config_repository.py -v`
Expected: FAIL (ModuleNotFoundError or ImportError)

- [ ] **Step 3: Implement SqliteAiConfigRepository**

```python
# engine/src/soatvan/custom_rules/ai_config_repository.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project engine pytest engine/tests/test_ai_config_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/src/soatvan/custom_rules/ engine/tests/test_ai_config_repository.py
git commit -m "feat(engine): add SqliteAiConfigRepository for storing AI provider settings"
```

---

### Task 2: Cloud AI Reviewer Adapter (`soatvan.models.cloud_api`)

**Files:**
- Create: `engine/src/soatvan/models/cloud_api.py`
- Modify: `engine/src/soatvan/models/__init__.py`
- Test: `engine/tests/test_cloud_api.py`

**Interfaces:**
- Produces:
  - `CloudAiReviewer`: implements `FullTextReviewer` and `ContextClassifier`
  - `test_ai_connection(config: AiConfigEntry) -> dict[str, Any]`

- [ ] **Step 1: Write failing unit test for CloudAiReviewer & Connection test**

```python
# engine/tests/test_cloud_api.py
import json
from unittest.mock import MagicMock, patch
from soatvan.checking.domain import Block
from soatvan.custom_rules.ai_config_repository import AiConfigEntry
from soatvan.models.cloud_api import CloudAiReviewer, test_ai_connection
from soatvan.workflow.ports import CancellationToken


class DummyCancelToken(CancellationToken):
    def raise_if_cancelled(self) -> None:
        pass


def test_openai_compatible_review_success() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    reviewer = CloudAiReviewer(config)

    mock_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "discoveries": [
                                {
                                    "segment_id": "b1",
                                    "source_text": "nghiên cứu",
                                    "suggestion": "nghiên cứu",
                                    "category": "spelling",
                                    "reason_code": "spelling",
                                    "occurrence_index": 0,
                                    "confidence": 0.95,
                                }
                            ]
                        }
                    )
                }
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(mock_response).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        blocks = (Block("b1", "b1", "Đây là nghiên cứu."),)
        result = reviewer.review(blocks, (), "", DummyCancelToken())
        assert result.reviewed_chunks == 1
        assert result.status == "complete"


def test_gemini_review_success() -> None:
    config = AiConfigEntry(
        provider="gemini",
        api_key="AIzaSyTest",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        model_name="gemini-2.5-flash",
    )
    reviewer = CloudAiReviewer(config)

    mock_gemini_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps(
                                {
                                    "discoveries": []
                                }
                            )
                        }
                    ]
                }
            }
        ]
    }

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps(mock_gemini_resp).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        blocks = (Block("b1", "b1", "Văn bản mẫu hoàn chỉnh."),)
        result = reviewer.review(blocks, (), "", DummyCancelToken())
        assert result.reviewed_chunks == 1
        assert len(result.discoveries) == 0


def test_test_ai_connection() -> None:
    config = AiConfigEntry(
        provider="openai",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o-mini",
    )
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_cm = MagicMock()
        mock_cm.read.return_value = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
        mock_cm.__enter__.return_value = mock_cm
        mock_urlopen.return_value = mock_cm

        res = test_ai_connection(config)
        assert res["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project engine pytest engine/tests/test_cloud_api.py -v`
Expected: FAIL

- [ ] **Step 3: Implement CloudAiReviewer and test_ai_connection**

```python
# engine/src/soatvan/models/cloud_api.py
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from soatvan.checking.domain import Block
from soatvan.checking.localization import canonicalize_llm_edit, localize_llm_edit
from soatvan.custom_rules.ai_config_repository import AiConfigEntry
from soatvan.models.review import (
    DISCOVERY_CATEGORIES,
    DISCOVERY_REASON_CODES,
    LLM_ONLY_REVIEW_SYSTEM_PROMPT,
    REVIEW_SYSTEM_PROMPT,
)
from soatvan.workflow.ports import (
    CancellationToken,
    ClassificationCandidate,
    ClassifierVerdict,
    DiscoveryProposal,
    FullReviewResult,
    ReviewCandidate,
)


class CloudAiError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def test_ai_connection(config: AiConfigEntry) -> dict[str, Any]:
    if not config.api_key:
        return {"ok": False, "error": "API_KEY_REQUIRED", "message": "API key không được để trống"}

    try:
        if config.provider == "gemini":
            url = f"{config.base_url.rstrip('/')}/models/{config.model_name}:generateContent?key={config.api_key}"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": "Ping"}]}],
                "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            url = f"{config.base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": config.model_name,
                "messages": [{"role": "user", "content": "Ping"}],
                "temperature": 0.0,
                "max_tokens": 10,
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {config.api_key}",
                },
                method="POST",
            )

        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
            return {"ok": True, "provider": config.provider, "model": config.model_name}
    except urllib.error.HTTPError as err:
        err_msg = err.read().decode("utf-8", errors="ignore")
        if err.code == 401 or err.code == 403:
            return {"ok": False, "error": "API_KEY_INVALID", "message": "API key không hợp lệ hoặc không có quyền truy cập"}
        if err.code == 404:
            return {"ok": False, "error": "MODEL_NOT_FOUND", "message": f"Không tìm thấy model '{config.model_name}' hoặc sai URL"}
        return {"ok": False, "error": f"HTTP_{err.code}", "message": f"Lỗi HTTP {err.code}: {err_msg[:200]}"}
    except Exception as exc:
        return {"ok": False, "error": "CONNECTION_FAILED", "message": f"Không thể kết nối đến máy chủ AI: {exc}"}


class CloudAiReviewer:
    def __init__(self, config: AiConfigEntry) -> None:
        self._config = config

    @property
    def version(self) -> str:
        return f"{self._config.provider}:{self._config.model_name}"

    @property
    def minimum_confidence(self) -> float:
        return 0.55

    def classify(
        self,
        candidates: tuple[ClassificationCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
    ) -> tuple[ClassifierVerdict, ...]:
        cancellation.raise_if_cancelled()
        if not candidates:
            return ()
        # For candidate filter, return keep by default or simple verdict list
        return tuple(
            ClassifierVerdict(candidate.candidate_id, "keep", 0.95)
            for candidate in candidates
        )

    def review(
        self,
        blocks: tuple[Block, ...],
        candidates: tuple[ReviewCandidate, ...],
        custom_prompt: str,
        cancellation: CancellationToken,
        progress: Callable[[int, int], None] | None = None,
    ) -> FullReviewResult:
        cancellation.raise_if_cancelled()
        if not blocks:
            return FullReviewResult((), (), 0, 0)

        # Batch blocks into chunks
        chunks = self._build_chunks(blocks)
        total_chunks = len(chunks)
        reviewed_chunks = 0
        all_verdicts: list[ClassifierVerdict] = []
        all_discoveries: list[DiscoveryProposal] = []
        failed_chunk_ids: list[str] = []

        system_prompt = (
            LLM_ONLY_REVIEW_SYSTEM_PROMPT
            if not candidates
            else REVIEW_SYSTEM_PROMPT
        )
        if custom_prompt:
            system_prompt += f"\n\nQuy tắc riêng:\n{custom_prompt}"

        for idx, (chunk_id, chunk_blocks) in enumerate(chunks):
            cancellation.raise_if_cancelled()
            try:
                raw_json = self._call_ai(system_prompt, chunk_blocks, cancellation)
                verdicts, discoveries = self._parse_response(raw_json, chunk_blocks)
                all_verdicts.extend(verdicts)
                all_discoveries.extend(discoveries)
                reviewed_chunks += 1
            except Exception:
                failed_chunk_ids.append(chunk_id)

            if progress is not None:
                progress(idx + 1, total_chunks)

        return FullReviewResult(
            verdicts=tuple(all_verdicts),
            discoveries=tuple(all_discoveries),
            total_chunks=total_chunks,
            reviewed_chunks=reviewed_chunks,
            failed_chunk_ids=tuple(failed_chunk_ids),
        )

    def _build_chunks(self, blocks: tuple[Block, ...]) -> list[tuple[str, list[Block]]]:
        chunks: list[tuple[str, list[Block]]] = []
        current: list[Block] = []
        current_len = 0
        chunk_idx = 0

        for block in blocks:
            text_len = len(block.text)
            if current and (current_len + text_len > 4000):
                chunks.append((f"chunk_{chunk_idx}", current))
                chunk_idx += 1
                current = []
                current_len = 0
            current.append(block)
            current_len += text_len

        if current:
            chunks.append((f"chunk_{chunk_idx}", current))
        return chunks

    def _call_ai(
        self,
        system_prompt: str,
        blocks: list[Block],
        cancellation: CancellationToken,
    ) -> dict[str, Any]:
        cancellation.raise_if_cancelled()
        content_lines = [
            f'<segment id="{b.id}" role="target">{b.text}</segment>'
            for b in blocks
        ]
        user_content = "\n".join(content_lines)

        if self._config.provider == "gemini":
            url = f"{self._config.base_url.rstrip('/')}/models/{self._config.model_name}:generateContent?key={self._config.api_key}"
            payload = {
                "contents": [
                    {"role": "user", "parts": [{"text": f"{system_prompt}\n\n{user_content}"}]}
                ],
                "generationConfig": {
                    "temperature": self._config.temperature,
                    "responseMimeType": "application/json",
                },
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        else:
            url = f"{self._config.base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": self._config.model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "temperature": self._config.temperature,
                "response_format": {"type": "json_object"},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._config.api_key}",
                },
                method="POST",
            )

        with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if self._config.provider == "gemini":
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                text = data["choices"][0]["message"]["content"]
            return json.loads(text)

    def _parse_response(
        self, data: dict[str, Any], blocks: list[Block]
    ) -> tuple[list[ClassifierVerdict], list[DiscoveryProposal]]:
        block_map = {b.id: b for b in blocks}
        discoveries: list[DiscoveryProposal] = []
        verdicts: list[ClassifierVerdict] = []

        for item in data.get("discoveries", []):
            if not isinstance(item, dict):
                continue
            seg_id = str(item.get("segment_id", ""))
            if seg_id not in block_map:
                continue
            block = block_map[seg_id]
            source_text = str(item.get("source_text", ""))
            suggestion = str(item.get("suggestion", ""))
            category = str(item.get("category", "spelling"))
            reason_code = str(item.get("reason_code", category))
            if category not in DISCOVERY_CATEGORIES:
                category = "spelling"
            if reason_code not in DISCOVERY_REASON_CODES:
                reason_code = category

            source_text, suggestion = canonicalize_llm_edit(
                source_text, suggestion, category, reason_code
            )
            localized = localize_llm_edit(source_text, suggestion, reason_code)
            if localized is None:
                continue
            _, local_src, local_sug = localized
            occ_idx = int(item.get("occurrence_index", 0))

            # Locate occurrence
            start = -1
            current_occ = 0
            search_start = 0
            while True:
                idx = block.text.find(local_src, search_start)
                if idx == -1:
                    break
                if current_occ == occ_idx:
                    start = idx
                    break
                current_occ += 1
                search_start = idx + len(local_src)

            if start == -1:
                start = block.text.find(local_src)
            if start == -1:
                continue

            discoveries.append(
                DiscoveryProposal(
                    block_id=seg_id,
                    start=start,
                    end=start + len(local_src),
                    source_text=local_src,
                    suggestion=local_sug,
                    category=category,
                    reason_code=reason_code,
                    confidence=float(item.get("confidence", 0.9)),
                )
            )

        for v in data.get("verdicts", []):
            if isinstance(v, dict) and "candidate_id" in v and "verdict" in v:
                verdicts.append(
                    ClassifierVerdict(
                        candidate_id=str(v["candidate_id"]),
                        verdict=str(v["verdict"]),
                        confidence=float(v.get("confidence", 0.9)),
                    )
                )

        return verdicts, discoveries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project engine pytest engine/tests/test_cloud_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/src/soatvan/models/cloud_api.py engine/tests/test_cloud_api.py
git commit -m "feat(engine): implement CloudAiReviewer and test_ai_connection"
```

---

### Task 3: Sidecar IPC Integration & Dynamic Provider

**Files:**
- Modify: `engine/src/soatvan/entrypoints/sidecar.py`
- Test: `engine/tests/test_sidecar_cloud_ai.py`

**Interfaces:**
- Extends Sidecar with methods:
  - `ai_config.get`: Returns list of configs and active provider.
  - `ai_config.update`: Updates a provider config.
  - `ai_config.set_active`: Sets active provider (`"local"`, `"openai"`, or `"gemini"`).
  - `ai_config.test_connection`: Tests connection for given params.
- Updates `ProcessDocument` classifier provider injection to use CloudAiReviewer when cloud provider is active.

- [ ] **Step 1: Write failing test for Sidecar AI Config dispatch**

```python
# engine/tests/test_sidecar_cloud_ai.py
import json
from unittest.mock import MagicMock, patch
from soatvan.entrypoints.sidecar import Sidecar


def test_sidecar_ai_config_methods(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SOATVAN_DATA_DIR", str(tmp_path))
    sidecar = Sidecar()

    # Get initial config
    res = sidecar.dispatch("ai_config.get", {})
    assert res["active_provider"] == "local"

    # Update openai config
    update_res = sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "api_key": "sk-1234567890",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    assert update_res["updated"] is True

    # Get updated config
    get_res = sidecar.dispatch("ai_config.get", {})
    assert get_res["active_provider"] == "openai"
    openai_cfg = next(c for c in get_res["configs"] if c["provider"] == "openai")
    assert openai_cfg["masked_key"].startswith("sk-1")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project engine pytest engine/tests/test_sidecar_cloud_ai.py -v`
Expected: FAIL

- [ ] **Step 3: Modify Sidecar to add AI Config dispatch and dynamic classifier provider**

Update `engine/src/soatvan/entrypoints/sidecar.py`:
- Add `SqliteAiConfigRepository` instance to `Sidecar.__init__`.
- Create a `DynamicClassifierProvider` class combining `ModelRegistry` (local) and `CloudAiReviewer` (cloud).
- Register methods in `Sidecar.dispatch`: `ai_config.get`, `ai_config.update`, `ai_config.set_active`, `ai_config.test_connection`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --project engine pytest engine/tests/test_sidecar_cloud_ai.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine/src/soatvan/entrypoints/sidecar.py engine/tests/test_sidecar_cloud_ai.py
git commit -m "feat(engine): add ai_config IPC methods and dynamic classifier provider to sidecar"
```

---

### Task 4: Frontend API & Contracts Update

**Files:**
- Modify: `apps/desktop/src/contracts.ts`
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Modify: `apps/desktop/src-tauri/src/sidecar.rs`

**Interfaces:**
- `AiProviderConfig` interface in TypeScript:
  - `provider: "local" | "openai" | "gemini"`
  - `apiKey: string`
  - `maskedKey?: string`
  - `baseUrl: string`
  - `modelName: string`
  - `temperature: number`
  - `timeoutSeconds: number`
  - `isActive: boolean`
- API functions:
  - `api.aiConfigGet(): Promise<{ active_provider: string; configs: AiProviderConfig[] }>`
  - `api.aiConfigUpdate(config: AiProviderConfig): Promise<boolean>`
  - `api.aiConfigSetActive(provider: string): Promise<boolean>`
  - `api.aiConfigTestConnection(config: Partial<AiProviderConfig>): Promise<{ ok: boolean; message?: string; error?: string }>`

- [ ] **Step 1: Add types to `contracts.ts` and api wrapper methods to `api.ts`**
- [ ] **Step 2: Add IPC commands to Rust Host (`src-tauri/src/lib.rs`) to forward `ai_config_*` to Sidecar**
- [ ] **Step 3: Run cargo check & clippy**

Run: `cargo clippy --manifest-path apps/desktop/src-tauri/Cargo.toml`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/src/contracts.ts apps/desktop/src/api.ts apps/desktop/src-tauri/
git commit -m "feat(desktop): add IPC bindings for ai_config methods"
```

---

### Task 5: Frontend Settings UI & Workflow Integration

**Files:**
- Modify: `apps/desktop/src/main.ts`
- Modify: `apps/desktop/src/styles.css`
- Test: `apps/desktop/` (npm test)

- [ ] **Step 1: Update Settings navigation and AI Model section in `main.ts`**
- [ ] **Step 2: Add form controls for OpenAI & Gemini configuration with "Kiểm tra kết nối" button**
- [ ] **Step 3: Update Workflow Step 2 AI readiness check and badge**
- [ ] **Step 4: Run UI unit tests**

Run: `npm test --prefix apps/desktop`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/main.ts apps/desktop/src/styles.css
git commit -m "feat(ui): add AI provider settings and connection test UI"
```

---

### Task 6: Full Verification & Integration Test

- [ ] **Step 1: Run all engine tests and lint checks**

```bash
uv run --project engine pytest --cov=soatvan
uv run --project engine ruff check engine
uv run --project engine mypy --config-file engine/pyproject.toml
```

- [ ] **Step 2: Run frontend build and tests**

```bash
npm test --prefix apps/desktop
npm run build --prefix apps/desktop
```

- [ ] **Step 3: Run Rust tests and clippy**

```bash
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo clippy --all-targets --manifest-path apps/desktop/src-tauri/Cargo.toml -- -D warnings
```

- [ ] **Step 4: Final commit and verify git status clean**

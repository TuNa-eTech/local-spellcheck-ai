# Kế hoạch Triển khai: Phân tách và Quản lý Kích hoạt Mô hình Seq2Seq (`vn-spell-correction-small`)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách card cấu hình mô hình `vn-spell-correction-small` ra khỏi tab LLM Provider thành một khối độc lập trong trang Cài đặt, bổ sung công tắc On/Off lưu bền vững vào SQLite, và điều khiển luồng rà soát chạy trước LLM một cách an toàn.

**Architecture:** Mở rộng bảng `seq2seq_config` trong SQLite với cột `is_enabled`, cập nhật IPC handler trong Python Sidecar và Rust Tauri host để hỗ trợ bật/tắt độc lập với đường dẫn model. Tái cấu trúc UI Cài đặt trong TypeScript để đưa Seq2Seq ra ngoài tab LLM, thêm Toggle Switch và cập nhật trạng thái tức thì.

**Tech Stack:** Python 3.12 (sqlite3, dataclasses, pytest), Rust (Tauri v2, serde, serde_json), TypeScript (Vanilla, Vite, Vitest).

## Global Constraints

- File gốc DOCX luôn bất biến; không can thiệp nội dung văn bản trực tiếp.
- Giữ nguyên màn hình Bước 2 (Chuẩn bị rà soát) tối giản, không thêm checkbox mới.
- Thiết kế giao diện tuân thủ `tokens.css` và `design.md` (flat rows, hairline divider, không nested tabs).
- Mọi chỉnh sửa phải có test kiểm thử tương ứng (TDD).

---

### Task 1: Python SQLite Repository & Config Migration

**Files:**
- Modify: `engine/src/soatvan/custom_rules/seq2seq_config_repository.py`
- Test: `engine/tests/test_seq2seq_config_repository.py`

**Interfaces:**
- Consumes: `sqlite3`, `pathlib.Path`, `dataclasses.dataclass`
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class Seq2SeqConfig:
      model_dir: str
      is_enabled: bool = True

      @property
      def is_configured(self) -> bool: ...
      def is_valid(self) -> bool: ...
      @property
      def is_active(self) -> bool: ...

  class SqliteSeq2SeqConfigRepository:
      def get_config(self) -> Seq2SeqConfig: ...
      def set_config(
          self,
          model_dir: str | None = None,
          is_enabled: bool | None = None,
      ) -> Seq2SeqConfig: ...
  ```

- [x] **Step 1: Viết failing test cho Seq2SeqConfig và migration cột `is_enabled`**

Tạo file `engine/tests/test_seq2seq_config_repository.py`:

```python
import sqlite3
from pathlib import Path

from soatvan.custom_rules.seq2seq_config_repository import (
    Seq2SeqConfig,
    SqliteSeq2SeqConfigRepository,
)


def test_seq2seq_config_dataclass_properties(tmp_path: Path) -> None:
    # 1. Chưa cấu hình
    cfg = Seq2SeqConfig(model_dir="", is_enabled=True)
    assert not cfg.is_configured
    assert not cfg.is_valid()
    assert not cfg.is_active

    # 2. Cấu hình nhưng thư mục không tồn tại / thiếu config.json
    invalid_dir = tmp_path / "invalid_model"
    invalid_dir.mkdir()
    cfg2 = Seq2SeqConfig(model_dir=str(invalid_dir), is_enabled=True)
    assert cfg2.is_configured
    assert not cfg2.is_valid()
    assert not cfg2.is_active

    # 3. Thư mục hợp lệ và is_enabled=True
    valid_dir = tmp_path / "valid_model"
    valid_dir.mkdir()
    (valid_dir / "config.json").write_text("{}", encoding="utf-8")
    cfg3 = Seq2SeqConfig(model_dir=str(valid_dir), is_enabled=True)
    assert cfg3.is_configured
    assert cfg3.is_valid()
    assert cfg3.is_active

    # 4. Thư mục hợp lệ nhưng is_enabled=False -> is_active là False
    cfg4 = Seq2SeqConfig(model_dir=str(valid_dir), is_enabled=False)
    assert cfg4.is_configured
    assert cfg4.is_valid()
    assert not cfg4.is_active


def test_sqlite_seq2seq_config_repository_crud(tmp_path: Path) -> None:
    db_path = tmp_path / "preferences.db"
    repo = SqliteSeq2SeqConfigRepository(db_path)

    # Mặc định ban đầu
    cfg = repo.get_config()
    assert cfg.model_dir == ""
    assert cfg.is_enabled is True

    # Cập nhật đường dẫn
    cfg = repo.set_config(model_dir="/path/to/model")
    assert cfg.model_dir == "/path/to/model"
    assert cfg.is_enabled is True

    # Cập nhật chỉ bật/tắt (is_enabled=False)
    cfg = repo.set_config(is_enabled=False)
    assert cfg.model_dir == "/path/to/model"
    assert cfg.is_enabled is False

    # Cập nhật cả hai
    cfg = repo.set_config(model_dir="/new/path", is_enabled=True)
    assert cfg.model_dir == "/new/path"
    assert cfg.is_enabled is True


def test_sqlite_seq2seq_config_migration_from_legacy_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy_preferences.db"
    # Giả lập DB cũ chỉ có cột model_dir
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE seq2seq_config (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                model_dir TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            "INSERT INTO seq2seq_config (id, model_dir) VALUES (1, '/legacy/model')"
        )

    # Khởi tạo repository, kiểm tra migration tự động thêm cột is_enabled
    repo = SqliteSeq2SeqConfigRepository(db_path)
    cfg = repo.get_config()
    assert cfg.model_dir == "/legacy/model"
    assert cfg.is_enabled is True

    # Kiểm tra cập nhật sau migration
    cfg = repo.set_config(is_enabled=False)
    assert cfg.model_dir == "/legacy/model"
    assert cfg.is_enabled is False
```

- [x] **Step 2: Chạy test để xác nhận test thất bại**

Run: `uv run --project engine pytest engine/tests/test_seq2seq_config_repository.py -v`
Expected: FAIL do `Seq2SeqConfig` chưa có `is_enabled`, `set_config` chưa nhận tham số mới.

- [x] **Step 3: Cập nhật code trong `seq2seq_config_repository.py`**

Thay thế nội dung `engine/src/soatvan/custom_rules/seq2seq_config_repository.py`:

```python
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
```

- [x] **Step 4: Chạy lại test để xác nhận pass**

Run: `uv run --project engine pytest engine/tests/test_seq2seq_config_repository.py -v`
Expected: PASS (3 tests passed).

- [x] **Step 5: Commit task 1**

```bash
git add engine/src/soatvan/custom_rules/seq2seq_config_repository.py engine/tests/test_seq2seq_config_repository.py
git commit -m "feat(engine): add is_enabled column and migration to seq2seq_config"
```

---

### Task 2: Sidecar IPC Handlers & Job Execution

**Files:**
- Modify: `engine/src/soatvan/entrypoints/sidecar.py:115-125,190-205,465-485`
- Test: `engine/tests/test_sidecar_seq2seq.py`

**Interfaces:**
- Consumes: `SqliteSeq2SeqConfigRepository`, `LocalSeq2SeqProvider`
- Produces:
  - `seq2seq_config.get` payload: `{"model_dir": str, "is_configured": bool, "is_valid": bool, "is_enabled": bool}`
  - `seq2seq_config.update` params: `{"model_dir"?: str, "is_enabled"?: bool}`
  - `_run_job`: `use_seq2seq = self.seq2seq is not None and self.seq2seq.is_ready() and _seq2seq_cfg.is_enabled`

- [x] **Step 1: Viết test cho Sidecar Seq2Seq methods và `_run_job`**

Tạo file `engine/tests/test_sidecar_seq2seq.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

from soatvan.entrypoints.sidecar import EngineSidecar


def test_sidecar_seq2seq_config_get_and_update(tmp_path: Path) -> None:
    sidecar = EngineSidecar(local_data=tmp_path)

    # 1. Initial config
    res = sidecar.seq2seq_config_get({})
    assert res["model_dir"] == ""
    assert res["is_configured"] is False
    assert res["is_valid"] is False
    assert res["is_enabled"] is True

    # 2. Update model_dir
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    res = sidecar.seq2seq_config_update({"model_dir": str(model_dir)})
    assert res["model_dir"] == str(model_dir)
    assert res["is_configured"] is True
    assert res["is_valid"] is True
    assert res["is_enabled"] is True

    # 3. Update is_enabled only
    res = sidecar.seq2seq_config_update({"is_enabled": False})
    assert res["model_dir"] == str(model_dir)
    assert res["is_enabled"] is False

    # 4. Verify get returns updated state
    res = sidecar.seq2seq_config_get({})
    assert res["is_enabled"] is False


def test_sidecar_run_job_disables_seq2seq_when_is_enabled_false(tmp_path: Path) -> None:
    sidecar = EngineSidecar(local_data=tmp_path)
    # Mock seq2seq provider as ready
    mock_seq2seq = MagicMock()
    mock_seq2seq.is_ready.return_value = True
    sidecar.seq2seq = mock_seq2seq

    # Set is_enabled = False
    sidecar.seq2seq_config_repo.set_config(model_dir=str(tmp_path), is_enabled=False)

    # Mock processor.process to check the request passed
    mock_process = MagicMock()
    mock_result = MagicMock()
    mock_result.findings = ()
    mock_result.review = None
    mock_process.return_value = mock_result
    sidecar.processor.process = mock_process

    # Call _run_job
    params = {
        "source_path": str(tmp_path / "input.docx"),
        "temporary_output_path": str(tmp_path / "out.docx"),
        "use_model": False,
        "full_review": False,
        "include_rule_findings": False,
    }
    # Create fake input file
    (tmp_path / "input.docx").write_bytes(b"dummy")

    token = MagicMock()
    token.is_cancelled = False
    sidecar._run_job("test-job-1", params, token)

    # Verify process request was called with use_seq2seq=False
    assert mock_process.called
    req = mock_process.call_args[0][0]
    assert req.use_seq2seq is False
```

- [x] **Step 2: Chạy test để xác nhận test thất bại**

Run: `uv run --project engine pytest engine/tests/test_sidecar_seq2seq.py -v`
Expected: FAIL do sidecar chưa trả về `is_enabled` và `seq2seq_config_update` chưa nhận `is_enabled`.

- [x] **Step 3: Cập nhật code trong `engine/src/soatvan/entrypoints/sidecar.py`**

Trong file `engine/src/soatvan/entrypoints/sidecar.py`:

Tại dòng 196 (trong `_run_job`):
```python
            _seq2seq_cfg = self.seq2seq_config_repo.get_config()
            request = ProcessRequest(
                source=Path(params["source_path"]),
                temporary_output=temporary_output,
                preset=Preset(params.get("preset", "standard")),
                rule_config=_rule_config(params.get("rule_config")),
                use_model=_boolean_param(params, "use_model"),
                use_seq2seq=(
                    self.seq2seq is not None
                    and self.seq2seq.is_ready()
                    and _seq2seq_cfg.is_enabled
                ),
                custom_prompt=_custom_prompt(params.get("custom_prompt", "")),
                ignored_words=_ignored_words(params.get("ignored_words", [])),
                full_review=_boolean_param(params, "full_review"),
                include_rule_findings=_boolean_param(params, "include_rule_findings"),
            )
```

Tại phương thức `seq2seq_config_get` (dòng 467-474):
```python
    def seq2seq_config_get(self, _: dict[str, Any]) -> dict[str, Any]:
        cfg = self.seq2seq_config_repo.get_config()
        return {
            "model_dir": cfg.model_dir,
            "is_configured": cfg.is_configured,
            "is_valid": cfg.is_valid(),
            "is_enabled": cfg.is_enabled,
        }
```

Tại phương thức `seq2seq_config_update` (dòng 475-485):
```python
    def seq2seq_config_update(self, params: dict[str, Any]) -> dict[str, Any]:
        model_dir = params.get("model_dir")
        is_enabled = params.get("is_enabled")
        if is_enabled is not None:
            is_enabled = bool(is_enabled)

        cfg = self.seq2seq_config_repo.set_config(
            model_dir=str(model_dir) if model_dir is not None else None,
            is_enabled=is_enabled,
        )
        if model_dir is not None:
            from soatvan.workflow.seq2seq_provider import LocalSeq2SeqProvider

            self.seq2seq = LocalSeq2SeqProvider(
                model_dir=cfg.model_dir if cfg.is_configured else None
            )
            self.processor._seq2seq = self.seq2seq
        return {
            "model_dir": cfg.model_dir,
            "is_configured": cfg.is_configured,
            "is_valid": cfg.is_valid(),
            "is_enabled": cfg.is_enabled,
        }
```

- [x] **Step 4: Chạy lại test để xác nhận pass**

Run: `uv run --project engine pytest engine/tests/test_sidecar_seq2seq.py -v`
Expected: PASS (2 tests passed).

- [x] **Step 5: Commit task 2**

```bash
git add engine/src/soatvan/entrypoints/sidecar.py engine/tests/test_sidecar_seq2seq.py
git commit -m "feat(sidecar): support is_enabled in seq2seq config and job execution"
```

---

### Task 3: Rust Tauri Host IPC Bridge

**Files:**
- Modify: `apps/desktop/src-tauri/src/lib.rs:158-164,645-660`

**Interfaces:**
- Consumes: Tauri `#[tauri::command]`, `serde::Serialize`, `serde::Deserialize`
- Produces:
  ```rust
  #[derive(Debug, Serialize, Deserialize, Clone)]
  struct Seq2SeqConfig {
      model_dir: String,
      is_configured: bool,
      is_valid: bool,
      is_enabled: bool,
  }

  #[tauri::command]
  async fn seq2seq_config_update(
      model_dir: Option<String>,
      is_enabled: Option<bool>,
      state: State<'_, AppState>,
  ) -> AppResult<Seq2SeqConfig>;
  ```

- [x] **Step 1: Cập nhật struct `Seq2SeqConfig` và command `seq2seq_config_update` trong Rust**

Tại `apps/desktop/src-tauri/src/lib.rs`:

1. Cập nhật `struct Seq2SeqConfig` (dòng 158-164):
```rust
#[derive(Debug, Serialize, Deserialize, Clone)]
struct Seq2SeqConfig {
    model_dir: String,
    is_configured: bool,
    is_valid: bool,
    is_enabled: bool,
}
```

2. Cập nhật command `seq2seq_config_update` (dòng 652-660):
```rust
#[tauri::command]
async fn seq2seq_config_update(
    model_dir: Option<String>,
    is_enabled: Option<bool>,
    state: State<'_, AppState>,
) -> AppResult<Seq2SeqConfig> {
    let mut params = json!({});
    if let Some(dir) = model_dir {
        params["model_dir"] = json!(dir);
    }
    if let Some(enabled) = is_enabled {
        params["is_enabled"] = json!(enabled);
    }
    let result = state.engine.call(
        "seq2seq_config.update",
        params,
        Duration::from_secs(5),
    )?;
    Ok(serde_json::from_value(result)?)
}
```

- [x] **Step 2: Thêm unit test kiểm tra struct serialization trong Rust**

Thêm vào phần `#[cfg(test)] mod tests` trong `apps/desktop/src-tauri/src/lib.rs`:
```rust
    #[test]
    fn seq2seq_config_serialization_includes_is_enabled() {
        let json_str = r#"{"model_dir":"/path/to/model","is_configured":true,"is_valid":true,"is_enabled":false}"#;
        let config: Seq2SeqConfig = serde_json::from_str(json_str).expect("deserialize config");
        assert_eq!(config.model_dir, "/path/to/model");
        assert!(config.is_configured);
        assert!(config.is_valid);
        assert!(!config.is_enabled);
    }
```

- [x] **Step 3: Chạy cargo test để kiểm tra**

Run: `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml`
Expected: PASS (All rust tests pass).

- [x] **Step 4: Commit task 3**

```bash
git add apps/desktop/src-tauri/src/lib.rs
git commit -m "feat(tauri): add is_enabled to Seq2SeqConfig and seq2seq_config_update command"
```

---

### Task 4: Frontend Contracts, API & Unit Tests

**Files:**
- Modify: `apps/desktop/src/contracts.ts`
- Modify: `apps/desktop/src/api.ts:60-70`
- Modify: `apps/desktop/tests/api.test.ts`

**Interfaces:**
- Consumes: `@tauri-apps/api/core`
- Produces:
  ```typescript
  export interface Seq2SeqConfig {
    model_dir: string;
    is_configured: boolean;
    is_valid: boolean;
    is_enabled: boolean;
  }

  api.seq2seqConfigGet(): Promise<Seq2SeqConfig>;
  api.seq2seqConfigUpdate(modelDir?: string, isEnabled?: boolean): Promise<Seq2SeqConfig>;
  ```

- [x] **Step 1: Cập nhật `contracts.ts`**

Thêm interface `Seq2SeqConfig` vào cuối `apps/desktop/src/contracts.ts`:

```typescript
export interface Seq2SeqConfig {
  model_dir: string;
  is_configured: boolean;
  is_valid: boolean;
  is_enabled: boolean;
}
```

- [x] **Step 2: Cập nhật `api.ts`**

Trong `apps/desktop/src/api.ts`:
Import `Seq2SeqConfig` từ `./contracts`.
Cập nhật phương thức `seq2seqConfigGet` và `seq2seqConfigUpdate`:

```typescript
  async seq2seqConfigGet(): Promise<Seq2SeqConfig> {
    if (!isTauri()) return { model_dir: "", is_configured: false, is_valid: false, is_enabled: true };
    return invoke("seq2seq_config_get");
  },
  async seq2seqConfigUpdate(modelDir?: string, isEnabled?: boolean): Promise<Seq2SeqConfig> {
    if (!isTauri()) {
      return {
        model_dir: modelDir ?? "",
        is_configured: Boolean(modelDir),
        is_valid: Boolean(modelDir),
        is_enabled: isEnabled ?? true,
      };
    }
    return invoke("seq2seq_config_update", {
      modelDir: modelDir ?? null,
      isEnabled: isEnabled ?? null,
    });
  },
```

- [x] **Step 3: Cập nhật unit test trong `apps/desktop/tests/api.test.ts`**

Thêm test case cho `seq2seqConfigGet` và `seq2seqConfigUpdate` vào `apps/desktop/tests/api.test.ts`:

```typescript
  it("handles seq2seq config get and update with isEnabled", async () => {
    const fakeConfig = {
      model_dir: "C:\\models\\seq2seq",
      is_configured: true,
      is_valid: true,
      is_enabled: false,
    };
    mocks.invoke.mockResolvedValueOnce(fakeConfig);

    const got = await api.seq2seqConfigGet();
    expect(got).toEqual(fakeConfig);
    expect(mocks.invoke).toHaveBeenNthCalledWith(1, "seq2seq_config_get");

    mocks.invoke.mockResolvedValueOnce({ ...fakeConfig, is_enabled: true });
    const updated = await api.seq2seqConfigUpdate(undefined, true);
    expect(updated.is_enabled).toBe(true);
    expect(mocks.invoke).toHaveBeenNthCalledWith(2, "seq2seq_config_update", {
      modelDir: null,
      isEnabled: true,
    });
  });
```

- [x] **Step 4: Chạy Vitest để xác nhận pass**

Run: `npm test --prefix apps/desktop`
Expected: PASS (All tests pass).

- [x] **Step 5: Commit task 4**

```bash
git add apps/desktop/src/contracts.ts apps/desktop/src/api.ts apps/desktop/tests/api.test.ts
git commit -m "feat(desktop): add Seq2SeqConfig contract and update api methods"
```

---

### Task 5: Frontend UI Layout & Toggle Control in Settings

**Files:**
- Modify: `apps/desktop/src/main.ts`
- Modify: `apps/desktop/src/styles.css`

**Interfaces:**
- Consumes: `api.seq2seqConfigUpdate`, `Seq2SeqConfig`, `tokens.css`
- Produces:
  - Tách card Seq2Seq ra ngoài khối `if (currentTab === "local")`
  - Render Card Seq2Seq nằm độc lập ở phía dưới trang "Mô hình AI"
  - Công tắc Toggle Switch `#seq2seq-toggle-enabled` có nhãn On/Off và xử lý sự kiện change

- [x] **Step 1: Bổ sung style cho Toggle Switch trong `apps/desktop/src/styles.css`**

Thêm css cho switch toggle nếu chưa có (theo chuẩn design system):

```css
/* Toggle Switch for Seq2Seq */
.toggle-control {
  display: inline-flex;
  align-items: center;
  gap: var(--space-xs);
  cursor: pointer;
  user-select: none;
}

.toggle-switch {
  position: relative;
  width: 2.75rem;
  height: 1.5rem;
  background-color: var(--color-paper-3);
  border: 1px solid var(--color-rule);
  border-radius: var(--radius-pill);
  transition: background-color var(--dur-short) var(--ease-out), border-color var(--dur-short) var(--ease-out);
}

.toggle-switch::after {
  content: "";
  position: absolute;
  top: 2px;
  left: 2px;
  width: 1.125rem;
  height: 1.125rem;
  background-color: var(--color-paper);
  border-radius: 50%;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
  transition: transform var(--dur-short) var(--ease-out);
}

.toggle-input:checked + .toggle-switch {
  background-color: var(--color-accent);
  border-color: var(--color-accent);
}

.toggle-input:checked + .toggle-switch::after {
  transform: translateX(1.25rem);
}

.toggle-input:disabled + .toggle-switch {
  opacity: 0.45;
  cursor: not-allowed;
}

.toggle-label {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-ink);
}

.settings-divider {
  border: 0;
  border-top: 1px solid var(--color-rule);
  margin: var(--space-xl) 0 var(--space-lg) 0;
}
```

- [x] **Step 2: Cập nhật layout và rendering trong `apps/desktop/src/main.ts`**

1. Cập nhật kiểu `state.seq2seqConfig`:
   Sử dụng `Seq2SeqConfig | null` đã import từ `./contracts`.

2. Hàm helper render Seq2Seq Section độc lập:
```typescript
function seq2seqSectionHtml(controlsLocked: boolean): string {
  const seq2seqCfg = state.seq2seqConfig;
  const isConfigured = Boolean(seq2seqCfg?.is_configured);
  const isValid = Boolean(seq2seqCfg?.is_valid);
  const isEnabled = Boolean(seq2seqCfg?.is_enabled);

  const statusTitle = !isConfigured
    ? "Chưa cấu hình thư mục model"
    : isValid
      ? (isEnabled ? "✓ Đang bật — Tự động chạy rà soát chính tả trước LLM" : "○ Đã tắt — Bỏ qua bước sửa chính tả Seq2Seq")
      : "✕ Thư mục không hợp lệ (không tìm thấy config.json)";

  const desc = isConfigured
    ? escape(seq2seqCfg?.model_dir ?? "")
    : "Chưa chọn thư mục chứa model vn-spell-correction-small.";

  const toggleDisabled = controlsLocked || state.seq2seqSaving || !isValid;
  const toggleChecked = isValid && isEnabled ? "checked" : "";
  const toggleTooltip = !isValid ? "title=\"Cần chọn thư mục model hợp lệ để kích hoạt\"" : "";

  return `
    <hr class="settings-divider" aria-hidden="true" />
    <div class="section-copy model-section-copy">
      <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
        <div>
          <h3 id="seq2seq-heading">AI sửa lỗi chính tả (Seq2Seq Offline)</h3>
          <p>Mô hình <code>vn-spell-correction-small</code> chạy hoàn toàn trên máy, tự động sửa lỗi telex, dấu câu và chính tả ngữ cảnh trước khi LLM phân tích.</p>
        </div>
        <label class="toggle-control" ${toggleTooltip}>
          <input type="checkbox" class="toggle-input sr-only" id="seq2seq-toggle-enabled" ${toggleChecked} ${toggleDisabled ? "disabled" : ""} aria-labelledby="seq2seq-heading">
          <span class="toggle-switch" aria-hidden="true"></span>
          <span class="toggle-label">${isEnabled && isValid ? "Bật" : "Tắt"}</span>
        </label>
      </div>
    </div>
    <div class="model-card" id="seq2seq-card">
      <div class="model-card__header">
        <div>
          <strong id="seq2seq-status-title">${statusTitle}</strong>
          <p>${desc}</p>
        </div>
        <div class="button-row">
          <button class="button button--secondary button--small" id="seq2seq-choose-dir" type="button" ${controlsLocked || state.seq2seqSaving ? "disabled" : ""} ${state.seq2seqSaving ? 'aria-busy="true"' : ""}>
            ${state.seq2seqSaving ? "Đang lưu…" : isConfigured ? "📂 Chọn lại thư mục" : "📂 Chọn thư mục model"}
          </button>
          ${isConfigured ? `<button class="delete-button" id="seq2seq-remove" type="button" ${controlsLocked || state.seq2seqSaving ? "disabled" : ""}>Xóa</button>` : ""}
        </div>
      </div>
    </div>
  `;
}
```

3. Trong `settingsHtml()` cho mục `models`:
   - Bỏ khối `seq2seq` ra khỏi `if (currentTab === "local")`.
   - Nối `${seq2seqSectionHtml(controlsLocked)}` vào cuối `body` của `settings-models` để **luôn luôn hiển thị bên dưới tab LLM**.

4. Thêm event listener cho `#seq2seq-toggle-enabled`:
```typescript
  document.querySelector("#seq2seq-toggle-enabled")?.addEventListener("change", async (e) => {
    if (state.seq2seqSaving || !state.seq2seqConfig?.is_valid) return;
    const target = e.target as HTMLInputElement;
    const wantEnabled = target.checked;
    state.seq2seqSaving = true;
    render();
    try {
      const cfg = await api.seq2seqConfigUpdate(undefined, wantEnabled);
      state.seq2seqConfig = cfg;
      state.settingsMessage = {
        tone: "status",
        text: wantEnabled ? "Đã bật mô hình chính tả Seq2Seq." : "Đã tắt mô hình chính tả Seq2Seq.",
      };
    } catch {
      state.settingsMessage = { tone: "error", text: "Không thể lưu trạng thái kích hoạt." };
    } finally {
      state.seq2seqSaving = false;
      render("#seq2seq-toggle-enabled");
    }
  });
```

- [x] **Step 3: Chạy build và test của frontend để kiểm tra cú pháp**

Run:
```bash
npm run build --prefix apps/desktop
npm test --prefix apps/desktop
```
Expected: PASS (build thành công và tất cả unit test pass).

- [x] **Step 4: Commit task 5**

```bash
git add apps/desktop/src/main.ts apps/desktop/src/styles.css
git commit -m "feat(desktop): decouple seq2seq card into independent section with on/off toggle"
```

---

### Task 6: Kiểm thử Toàn diện & Xác minh (E2E & Regressions)

**Files:**
- Toàn bộ các files liên quan trong backend và frontend.

- [x] **Step 1: Chạy toàn bộ test backend Python và linter**

Run:
```bash
uv run --project engine pytest --cov=soatvan --cov-fail-under=85
uv run --project engine ruff check engine
uv run --project engine mypy --config-file engine/pyproject.toml
```
Expected: PASS (Không có lỗi type, lint, và coverage >= 85%).

- [x] **Step 2: Chạy kiểm tra Rust Host clippy và test**

Run:
```bash
cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
cargo clippy --all-targets --manifest-path apps/desktop/src-tauri/Cargo.toml -- -D warnings
```
Expected: PASS (Không có warning clippy, mọi test pass).

- [x] **Step 3: Chạy kiểm tra Frontend test và build**

Run:
```bash
npm test --prefix apps/desktop
npm run build --prefix apps/desktop
```
Expected: PASS.

- [x] **Step 4: Commit và hoàn thiện kế hoạch**

```bash
git add docs/superpowers/plans/2026-09-03-seq2seq-decoupling.md
git commit -m "docs: finalize implementation plan for seq2seq decoupling"
```

# Thiết kế Kỹ thuật: Phân tách và Quản lý Kích hoạt Mô hình Seq2Seq (`vn-spell-correction-small`)

- **Ngày tạo**: 2026-09-03
- **Trạng thái**: Approved
- **Phạm vi**: Giao diện Cài đặt (Settings), Quản lý cấu hình SQLite, Rust Tauri IPC, và Pipeline rà soát Sidecar

---

## 1. Mục tiêu và Bối cảnh

### 1.1 Bối cảnh
Mô hình `vn-spell-correction-small` là mô hình Seq2Seq (BARTpho/ViT5) chuyên trách việc phát hiện và sửa lỗi telex, dấu câu, và chính tả ngữ cảnh tiếng Việt ở mức câu/đoạn. Khác với các LLM (GGUF cục bộ hay Cloud API qua OpenAI/Gemini), mô hình này chạy trực tiếp qua PyTorch/Transformers và không nhận prompt.

Tuy nhiên, trong triển khai hiện tại:
1. Card cấu hình Seq2Seq đang bị đặt lồng bên trong tab `Local` của LLM Provider. Khi người dùng chuyển sang tab `OpenAI` hoặc `Google Gemini`, cấu hình Seq2Seq bị ẩn hoàn toàn, gây hiểu lầm rằng Seq2Seq chỉ dùng được khi chạy Local GGUF.
2. Không có công tắc **Bật / Tắt (ON/OFF)**: Khi người dùng đã trỏ đường dẫn hợp lệ, backend luôn tự động chạy Seq2Seq cho mọi tài liệu. Cách duy nhất để tắt là bấm "Xóa" cấu hình thư mục.
3. Cờ `use_seq2seq` trong backend `sidecar.py` đang được gán cứng theo `self.seq2seq is not None and self.seq2seq.is_ready()`, chưa kết hợp với trạng thái kích hoạt mong muốn của người dùng.

### 1.2 Mục tiêu
- **Phân tách giao diện**: Đưa phần cấu hình `vn-spell-correction-small` thành một khối độc lập nằm ở nửa dưới trang "Mô hình AI", luôn hiển thị bất kể người dùng chọn tab LLM nào (Local, OpenAI, hay Gemini).
- **Công tắc Bật/Tắt tập trung**: Bổ sung toggle switch Bật/Tắt ngay trên card Seq2Seq trong Cài đặt:
  - Nếu chưa có thư mục model hoặc thư mục không hợp lệ: Toggle bị vô hiệu hóa (`disabled`) và ở trạng thái Tắt.
  - Nếu thư mục hợp lệ: Người dùng có thể bật hoặc tắt tùy ý; trạng thái được lưu bền vững vào SQLite.
- **Giữ nguyên Bước 2 (Chuẩn bị rà soát)**: Không thêm checkbox ở màn hình rà soát để đảm bảo trải nghiệm tối giản, nhanh gọn cho người dùng.
- **Tuân thủ Pipeline**: Khi được bật, Seq2Seq luôn chạy trước LLM trong luồng xử lý tài liệu; khi bị tắt, pipeline bỏ qua bước này.

---

## 2. Thiết kế Kỹ thuật Chi tiết

### 2.1 Tầng Python Engine & Lưu trữ SQLite

#### A. Migration & Repository (`soatvan.custom_rules.seq2seq_config_repository`)
- Bảng `seq2seq_config` trong `preferences.db` được bổ sung cột `is_enabled`:
  ```sql
  CREATE TABLE IF NOT EXISTS seq2seq_config (
      id INTEGER PRIMARY KEY CHECK(id = 1),
      model_dir TEXT NOT NULL DEFAULT '',
      is_enabled INTEGER NOT NULL DEFAULT 1
  );
  ```
- Cơ chế tự động migration: Nếu bảng đã tồn tại từ trước mà chưa có cột `is_enabled`, thực hiện `ALTER TABLE seq2seq_config ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1;`.
- Dataclass `Seq2SeqConfig`:
  ```python
  @dataclass(frozen=True, slots=True)
  class Seq2SeqConfig:
      model_dir: str
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
  ```
- Cập nhật các hàm `get_config()` và `set_config(model_dir: str | None = None, is_enabled: bool | None = None)`.

#### B. Sidecar Handlers (`soatvan.entrypoints.sidecar`)
- `seq2seq_config_get`:
  Trả về:
  ```json
  {
    "model_dir": "...",
    "is_configured": true,
    "is_valid": true,
    "is_enabled": true
  }
  ```
- `seq2seq_config_update`:
  Nhận tham số tùy chọn: `{"model_dir": "...", "is_enabled": true/false}`.
  Cập nhật trạng thái và khởi tạo lại `LocalSeq2SeqProvider` nếu đường dẫn thay đổi.
- Luồng khởi tạo Job rà soát (`_run_job`):
  ```python
  _seq2seq_cfg = self.seq2seq_config_repo.get_config()
  use_seq2seq = (
      self.seq2seq is not None
      and self.seq2seq.is_ready()
      and _seq2seq_cfg.is_enabled
  )
  ```

---

### 2.2 Tầng Rust Tauri Host

Tại `apps/desktop/src-tauri/src/lib.rs`:
- Cập nhật struct `Seq2SeqConfig`:
  ```rust
  #[derive(Debug, Serialize, Deserialize, Clone)]
  struct Seq2SeqConfig {
      model_dir: String,
      is_configured: bool,
      is_valid: bool,
      is_enabled: bool,
  }
  ```
- Cập nhật Tauri command `seq2seq_config_update`:
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
      let result = state.engine.call("seq2seq_config.update", params, Duration::from_secs(5))?;
      Ok(serde_json::from_value(result)?)
  }
  ```

---

### 2.3 Tầng Frontend (TypeScript & CSS)

#### A. Hợp đồng & API Call (`contracts.ts` & `api.ts`)
- Trong `contracts.ts`:
  ```typescript
  export interface Seq2SeqConfig {
    model_dir: string;
    is_configured: bool;
    is_valid: bool;
    is_enabled: bool;
  }
  ```
- Trong `api.ts`:
  ```typescript
  seq2seqConfigUpdate(modelDir?: string, isEnabled?: boolean): Promise<Seq2SeqConfig>
  ```

#### B. Giao diện Cài đặt Mô hình (`main.ts`)
- Tách khối `vn-spell-correction-small` ra khỏi nhánh `if (currentTab === "local")`.
- Cấu trúc trang "Mô hình AI" (`#settings-models`):
  1. **Phần trên**: Khối AI Rà soát & Lập luận (LLM) với 3 tab: `Local (GGUF)` | `OpenAI / Tương thích` | `Google Gemini API`.
  2. **Đường phân cách (Hairline Divider)**.
  3. **Phần dưới**: Khối "AI sửa lỗi chính tả chuyên biệt (Seq2Seq Offline)".
- Chi tiết khối Seq2Seq:
  - Header bao gồm:
    - Tiêu đề: `AI sửa lỗi chính tả (Seq2Seq Offline)`
    - Phụ đề: `Mô hình nrl-ai/vn-spell-correction-small chạy hoàn toàn trên máy, tự động sửa lỗi telex, dấu câu và chính tả ngữ cảnh trước khi LLM phân tích.`
    - **Toggle Switch (Bật / Tắt)**:
      - Đặt ID `#seq2seq-toggle-enabled`.
      - Khi `!seq2seqCfg?.is_valid`: switch ở trạng thái unchecked, `disabled="true"`, title giải thích: "Cần cấu hình thư mục model hợp lệ để kích hoạt".
      - Khi `seq2seqCfg?.is_valid`: người dùng có thể click gạt On/Off, gọi ngay `api.seq2seqConfigUpdate(undefined, !seq2seqCfg.is_enabled)`.
  - Thẻ thông tin (`#seq2seq-card`):
    - Trạng thái trực quan:
      - `is_valid && is_enabled`: `✓ Đang bật — Tự động chạy rà soát chính tả trước LLM` (màu xanh/accent).
      - `is_valid && !is_enabled`: `○ Đã tắt — Bỏ qua bước sửa chính tả Seq2Seq` (màu trung tính).
      - `!is_configured`: `Chưa cấu hình thư mục model`.
      - `is_configured && !is_valid`: `✕ Thư mục không hợp lệ (không tìm thấy config.json)`.
    - Đường dẫn thư mục model (nếu đã cấu hình).
    - Nút `📂 Chọn thư mục model` / `📂 Chọn lại thư mục` và nút `Xóa` cấu hình.

---

## 3. Kế hoạch Kiểm thử & Xác minh (Verification)

1. **Kiểm thử Cơ sở dữ liệu & Python Sidecar**:
   - Viết test kiểm tra migration cột `is_enabled` khi bảng `seq2seq_config` đã tồn tại.
   - Viết test cho phương thức `set_config` cập nhật riêng `model_dir`, riêng `is_enabled`, hoặc cả hai.
   - Viết test kiểm tra `_run_job`:
     - Khi `is_enabled = False`: `use_seq2seq` phải là `False`, không gọi `check_blocks()`.
     - Khi `is_enabled = True` và model hợp lệ: `use_seq2seq` là `True`, có tiến trình `seq2seq` trong progress.
2. **Kiểm thử Rust Host**:
   - `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml` đảm bảo biên dịch và serialization của `Seq2SeqConfig` chính xác.
3. **Kiểm thử Giao diện Người dùng**:
   - Kiểm tra chuyển qua lại giữa các tab `Local`, `OpenAI`, `Google Gemini`: Card Seq2Seq vẫn luôn hiển thị ở nửa dưới.
   - Thử nghiệm gạt công tắc On/Off: Trạng thái lưu ngay vào DB, thông báo trạng thái cập nhật phù hợp.
   - Thử nghiệm khi chưa chọn thư mục: Toggle bị khóa và hướng dẫn người dùng chọn thư mục.

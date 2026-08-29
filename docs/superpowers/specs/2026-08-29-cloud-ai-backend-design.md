# Thiết kế Kỹ thuật: Multi-Backend Cloud AI Reviewer (OpenAI-Compatible & Google Gemini)

- **Ngày tạo**: 2026-08-29
- **Trạng thái**: Approved
- **Milestone liên quan**: Cloud AI Provider Integration

---

## 1. Mục tiêu và Bối cảnh

### 1.1 Bối cảnh
SoátVăn là ứng dụng rà soát chính tả và ngữ pháp tiếng Việt trên tài liệu DOCX, hiện tại hỗ trợ:
1. Bộ quy tắc code cố định (RuleEngine).
2. Mô hình cục bộ (Local GGUF qua `llama-cpp-python`).

Khi chạy trên các dòng máy tính cấu hình yếu hoặc không có GPU/VRAM rời, việc nạp và suy luận mô hình cục bộ gặp giới hạn về tốc độ và context window (4K tokens). Người dùng mong muốn có tùy chọn kết nối trực tiếp với các dịch vụ AI trên Cloud bằng API Key cá nhân để tăng tốc độ rà soát và độ chính xác.

### 1.2 Mục tiêu
- Hỗ trợ chuẩn **OpenAI-compatible REST API** (OpenAI, DeepSeek, Groq, OpenRouter, Ollama/vLLM từ xa...).
- Hỗ trợ **Google Gemini REST API native** (`v1beta / generateContent`).
- Lưu trữ cấu hình an toàn trong SQLite Preferences (`preferences.db`).
- Bổ sung giao diện Cài đặt AI trực quan với tính năng Kiểm tra kết nối (Test Connection).
- Giữ nguyên toàn bộ cơ chế tách block, anchor OOXML, discovery quality gate, và protocol contract sẵn có.

---

## 2. Kiến trúc & Thiết kế Chi tiết

### 2.1 Tầng Python Engine

#### A. Data Model & Config (`soatvan.models.cloud_api`)
```python
from dataclasses import dataclass
from typing import Literal

AiProviderType = Literal["local", "openai", "gemini"]

@dataclass(frozen=True, slots=True)
class CloudAiConfig:
    provider: AiProviderType
    api_key: str
    base_url: str
    model_name: str
    temperature: float = 0.0
    timeout_seconds: int = 60
```

#### B. Protocol Implementation: `FullTextReviewer` & `ContextClassifier`
- **`OpenAiCompatibleReviewer`**:
  - Gửi POST request đến `{base_url}/chat/completions`.
  - Body payload:
    ```json
    {
      "model": "<model_name>",
      "messages": [
        {"role": "system", "content": "<SYSTEM_PROMPT>"},
        {"role": "user", "content": "<CHUNK_PAYLOAD_WITH_XML_SEGMENTS>"}
      ],
      "temperature": 0.0,
      "response_format": {"type": "json_object"}
    }
    ```
  - Headers: `Authorization: Bearer <api_key>`, `Content-Type: application/json`.
- **`GeminiReviewer`**:
  - Gửi POST request đến `{base_url}/models/{model_name}:generateContent?key={api_key}` (hoặc header `x-goog-api-key`).
  - Body payload:
    ```json
    {
      "contents": [
        {"role": "user", "parts": [{"text": "<SYSTEM_PROMPT>\n\n<CHUNK_PAYLOAD>"}]}
      ],
      "generationConfig": {
        "temperature": 0.0,
        "responseMimeType": "application/json"
      }
    }
    ```
- **Xử lý phản hồi & Quality Gate**:
  - Parse JSON kết quả discovery theo schema chuẩn `{"discoveries": [...]}` và `{"verdicts": [...]}`.
  - Sử dụng chung logic localization `localize_llm_edit` và quality gate lọc lỗi ảo như local model.
- **Xử lý huỷ và lỗi**:
  - Tích hợp `CancellationToken` kiểm tra trước và trong mỗi lần gọi chunk.
  - Xử lý HTTP status code (401 Unauthorized, 429 Rate Limit có exponential backoff retry 3 lần, 5xx Provider Error).

---

### 2.2 Lưu trữ Cấu hình & Giao thức Sidecar (IPC)

#### A. SQLite Repository (`soatvan.custom_rules.sqlite_repository` / `ai_config_repository`)
Tạo bảng `ai_config` trong `preferences.db`:
```sql
CREATE TABLE IF NOT EXISTS ai_config (
    provider TEXT PRIMARY KEY,
    api_key TEXT NOT NULL,
    base_url TEXT NOT NULL,
    model_name TEXT NOT NULL,
    temperature REAL DEFAULT 0.0,
    timeout_seconds INTEGER DEFAULT 60,
    is_active INTEGER DEFAULT 0
);
```

#### B. Sidecar Dispatch Methods
- **`ai_config.get`**: Trả về danh sách cấu hình và provider đang active (API key được mask: `sk-...xxxx`).
- **`ai_config.update`**: Cập nhật provider, base_url, api_key, model_name, is_active.
- **`ai_config.test_connection`**: Gửi 1 request thử nghiệm ngắn gọn để kiểm tra kết nối API Key và model name.
- **`model.status`**: Trả về `ready` nếu local model được cài đặt HOẶC cloud provider đang active có cấu hình hợp lệ.

---

### 2.3 Giao diện Desktop UI (Tauri / TypeScript)

#### A. Mở rộng trang Cài đặt (Settings)
- Tab **"Mô hình & AI"** cho phép chọn 3 chế độ:
  1. **AI Cục bộ (Local Offline)**: Hiển thị quản lý file `.svmodel` / GGUF như hiện tại.
  2. **OpenAI / Tương thích**: Nhập Base URL (mặc định: `https://api.openai.com/v1`), API Key, Model name (mặc định: `gpt-4o-mini`).
  3. **Google Gemini API**: Nhập API Key, Base URL (mặc định: `https://generativelanguage.googleapis.com/v1beta`), Model name (mặc định: `gemini-2.5-flash`).
- Nút **"Kiểm tra kết nối"**: Gọi `api.aiConfigTestConnection()` và hiển thị trạng thái `Thành công` hoặc thông báo lỗi cụ thể.
- Cảnh báo quyền riêng tư (Privacy Notice): Hiển thị thông báo khi người dùng bật Cloud API.

#### B. Tích hợp màn hình Rà soát (Workflow)
- Hiển thị badge trạng thái AI hiện tại: `AI: OpenAI (gpt-4o-mini)` / `AI: Gemini` / `AI: Local`.
- Cho phép chạy rà soát AI bình thường mà không yêu cầu cài đặt local model nếu Cloud API đã sẵn sàng.

---

## 3. Kế hoạch Kiểm thử & Đảm bảo Chất lượng

1. **Unit Tests (Engine)**:
   - Test mock HTTP request/response cho `OpenAiCompatibleReviewer` và `GeminiReviewer`.
   - Test xử lý lỗi HTTP: 401, 429 rate limit retry, 500 server error, invalid JSON response.
   - Test `CancellationToken` huỷ giữa chừng.
   - Test SQLite CRUD cấu hình AI.
2. **Integration Tests (Sidecar IPC)**:
   - Test các method `ai_config.get`, `ai_config.update`, `ai_config.test_connection`.
   - Test luồng `job.start` hoàn chỉnh với Cloud AI provider.
3. **Frontend UI Tests**:
   - Test form cài đặt, mask API key, validate input, gọi test connection và lưu cài đặt.

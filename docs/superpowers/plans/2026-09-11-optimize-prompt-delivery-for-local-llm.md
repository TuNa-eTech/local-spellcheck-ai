# Tối Ưu Hóa Phương Thức Truyền Custom Prompt Cho Local LLM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Chuyển đổi phương thức truyền `custom_prompt` (các quy tắc người dùng tùy chỉnh) từ việc nhúng trong trường JSON của `user` message sang khối chỉ thị trực tiếp trong `system` message cho cả `review_messages` và `classification_messages`, nhằm tối ưu hóa khả năng tuân thủ luật của mô hình cục bộ và tận dụng cơ chế KV Cache Prefix của `llama.cpp`.

**Architecture:** 
- Đưa chuỗi `custom_prompt` vào phần cuối của `system` prompt với tiêu đề phân định rõ ràng (`## QUY TẮC RIÊNG CỦA NGƯỜI DÙNG (BẮT BUỘC TUÂN THỦ):\n<custom_rules>\n...\n</custom_rules>`).
- Loại bỏ trường `"custom_rule"` khỏi JSON payload của `user` trong `review_messages` và `classification_messages`.
- Giữ nguyên cơ chế đo đạc và kiểm soát ngân sách token (`_effective_document_budget`, `_count_review_request_tokens`, `_ensure_classification_request_fits`), do các hàm này đo đạc toàn bộ tin nhắn (cả system lẫn user).
- Cập nhật các mock test và assertions tương ứng trong bộ unit tests.

**Tech Stack:** Python 3.11+, pytest, llama-cpp-python.

## Global Constraints

- Không làm thay đổi output schema JSON trả về từ mô hình (`REVIEW_SCHEMA`, `LLM_ONLY_REVIEW_SCHEMA`, `VERDICT_SCHEMA`).
- Giữ nguyên các cơ chế an toàn: cảnh báo và chặn khi vượt quá context window (`CUSTOM_PROMPT_CONTEXT_EXCEEDED`, `MODEL_REVIEW_CONTEXT_TOO_SMALL`).
- Tất cả 361 unit tests trong `engine/tests` phải vượt qua sau khi hoàn tất.

---

### Task 1: Cập Nhật `review_messages` và `ReviewChunk.payload` trong `review.py`

**Files:**
- Modify: `engine/src/soatvan/models/review.py:202-260`
- Test: `engine/tests/test_full_review.py:100-165`

**Interfaces:**
- Consumes: `ReviewChunk.custom_prompt: str`
- Produces: `review_messages(chunk: ReviewChunk) -> list[dict[str, str]]` trong đó `messages[0]["content"]` chứa `custom_prompt` (khi có) và `messages[1]["content"]` không còn trường `"custom_rule"`.

- [ ] **Step 1: Viết test kiểm tra format mới của `review_messages`**

Thêm test case vào `engine/tests/test_full_review.py`:

```python
def test_review_messages_injects_custom_prompt_into_system_prompt() -> None:
    segment = ReviewSegment("seg-1", "blk-1", 0, 0, "Nội dung kiểm tra", "p")
    chunk = ReviewChunk("chunk-1", (segment,), (), (), "Luật riêng của người dùng")
    messages = review_messages(chunk)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "## QUY TẮC RIÊNG CỦA NGƯỜI DÙNG (BẮT BUỘC TUÂN THỦ):" in messages[0]["content"]
    assert "<custom_rules>\nLuật riêng của người dùng\n</custom_rules>" in messages[0]["content"]

    user_payload = json.loads(messages[1]["content"])
    assert "custom_rule" not in user_payload
    assert "segments" in user_payload
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `uv run pytest engine/tests/test_full_review.py -k test_review_messages_injects_custom_prompt_into_system_prompt`
Expected: FAIL (vì hiện tại `review_messages` chưa đưa vào system prompt và payload vẫn còn `custom_rule`).

- [ ] **Step 3: Triển khai cập nhật trong `engine/src/soatvan/models/review.py`**

Trong `ReviewChunk.payload()`:
Bỏ dòng `"custom_rule": self.custom_prompt,`.

Trong `review_messages(chunk: ReviewChunk)`:
```python
def review_messages(chunk: ReviewChunk) -> list[dict[str, str]]:
    llm_only = not chunk.candidates
    system = LLM_ONLY_REVIEW_SYSTEM_PROMPT if llm_only else REVIEW_SYSTEM_PROMPT
    if chunk.custom_prompt:
        system += (
            "\n\n## QUY TẮC RIÊNG CỦA NGƯỜI DÙNG (BẮT BUỘC TUÂN THỦ):\n"
            f"<custom_rules>\n{chunk.custom_prompt}\n</custom_rules>"
        )
    return [
        {
            "role": "system",
            "content": system,
        },
        {
            "role": "user",
            "content": json.dumps(chunk.payload(), ensure_ascii=False, separators=(",", ":")),
        },
    ]
```

Đồng thời cập nhật mock `CountingRuntime` trong `test_full_review.py`:
Đổi `has_custom_rule = '"custom_rule":""' not in messages[1]["content"]` thành:
`has_custom_rule = "<custom_rules>" in messages[0]["content"]`.

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `uv run pytest engine/tests/test_full_review.py`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/src/soatvan/models/review.py engine/tests/test_full_review.py
git commit -m "refactor(review): inject custom prompt into system message for local llm"
```

---

### Task 2: Cập Nhật `classification_messages` trong `classifier.py`

**Files:**
- Modify: `engine/src/soatvan/models/classifier.py:83-115`
- Test: `engine/tests/test_classifier.py:120-170`

**Interfaces:**
- Consumes: `candidates: tuple[ClassificationCandidate, ...]`, `custom_prompt: str`
- Produces: `classification_messages(candidates, custom_prompt) -> list[dict[str, str]]` trong đó `messages[0]["content"]` chứa `custom_prompt` và `messages[1]["content"]` không còn trường `"custom_rule"`.

- [ ] **Step 1: Viết test kiểm tra format mới của `classification_messages`**

Cập nhật test trong `engine/tests/test_classifier.py` để verify `messages[0]` chứa custom rules thay vì `prompt["custom_rule"]` trong `messages[1]`:

```python
def test_classifier_injects_custom_prompt_into_system_message() -> None:
    candidate = ClassificationCandidate("c-1", "b-1", "từ sai", "rule-1", "spelling", "lỗi", ("từ đúng",), "ngữ cảnh")
    messages = classification_messages((candidate,), "Ưu tiên thuật ngữ nội bộ")

    assert "## QUY TẮC RIÊNG CỦA NGƯỜI DÙNG" in messages[0]["content"]
    assert "<custom_rules>\nƯu tiên thuật ngữ nội bộ\n</custom_rules>" in messages[0]["content"]

    user_payload = json.loads(messages[1]["content"])
    assert "custom_rule" not in user_payload
    assert "candidates" in user_payload
```

- [ ] **Step 2: Chạy test để xác nhận fail**

Run: `uv run pytest engine/tests/test_classifier.py -k test_classifier_injects_custom_prompt_into_system_message`
Expected: FAIL.

- [ ] **Step 3: Triển khai cập nhật trong `engine/src/soatvan/models/classifier.py`**

Chỉnh sửa `classification_messages`:
```python
def classification_messages(
    candidates: tuple[ClassificationCandidate, ...], custom_prompt: str
) -> list[dict[str, str]]:
    system = (
        "Bạn là bộ phân loại lỗi tiếng Việt chạy cục bộ. "
        "Chỉ đánh giá candidate đã cho. Trả JSON duy nhất dạng "
        '{"verdicts":[{"candidate_id":"...","verdict":"keep|drop",'
        '"confidence":0.0}]}. Không thêm candidate và không sửa văn bản.'
    )
    if custom_prompt:
        system += (
            "\n\n## QUY TẮC RIÊNG CỦA NGƯỜI DÙNG (BẮT BUỘC TUÂN THỦ):\n"
            f"<custom_rules>\n{custom_prompt}\n</custom_rules>"
        )
    return [
        {
            "role": "system",
            "content": system,
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "candidates": [
                        {
                            "candidate_id": item.candidate_id,
                            "paragraph_id": item.block_id,
                            "source_text": item.source_text,
                            "rule_id": item.rule_id,
                            "category": item.category,
                            "reason": item.reason,
                            "suggestions": list(item.suggestions),
                            "context": item.context,
                        }
                        for item in candidates
                    ]
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    ]
```

Cập nhật `test_classifier.py` (đổi kiểm tra `prompt["custom_rule"]` thành kiểm tra `messages[0]["content"]` và cập nhật `CountingRuntime.count_chat_tokens`).

- [ ] **Step 4: Chạy test để xác nhận pass**

Run: `uv run pytest engine/tests/test_classifier.py`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add engine/src/soatvan/models/classifier.py engine/tests/test_classifier.py
git commit -m "refactor(classifier): inject custom prompt into system message"
```

---

### Task 3: Chạy Toàn Bộ Test Suite & Đảm Bảo Tính Tương Thích Hoàn Hảo

**Files:**
- Test: Toàn bộ test suite trong `engine/tests`

- [ ] **Step 1: Chạy pytest toàn diện**

Run: `uv run pytest engine/tests`
Expected: 361 passed.

- [ ] **Step 2: Chạy cargo tests và frontend vitest**

Run:
1. `cargo test --manifest-path src-tauri/Cargo.toml`
2. `npm test`
Expected: Toàn bộ pass không lỗi.

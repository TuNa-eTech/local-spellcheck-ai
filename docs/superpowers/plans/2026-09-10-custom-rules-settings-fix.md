# Kế hoạch Triển khai: Sửa lỗi Nhập & Lưu Prompt Quy tắc riêng trong Cài đặt và Cải thiện UI/UX Thao tác tay

> **Dành cho agent thực thi:** REQUIRED SUB-SKILL: Sử dụng `superpowers:subagent-driven-development` (khuyến nghị) hoặc `superpowers:executing-plans` để triển khai từng nhiệm vụ. Các bước sử dụng cú pháp checkbox (`- [ ]`) để theo dõi tiến độ.

**Mục tiêu:** Khắc phục triệt để lỗi không thể nhập hoặc lưu prompt quy tắc riêng trong Cài đặt, loại bỏ tình trạng đơ/khóa form khi mở cài đặt, đưa cụm nút thao tác tay (Lưu/Huỷ) vào trực tiếp form soạn thảo, và cung cấp phản hồi lỗi/thành công chuẩn xác, minh bạch.

**Kiến trúc:**
- Chuyển `openSettings()` sang gọi `api.modelStatus(false)` để ngăn chặn việc tái kích hoạt/nạp lại GGUF nặng làm treo giao diện ở trạng thái `settingsLoading`.
- Loại bỏ `maxlength="${available}"` bị khóa cứng về `0` trên `<textarea>`, cho phép người dùng luôn nhập/dán nội dung tự nhiên và kiểm tra giới hạn dung lượng tổng kèm cảnh báo màu sắc/thông báo lỗi trực quan.
- Không khóa cứng (`disabled`) nút Lưu khi thiếu trường; cho phép bấm nút để kích hoạt validation có thông điệp hướng dẫn rõ ràng (ví dụ: *"Tiêu đề là bắt buộc"*).
- Bổ sung cụm nút bấm thao tác tay (`[Thêm prompt / Lưu thay đổi]`, `[Huỷ sửa]`) nằm ngay bên dưới form nhập liệu, không để người dùng phải tìm kiếm ở góc footer xa xôi.
- Chuẩn hóa thông điệp lỗi khi lưu thất bại dựa trên mã lỗi thực tế của engine backend thay vì gán cứng câu lỗi "vượt quá 4.000 ký tự".

**Tech Stack:** TypeScript, Vanilla DOM (template literals), CSS3 (CSS Variables), Vitest (jsdom).

## Global Constraints

- Tuân thủ yêu cầu người dùng: **Không thêm phím tắt lưu (`Ctrl+Enter` / `Cmd+Enter`), toàn bộ thao tác sử dụng thao tác tay (chuột/click) trực quan.**
- Phạm vi thay đổi giới hạn trong `apps/desktop/src/main.ts`, `apps/desktop/src/styles.css` và `apps/desktop/tests/workflow.test.ts`.
- Đảm bảo toàn bộ 61/61 bài test hiện tại trong `apps/desktop` tiếp tục PASS (cập nhật các bài test kiểm tra trạng thái disabled trước đây sang kiểm tra validation message khi bấm lưu).

---

### Task 1: Ngăn chặn Treo Giao diện khi Mở Cài đặt (`openSettings` không nạp lại model nặng)

**Files:**
- Modify: `apps/desktop/src/main.ts:1407-1425`
- Test: `apps/desktop/tests/workflow.test.ts:930-945`

**Interfaces:**
- Consumes: `api.modelStatus(activate: boolean): Promise<ModelStatus>`
- Produces: `openSettings()` gọi `api.modelStatus(false)` thay vì `api.modelStatus(state.useModel)`.

- [x] **Step 1: Viết test kiểm tra `openSettings` gọi `api.modelStatus(false)`**

Mở `apps/desktop/tests/workflow.test.ts`, bổ sung test xác nhận khi click mở Cài đặt thì `api.modelStatus` được gọi với `false`:

```typescript
it("checks model status in non-activating mode when opening settings to avoid freezing UI", async () => {
  const api = await loadApp({
    modelStatus: vi.fn(() => Promise.resolve({ state: "ready", model_id: "test-model" })),
  });
  document.querySelector<HTMLButtonElement>("#settings")!.click();
  await vi.waitFor(() => expect(document.querySelector("#settings-page")).not.toBeNull());
  expect(api.modelStatus).toHaveBeenCalledWith(false);
});
```

- [x] **Step 2: Chạy test để xác nhận test thất bại**

Run: `npx vitest run tests/workflow.test.ts -t "checks model status in non-activating mode"`
Expected: FAIL (vì hiện tại đang gọi `api.modelStatus(state.useModel)`).

- [x] **Step 3: Cập nhật `openSettings` trong `apps/desktop/src/main.ts`**

Sửa dòng 1408 trong `apps/desktop/src/main.ts`:

```typescript
// Trước:
const [modelResult, customRulesResult, aiConfigResult] = await Promise.allSettled([
  api.modelStatus(state.useModel),
  api.customRuleList(),
  api.aiConfigGet(),
]);

// Sau (chỉ kiểm tra trạng thái snappily mà không kích hoạt nạp lại file model GGUF nặng):
const [modelResult, customRulesResult, aiConfigResult] = await Promise.allSettled([
  api.modelStatus(false),
  api.customRuleList(),
  api.aiConfigGet(),
]);
```

- [x] **Step 4: Chạy lại test để xác nhận PASS**

Run: `npx vitest run tests/workflow.test.ts -t "checks model status in non-activating mode"`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/main.ts apps/desktop/tests/workflow.test.ts
git commit -m "fix(desktop): query passive model status on opening settings to avoid freeze"
```

---

### Task 2: Loại bỏ Khóa Input Textarea & Xử lý Giới hạn Dung lượng Prompt Linh hoạt

**Files:**
- Modify: `apps/desktop/src/main.ts:709-725`, `apps/desktop/src/main.ts:1002-1016`, `apps/desktop/src/main.ts:1446-1465`
- Test: `apps/desktop/tests/workflow.test.ts:680-715`

**Interfaces:**
- Consumes: `customRulePromptLimit = 4000`, `customRuleCharacterCount(): number`
- Produces: Textarea luôn có `maxlength="4000"`, hiển thị cảnh báo khi `draftLength > available`, và validate khi lưu thay vì chặn gõ phím.

- [x] **Step 1: Viết test cho phép gõ và hiển thị cảnh báo dung lượng thay vì bị khóa input**

Trong `apps/desktop/tests/workflow.test.ts`, viết test kiểm tra textarea không bị gán `maxlength="0"` khi dung lượng tổng đã đầy:

```typescript
it("allows typing in prompt textarea even when aggregate budget is tight and warns clearly", async () => {
  await loadApp({
    customRuleList: () => Promise.resolve([
      customRule("rule-1", "a".repeat(3950)),
    ]),
  });
  document.querySelector<HTMLButtonElement>("#settings")!.click();
  await vi.waitFor(() => expect(document.querySelector("#custom-rule-prompt")).not.toBeNull());

  const prompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
  // Không bao giờ khóa cứng maxlength về 50 hay 0 khiến người dùng không gõ được
  expect(prompt.maxLength).toBe(4000);

  // Nhập nội dung vượt quá 50 ký tự còn lại
  prompt.value = "b".repeat(60);
  prompt.dispatchEvent(new InputEvent("input", { bubbles: true }));

  const counter = document.querySelector("#custom-rule-count")!;
  expect(counter.classList.contains("field__meta--overbudget")).toBe(true);
  expect(counter.textContent).toContain("Vượt quá dung lượng còn lại");
});
```

- [x] **Step 2: Chạy test để xác nhận test thất bại**

Run: `npx vitest run tests/workflow.test.ts -t "allows typing in prompt textarea even when aggregate budget is tight"`
Expected: FAIL.

- [x] **Step 3: Cập nhật template và logic tính toán trong `apps/desktop/src/main.ts`**

1. Tại `settingsContent` (dòng ~712):
```typescript
const editing = state.customRules.find(rule => rule.id === state.editingCustomRuleId);
const existingLength = editing ? [...editing.prompt].length : 0;
const available = Math.max(0, customRulePromptLimit - customRuleCharacterCount() + existingLength);
const draftLength = [...state.customRuleDraft].length;
const isOverBudget = draftLength > available;
```
Và trong HTML textarea:
```html
<textarea id="custom-rule-prompt" name="prompt" rows="7" required maxlength="${customRulePromptLimit}" aria-describedby="custom-rule-help custom-rule-count${state.customRulePromptInvalid ? " settings-message" : ""}" ${state.customRulePromptInvalid ? 'aria-invalid="true"' : ""} placeholder="Ví dụ: Dùng thuật ngữ “khách hàng”, không dùng “client”." ${controlsLocked ? "disabled" : ""}>${escape(state.customRuleDraft)}</textarea>
<div class="field__meta">
  <span class="field__helper" id="custom-rule-help">Không yêu cầu AI trả cả câu/đoạn hoặc tự đặt cấu trúc output. Tổng tất cả prompt tối đa 4.000 ký tự.</span>
  <small id="custom-rule-count" class="${isOverBudget ? "field__meta--overbudget" : ""}">${isOverBudget ? `Vượt quá dung lượng còn lại ${(draftLength - available).toLocaleString("vi-VN")} ký tự (còn ${available.toLocaleString("vi-VN")}/${customRulePromptLimit.toLocaleString("vi-VN")})` : `${draftLength.toLocaleString("vi-VN")}/${available.toLocaleString("vi-VN")} ký tự còn dùng được cho mục này`}</small>
</div>
```

2. Cập nhật sự kiện `input` của `#custom-rule-prompt` (dòng ~1002):
```typescript
document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")?.addEventListener("input", event => {
  state.customRuleDraft = (event.target as HTMLTextAreaElement).value;
  if (state.customRulePromptInvalid) {
    state.customRulePromptInvalid = false;
    state.settingsMessage = null;
    (event.target as HTMLTextAreaElement).removeAttribute("aria-invalid");
    (event.target as HTMLTextAreaElement).setAttribute("aria-describedby", "custom-rule-help custom-rule-count");
    document.querySelector("#settings-message")?.remove();
  }
  const editing = state.customRules.find(rule => rule.id === state.editingCustomRuleId);
  const existingLength = editing ? [...editing.prompt].length : 0;
  const available = Math.max(0, customRulePromptLimit - customRuleCharacterCount() + existingLength);
  const draftLength = [...state.customRuleDraft].length;
  const isOver = draftLength > available;
  const counter = document.querySelector<HTMLElement>("#custom-rule-count");
  if (counter) {
    counter.classList.toggle("field__meta--overbudget", isOver);
    counter.textContent = isOver
      ? `Vượt quá dung lượng còn lại ${(draftLength - available).toLocaleString("vi-VN")} ký tự (còn ${available.toLocaleString("vi-VN")}/${customRulePromptLimit.toLocaleString("vi-VN")})`
      : `${draftLength.toLocaleString("vi-VN")}/${available.toLocaleString("vi-VN")} ký tự còn dùng được cho mục này`;
  }
  updateCustomRuleSaveControl();
});
```

3. Trong `saveCustomRule`: Kiểm tra nếu `draftLength > available` thì chặn lưu và báo lỗi rõ ràng:
```typescript
if ([...prompt].length > available) {
  state.customRulePromptInvalid = true;
  state.settingsMessage = {
    tone: "error",
    text: `Nội dung prompt vượt quá dung lượng khả dụng còn lại (${available} ký tự). Hãy rút gọn bớt nội dung.`,
  };
  render("#custom-rule-prompt");
  return;
}
```

- [x] **Step 4: Chạy lại test để xác nhận PASS**

Run: `npx vitest run tests/workflow.test.ts -t "allows typing in prompt textarea even when aggregate budget is tight"`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/main.ts apps/desktop/tests/workflow.test.ts
git commit -m "fix(desktop): allow typing in custom rule textarea and show clear budget warning"
```

---

### Task 3: Đưa Cụm Nút Thao Tác Tay vào Form & Loại Bỏ Vô Hiệu Hóa Ngầm Nút Lưu

**Files:**
- Modify: `apps/desktop/src/main.ts:724-727`, `apps/desktop/src/main.ts:1140-1143`, `apps/desktop/src/main.ts:1446-1465`, `apps/desktop/src/styles.css`
- Test: `apps/desktop/tests/workflow.test.ts:732-752`

**Interfaces:**
- Produces:
  - Form actions đặt ngay trong `.prompt-manager__detail`: `<div class="prompt-form-actions">...</div>`.
  - Nút Submit không bị set `disabled` khi form chưa điền đủ; khi click sẽ tự kích hoạt validate và thông báo trường còn thiếu.
  - Tự động làm sạch ký tự xuống dòng / tab trong tiêu đề: `title.replace(/[\r\n\t]+/g, " ")`.

- [x] **Step 1: Viết test cho hành vi bấm nút Lưu khi chưa nhập tiêu đề hoặc prompt**

Cập nhật test `refuses to save a prompt without a title` trong `apps/desktop/tests/workflow.test.ts`:

```typescript
it("validates and focuses required fields with clear messages when clicking save", async () => {
  const api = await loadApp();
  document.querySelector<HTMLButtonElement>("#settings")!.click();
  await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#new-custom-rule")?.disabled).toBe(false));

  const prompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
  prompt.value = "Nội dung chưa có tiêu đề.";
  prompt.dispatchEvent(new InputEvent("input", { bubbles: true }));

  // Nút lưu không bị disable cứng, người dùng có thể thao tác tay bấm vào
  const submitBtn = document.querySelector<HTMLButtonElement>('button[type="submit"][form="custom-rule-form"]')!;
  expect(submitBtn.disabled).toBe(false);

  // Bấm lưu sẽ kích hoạt validate và hiện thông báo lỗi Tiêu đề bắt buộc
  submitBtn.click();
  await vi.waitFor(() => expect(document.querySelector<HTMLInputElement>("#custom-rule-title")?.getAttribute("aria-invalid")).toBe("true"));
  expect(document.body.textContent).toContain("Tiêu đề là bắt buộc");
  expect(api.customRuleUpsert).not.toHaveBeenCalled();
});
```

- [x] **Step 2: Chạy test để xác nhận test thất bại**

Run: `npx vitest run tests/workflow.test.ts -t "validates and focuses required fields with clear messages when clicking save"`
Expected: FAIL (vì trước đây nút bị `disabled = true`).

- [x] **Step 3: Cập nhật UI & Logic trong `apps/desktop/src/main.ts`**

1. Thêm cụm nút bấm thao tác tay trực tiếp vào bên trong `.prompt-manager__detail` (dưới checkbox "Chọn sẵn"):
```html
<div class="prompt-form-actions">
  <button class="button button--primary" type="submit" form="custom-rule-form" ${controlsLocked ? "disabled" : ""}>
    ${state.customRulePending ? "Đang lưu…" : editing ? "Lưu thay đổi" : "Thêm prompt"}
  </button>
  ${editing ? `<button class="button button--secondary" id="cancel-rule-edit" type="button" ${controlsLocked ? "disabled" : ""}>Huỷ sửa</button>` : ""}
</div>
```
*(Đồng thời giữ nút phụ tương ứng ở footer để đồng bộ).*

2. Cập nhật `updateCustomRuleSaveControl()`:
```typescript
function updateCustomRuleSaveControl(): void {
  const controlsLocked = state.customRulePending || state.settingsLoading;
  document.querySelectorAll<HTMLButtonElement>('button[type="submit"][form="custom-rule-form"]').forEach(submit => {
    submit.disabled = controlsLocked;
  });
}
```

3. Cập nhật `saveCustomRule(event: SubmitEvent)`:
```typescript
async function saveCustomRule(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  if (state.customRulePending) return;

  // Làm sạch tiêu đề: loại bỏ xuống dòng và tab để tránh lỗi từ engine
  const rawTitle = state.customRuleTitleDraft.replace(/[\r\n\t]+/g, " ");
  const title = rawTitle.trim().normalize("NFC");
  const prompt = state.customRuleDraft.trim().normalize("NFC");

  if (!title || [...title].length > customRuleTitleLimit) {
    state.customRuleTitleInvalid = true;
    state.settingsMessage = {
      tone: "error",
      text: !title ? "Tiêu đề là bắt buộc và tối đa 80 ký tự." : `Tiêu đề tối đa ${customRuleTitleLimit} ký tự.`,
    };
    render("#custom-rule-title");
    return;
  }

  if (!prompt) {
    state.customRulePromptInvalid = true;
    state.settingsMessage = {
      tone: "error",
      text: "Nội dung prompt là bắt buộc.",
    };
    render("#custom-rule-prompt");
    return;
  }
  // ... tiếp tục xử lý lưu
```

4. Cập nhật CSS trong `apps/desktop/src/styles.css`:
```css
.prompt-form-actions {
  display: flex;
  align-items: center;
  gap: var(--space-xs);
  margin-block-start: var(--space-xs);
  padding-block-start: var(--space-xs);
  border-block-start: var(--rule-thin) solid var(--color-rule);
}

.field__meta--overbudget {
  color: var(--color-danger, #dc2626) !important;
  font-weight: 600;
}
```

- [x] **Step 4: Chạy lại test để xác nhận PASS**

Run: `npx vitest run tests/workflow.test.ts -t "validates and focuses required fields with clear messages when clicking save"`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/main.ts apps/desktop/src/styles.css apps/desktop/tests/workflow.test.ts
git commit -m "feat(desktop): place manual action buttons directly in prompt form and enable active validation"
```

---

### Task 4: Báo Lỗi Chuẩn Xác từ Backend & Giữ Trạng Thái Sau khi Sửa Prompt

**Files:**
- Modify: `apps/desktop/src/main.ts:1466-1490`
- Test: `apps/desktop/tests/workflow.test.ts:829-847`

**Interfaces:**
- Consumes: Error exceptions từ `api.customRuleUpsert`
- Produces:
  - Báo đúng lỗi từ backend (`CUSTOM_RULE_INVALID_TITLE`, `CUSTOM_RULE_LIMIT_REACHED`, `CUSTOM_RULE_INVALID_PROMPT`, lỗi kết nối,...).
  - Giữ nguyên prompt đang sửa trong form kèm thông báo thành công `✓ Đã lưu thay đổi`, không xóa trắng form đột ngột.

- [x] **Step 1: Viết test kiểm tra phân biệt thông báo lỗi chuẩn xác và giữ state sau khi sửa**

Trong `apps/desktop/tests/workflow.test.ts`:

```typescript
it("displays specific backend error message when save fails", async () => {
  const api = await loadApp({
    customRuleUpsert: () => Promise.reject(new Error("CUSTOM_RULE_INVALID_TITLE")),
  });
  document.querySelector<HTMLButtonElement>("#settings")!.click();
  await vi.waitFor(() => expect(document.querySelector("#custom-rule-title")).not.toBeNull());

  fillPromptEditor("Tiêu đề lỗi", "Nội dung hợp lệ.");
  document.querySelector<HTMLFormElement>("#custom-rule-form")!.requestSubmit();

  await vi.waitFor(() => expect(api.customRuleUpsert).toHaveBeenCalledOnce());
  expect(document.querySelector("#settings-message")?.textContent).toContain("Tiêu đề không hợp lệ");
  expect(document.querySelector("#settings-message")?.textContent).not.toContain("4.000 ký tự");
});

it("preserves edited prompt in form and shows success tone after editing", async () => {
  const existing = customRule("rule-1", "Nội dung cũ", undefined, "Tiêu đề cũ");
  const api = await loadApp({
    customRuleList: () => Promise.resolve([existing]),
    customRuleUpsert: (id, title, prompt, isDefault) => Promise.resolve(customRule(id!, prompt, undefined, title, isDefault)),
  });
  document.querySelector<HTMLButtonElement>("#settings")!.click();
  await vi.waitFor(() => expect(document.querySelector('[data-edit-rule="rule-1"]')).not.toBeNull());

  document.querySelector<HTMLButtonElement>('[data-edit-rule="rule-1"]')!.click();
  fillPromptEditor("Tiêu đề mới", "Nội dung mới");
  document.querySelector<HTMLFormElement>("#custom-rule-form")!.requestSubmit();

  await vi.waitFor(() => expect(api.customRuleUpsert).toHaveBeenCalledOnce());
  expect(document.querySelector("#settings-message")?.textContent).toContain("Đã lưu thay đổi prompt");
  // Vẫn giữ lại dữ liệu vừa sửa trong editor để người dùng yên tâm
  expect(document.querySelector<HTMLInputElement>("#custom-rule-title")!.value).toBe("Tiêu đề mới");
  expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!.value).toBe("Nội dung mới");
});
```

- [x] **Step 2: Chạy test để xác nhận test thất bại**

Run: `npx vitest run tests/workflow.test.ts -t "displays specific backend error message when save fails"`
Expected: FAIL.

- [x] **Step 3: Cập nhật hàm `saveCustomRule` trong `apps/desktop/src/main.ts`**

```typescript
  try {
    const saved = await api.customRuleUpsert(state.editingCustomRuleId, title, prompt, state.customRuleDefaultDraft);
    if (operation !== customRuleOperationSequence) return;
    const existingIndex = state.customRules.findIndex(rule => rule.id === saved.id);
    state.customRules = existingIndex >= 0
      ? state.customRules.map(rule => rule.id === saved.id ? saved : rule)
      : [...state.customRules, saved];
    state.customRules.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    applyRuleDefaultSelection(saved);

    const isEditMode = state.editingCustomRuleId !== null;
    if (isEditMode) {
      // Khi sửa: giữ nguyên dữ liệu trên form và thông báo rõ đã lưu
      state.customRuleTitleDraft = saved.title;
      state.customRuleDraft = saved.prompt;
      state.customRuleDefaultDraft = saved.is_default;
      state.settingsMessage = { tone: "status", text: "Đã lưu thay đổi prompt." };
    } else {
      // Khi thêm mới: dọn form để người dùng có thể thêm tiếp prompt khác nếu muốn
      state.customRuleTitleDraft = "";
      state.customRuleDraft = "";
      state.customRuleDefaultDraft = false;
      state.editingCustomRuleId = null;
      state.settingsMessage = { tone: "status", text: "Đã thêm prompt mới." };
    }
    state.lastDeletedRule = null;
  } catch (error: any) {
    if (operation === customRuleOperationSequence) {
      const errStr = String(error?.message || error || "");
      if (errStr.includes("CUSTOM_RULE_INVALID_TITLE")) {
        state.customRuleTitleInvalid = true;
        state.settingsMessage = { tone: "error", text: "Tiêu đề không hợp lệ. Vui lòng kiểm tra lại." };
      } else if (errStr.includes("CUSTOM_RULE_INVALID_PROMPT")) {
        state.customRulePromptInvalid = true;
        state.settingsMessage = { tone: "error", text: "Nội dung prompt không hợp lệ." };
      } else if (errStr.includes("CUSTOM_RULE_LIMIT_REACHED")) {
        state.customRulePromptInvalid = true;
        state.settingsMessage = {
          tone: "error",
          text: state.customRules.length >= 100
            ? "Đã đạt giới hạn tối đa 100 quy tắc riêng."
            : "Tổng dung lượng tất cả prompt đã vượt quá 4.000 ký tự cho phép.",
        };
      } else {
        state.customRulePromptInvalid = true;
        state.settingsMessage = { tone: "error", text: "Không lưu được prompt. Vui lòng thử lại." };
      }
    }
  } finally {
    if (operation === customRuleOperationSequence) {
      state.customRulePending = false;
      render("#custom-rule-prompt");
    }
  }
```

- [x] **Step 4: Chạy lại test để xác nhận PASS**

Run: `npx vitest run tests/workflow.test.ts -t "displays specific backend error message when save fails"`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add apps/desktop/src/main.ts apps/desktop/tests/workflow.test.ts
git commit -m "fix(desktop): report accurate error codes on rule save and preserve form on edit"
```

---

### Task 5: Cải thiện Hướng dẫn ở Bước 2 khi Chưa Bật AI

**Files:**
- Modify: `apps/desktop/src/main.ts:333-359`
- Test: `apps/desktop/tests/workflow.test.ts:1120-1135`

**Interfaces:**
- Produces: Cung cấp thông báo hướng dẫn rõ ràng kèm nút mở nhanh Cài đặt AI khi prompt riêng chưa thể chọn do AI chưa bật.

- [x] **Step 1: Viết test kiểm tra thông báo hướng dẫn khi chưa bật AI**

Trong `apps/desktop/tests/workflow.test.ts`:

```typescript
it("displays helpful action guidance when prompts exist but AI is inactive", async () => {
  await loadApp({
    customRuleList: () => Promise.resolve([customRule("rule-1", "Prompt 1")]),
    modelStatus: () => Promise.resolve({ state: "not_installed" }),
  });
  await chooseDocument();
  expect(document.body.textContent).toContain("Prompt riêng cần AI rà soát");
  expect(document.querySelector("#open-model-settings")).not.toBeNull();
});
```

- [x] **Step 2: Chạy test để xác nhận PASS**

Run: `npx vitest run tests/workflow.test.ts -t "displays helpful action guidance when prompts exist but AI is inactive"`
Expected: PASS.

- [x] **Step 3: Rà soát và hoàn thiện giao diện thông báo trong `apps/desktop/src/main.ts`**

Đảm bảo văn phong dễ hiểu, không tạo cảm giác báo lỗi đỏ làm người dùng hoang mang:
```typescript
const warningHtml = count > 0 && !applies
  ? `<p class="settings-message settings-message--status" role="status">Prompt riêng sẽ tự động được kích hoạt khi bật AI rà soát (Mô hình Offline hoặc Cloud). <button class="inline-button" id="open-model-settings" type="button">Cấu hình AI ngay</button></p>`
  : "";
```

- [x] **Step 4: Commit**

```bash
git add apps/desktop/src/main.ts apps/desktop/tests/workflow.test.ts
git commit -m "polish(desktop): clarify custom rule applicability guidance in review step"
```

---

### Task 6: Kiểm thử Toàn diện & Xác minh (Verification)

**Files:**
- Test: `apps/desktop/tests/workflow.test.ts`
- Test: `engine/tests/test_custom_rules.py`

- [x] **Step 1: Chạy toàn bộ test suite frontend**

Run: `npm test` trong thư mục `apps/desktop`
Expected: Toàn bộ các bài test (61+ tests) đều PASS 100%.

- [x] **Step 2: Chạy toàn bộ test suite backend**

Run: `pytest engine/tests/test_custom_rules.py`
Expected: Toàn bộ tests cho custom rules trong engine Python đều PASS 100%.

- [x] **Step 3: Kiểm tra Build ứng dụng**

Run: `npm run build` trong `apps/desktop`
Expected: TypeScript compile và Vite build thành công không có lỗi lint hay type error.

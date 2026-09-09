# Startup Loading Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hiển thị lớp phủ mờ (Loading Overlay) khi khởi động ứng dụng để chặn tương tác chuột/phím và thông báo "Đang chuẩn bị ứng dụng, vui lòng chờ…" trong khi hệ thống tải cấu hình và nạp mô hình AI, hỗ trợ xử lý lỗi kèm nút "Thử lại".

**Architecture:** Bổ sung `appInitializing: boolean` và `appInitError: string | null` vào trạng thái toàn cục `state` trong `main.ts`. Khi `appInitializing === true`, giao diện render thêm thẻ overlay với spinner và thông báo, đồng thời khóa tương tác của lớp giao diện bên dưới. Đóng gói logic khởi động thành `initializeApp()` có khả năng gọi lại khi người dùng bấm nút "Thử lại".

**Tech Stack:** TypeScript, Vanilla DOM (template literals), CSS3 (CSS Variables, `backdrop-filter`), Vitest (jsdom).

## Global Constraints

- Phạm vi thay đổi giới hạn trong `apps/desktop/src/main.ts`, `apps/desktop/src/styles.css` và `apps/desktop/tests/workflow.test.ts`.
- Bảo toàn tương thích ngược cho toàn bộ 59 test hiện có trong bộ test suite của `apps/desktop`.
- Lớp phủ overlay sử dụng `role="dialog"` và `aria-modal="true"` để đảm bảo khả năng tiếp cận (accessibility).
- Không cho phép người dùng bỏ qua loading khi chưa xong; nếu lỗi xảy ra, phải hiển thị thông báo lỗi và nút "Thử lại".

---

### Task 1: CSS Styles cho Startup Loading Overlay

**Files:**
- Modify: `apps/desktop/src/styles.css:2260-2264`

**Interfaces:**
- Produces: CSS classes `.app-init-overlay`, `.app-init-card`, `.app-init-card--error`, `.app-init-title`, `.app-init-message`, `.app-init-error-icon`.

- [ ] **Step 1: Viết mã CSS cho overlay và card thông báo**

Thêm các lớp CSS sau vào cuối tệp `apps/desktop/src/styles.css`:

```css
/* Startup Loading Overlay */
.app-init-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  padding: 1.5rem;
  background: rgba(15, 23, 42, 0.5);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

.app-init-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1rem;
  padding: 2rem 2.5rem;
  max-inline-size: 28rem;
  inline-size: 100%;
  background: var(--color-surface, #ffffff);
  border: 1px solid var(--color-rule, #e2e8f0);
  border-radius: var(--radius-card, 0.75rem);
  box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  text-align: center;
}

.app-init-card .spinner {
  inline-size: 2.25rem;
  block-size: 2.25rem;
}

.app-init-title {
  margin: 0;
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-text, #0f172a);
}

.app-init-card--error {
  border-color: var(--color-danger, #ef4444);
}

.app-init-error-icon {
  font-size: 2rem;
  line-height: 1;
}

.app-init-message {
  margin: 0;
  font-size: 0.875rem;
  color: var(--color-muted, #64748b);
  line-height: 1.4;
}
```

- [ ] **Step 2: Xác minh cú pháp CSS và build**

Run: `npm run build` trong `apps/desktop`
Expected: PASS (không có lỗi build CSS/TypeScript).

- [ ] **Step 3: Commit CSS styles**

```bash
git add apps/desktop/src/styles.css
git commit -m "style: add startup loading overlay classes"
```

---

### Task 2: Trạng thái, Render Overlay và Logic Khởi tạo `initializeApp()` trong `main.ts`

**Files:**
- Modify: `apps/desktop/src/main.ts`
- Test: `apps/desktop/tests/workflow.test.ts`

**Interfaces:**
- Consumes: `.app-init-overlay`, `.app-init-card`, `.spinner` từ `styles.css`.
- Produces: `state.appInitializing`, `state.appInitError`, hàm `initOverlayHtml()`, hàm `initializeApp()`.

- [ ] **Step 1: Viết failing tests cho Startup Loading Overlay trong `workflow.test.ts`**

Mở `apps/desktop/tests/workflow.test.ts`, bổ sung test suite `startup loading overlay` vào cuối file:

```typescript
describe("startup loading overlay", () => {
  it("renders loading overlay while app is initializing and dismisses it when ready", async () => {
    const aiConfigDeferred = deferred<any>();
    loadApp({
      aiConfigGet: () => aiConfigDeferred.promise,
    });

    await vi.waitFor(() => {
      const overlay = document.querySelector(".app-init-overlay");
      expect(overlay).not.toBeNull();
      expect(overlay?.getAttribute("role")).toBe("dialog");
      expect(overlay?.getAttribute("aria-modal")).toBe("true");
      expect(document.querySelector("#app-init-title")?.textContent).toContain("Đang chuẩn bị ứng dụng, vui lòng chờ…");
      expect(document.querySelector(".app-init-card .spinner")).not.toBeNull();
    });

    // Resolve initial background call
    aiConfigDeferred.resolve({
      active_provider: "local",
      configs: [
        { provider: "openai", base_url: "https://api.openai.com/v1", model_name: "gpt-4o-mini", masked_key: "sk-1234" },
        { provider: "gemini", base_url: "https://generativelanguage.googleapis.com/v1beta", model_name: "gemini-2.5-flash", masked_key: "AIza...5678" },
      ],
    });

    await vi.waitFor(() => {
      expect(document.querySelector(".app-init-overlay")).toBeNull();
      expect(document.querySelector("#choose")).not.toBeNull();
    });
  });

  it("displays error card and retries when initialization fails", async () => {
    let callCount = 0;
    const aiConfigMock = vi.fn(() => {
      callCount += 1;
      if (callCount === 1) {
        return Promise.reject(new Error("ENGINE_DISCONNECTED"));
      }
      return Promise.resolve({
        active_provider: "local",
        configs: [],
      });
    });

    await loadApp({
      aiConfigGet: aiConfigMock,
    });

    // Expect error card and retry button
    await vi.waitFor(() => {
      const overlay = document.querySelector(".app-init-overlay");
      expect(overlay).not.toBeNull();
      expect(document.querySelector(".app-init-card--error")).not.toBeNull();
      expect(document.querySelector("#retry-app-init")).not.toBeNull();
    });

    // Click retry
    document.querySelector<HTMLButtonElement>("#retry-app-init")!.click();

    // After retry succeeds, overlay should be dismissed
    await vi.waitFor(() => {
      expect(document.querySelector(".app-init-overlay")).toBeNull();
    });
  });
});
```

- [ ] **Step 2: Chạy test để xác nhận test fails**

Run: `npx vitest run tests/workflow.test.ts -t "startup loading overlay"` trong `apps/desktop`
Expected: FAIL (vì chưa có `.app-init-overlay` và `state.appInitializing`).

- [ ] **Step 3: Triển khai mã nguồn trong `apps/desktop/src/main.ts`**

1. Cập nhật `interface State` và object `state`:
```typescript
// Trong interface State:
  appInitializing: boolean;
  appInitError: string | null;

// Trong khởi tạo state:
  appInitializing: true,
  appInitError: null,
```

2. Thêm hàm `initOverlayHtml()`:
```typescript
function initOverlayHtml(): string {
  if (!state.appInitializing) return "";
  if (state.appInitError) {
    return `<div class="app-init-overlay" role="dialog" aria-modal="true" aria-labelledby="app-init-title">
      <div class="app-init-card app-init-card--error" role="alert">
        <div class="app-init-error-icon" aria-hidden="true">⚠️</div>
        <h2 class="app-init-title" id="app-init-title">Không thể chuẩn bị ứng dụng</h2>
        <p class="app-init-message">${escape(state.appInitError)}</p>
        <button class="button button--primary" id="retry-app-init" type="button">Thử lại</button>
      </div>
    </div>`;
  }
  return `<div class="app-init-overlay" role="dialog" aria-modal="true" aria-labelledby="app-init-title">
    <div class="app-init-card" role="status">
      <div class="spinner" aria-hidden="true"></div>
      <p class="app-init-title" id="app-init-title">Đang chuẩn bị ứng dụng, vui lòng chờ…</p>
    </div>
  </div>`;
}
```

3. Cập nhật hàm `render()`:
Trong `render()`, thêm `${initOverlayHtml()}` vào cuối HTML được gán cho `target.innerHTML`.
Đồng thời, khi `state.appInitializing === true`, thêm thuộc tính `inert` vào container chính bên dưới (hoặc `main`) để chặn bàn phím và chuột tương tác với màn hình bên dưới.

4. Bổ sung event listener cho `#retry-app-init` trong `renderListeners()`:
```typescript
  document.querySelector("#retry-app-init")?.addEventListener("click", () => {
    void initializeApp();
  });
```

5. Đóng gói logic khởi động thành `initializeApp()` và gọi khi mở app:
```typescript
let initSequence = 0;
async function initializeApp(): Promise<void> {
  const currentInit = ++initSequence;
  state.appInitializing = true;
  state.appInitError = null;
  render();

  try {
    const initialModelPreference = loadModelPreference();
    const modelSequence = modelOperationSequence;
    const statusRequest = ++modelStatusRequestSequence;
    const customRuleSequence = customRuleOperationSequence;
    const request = ++customRuleRequestSequence;

    const [aiConfigRes, rulesRes, seq2seqRes] = await Promise.allSettled([
      api.aiConfigGet(),
      api.customRuleList(),
      api.seq2seqConfigGet().catch(() => null),
    ]);

    if (currentInit !== initSequence) return;

    if (seq2seqRes.status === "fulfilled" && seq2seqRes.value) {
      state.seq2seqConfig = seq2seqRes.value;
    }
    if (aiConfigRes.status === "fulfilled") {
      state.aiConfig = aiConfigRes.value;
      syncCloudDraftsFromConfig();
      if (isCloudActive()) {
        state.selectedProviderTab = state.aiConfig.active_provider as ProviderTab;
        state.useModel = true;
        state.fullReview = true;
      }
    } else if (aiConfigRes.status === "rejected") {
      throw new Error("Không thể kết nối với dịch vụ cấu hình AI.");
    }

    if (rulesRes.status === "fulfilled" && customRuleSequence === customRuleOperationSequence && request === customRuleRequestSequence) {
      state.customRules = rulesRes.value;
      syncDefaultRuleSelection();
    }
    state.includeRuleFindings = false;
    clearUnavailableFullReview();
    render();

    const modelRes = await api.modelStatus(initialModelPreference);
    if (currentInit !== initSequence) return;

    if (modelRes && modelSequence === modelOperationSequence && statusRequest === modelStatusRequestSequence) {
      state.model = modelRes;
      if (!isCloudActive()) {
        state.useModel = initialModelPreference && modelCanFilter(modelRes);
        state.fullReview = state.useModel && modelRes.capabilities?.full_review === true;
      }
      state.includeRuleFindings = false;
      clearUnavailableFullReview();
    }

    state.appInitializing = false;
    state.appInitError = null;
    render();
  } catch (error: any) {
    if (currentInit !== initSequence) return;
    state.appInitializing = true;
    state.appInitError = error?.message || "Không thể khởi động dịch vụ ứng dụng. Hãy thử lại.";
    render();
  }
}

// Khởi chạy khi script được tải
void initializeApp();
```

- [ ] **Step 4: Chạy test để xác nhận test passes**

Run: `npx vitest run tests/workflow.test.ts -t "startup loading overlay"` trong `apps/desktop`
Expected: PASS.

- [ ] **Step 5: Commit implementation và test**

```bash
git add apps/desktop/src/main.ts apps/desktop/tests/workflow.test.ts
git commit -m "feat: add startup loading overlay and initialization lifecycle"
```

---

### Task 3: Xác minh toàn diện (Full Verification)

**Files:**
- N/A

- [ ] **Step 1: Chạy toàn bộ test suite của `apps/desktop`**

Run: `npm test` trong `apps/desktop`
Expected: Toàn bộ tests (61+ tests) đều PASS.

- [ ] **Step 2: Chạy kiểm tra build và type-checking**

Run: `npm run build` trong `apps/desktop`
Expected: `tsc && vite build` thành công không có lỗi.

# AI Model Selection & Configuration UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the AI Model Settings screen to decouple AI provider configuration from activation, featuring a prominent top-level Active AI Selector (Radio Cards) with automatic navigation/focus for unconfigured providers and independent Save/Activate actions.

**Architecture:** 
1. Backend Sidecar: Ensure `ai_config.update` preserves the current `is_active` status of a provider if `is_active` is omitted.
2. Frontend Layout & Styles: Implement two distinct visual blocks in `#settings-models`: an Active AI Selector (3 radio cards with live status) and detail configuration tabs with independent "Kiểm tra kết nối", "Lưu cấu hình", and "Kích hoạt nguồn này" controls.
3. Frontend State & Interactions: Handle active provider switching with readiness checks (redirecting to tab + focusing missing input if unconfigured), and separate config saving from provider activation.

**Tech Stack:** TypeScript, Vite, Vitest, Vanilla DOM, CSS custom properties, Python 3.12, SQLite.

## Global Constraints

- Preserve all existing custom rule and seq2seq decoupling behaviors.
- Maintain consistent status and error messages in Vietnamese.
- Mask saved API keys (`sk-...xxxx` / `AIza...xxxx`) and preserve existing keys when input is left empty.
- Keep controls locked and accessible during async operations with appropriate `aria-busy` and disabled states.

---

### Task 1: Sidecar Active State Preservation on Config Update

**Files:**
- Modify: `engine/src/soatvan/entrypoints/sidecar.py:354-357`
- Test: `engine/tests/test_sidecar.py`

**Interfaces:**
- Consumes: `params: dict[str, Any]` in `ai_config_update`
- Produces: Preserved `is_active` if `params.get("is_active") is None`

- [ ] **Step 1: Write failing test in `engine/tests/test_sidecar.py`**

```python
def test_sidecar_ai_config_update_preserves_active_state_when_omitted(tmp_path, monkeypatch) -> None:
    sidecar = Sidecar(local_data=tmp_path)
    # Configure and activate openai
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "api_key": "sk-test-key",
            "base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "is_active": True,
        },
    )
    get_res = sidecar.dispatch("ai_config.get", {})
    assert get_res["result"]["active_provider"] == "openai"

    # Update base_url without specifying is_active
    sidecar.dispatch(
        "ai_config.update",
        {
            "provider": "openai",
            "base_url": "https://custom.endpoint.com/v1",
        },
    )
    get_res_after = sidecar.dispatch("ai_config.get", {})
    # Active provider should remain openai
    assert get_res_after["result"]["active_provider"] == "openai"
    cfg = next(c for c in get_res_after["result"]["configs"] if c["provider"] == "openai")
    assert cfg["is_active"] is True
    assert cfg["base_url"] == "https://custom.endpoint.com/v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --project engine pytest engine/tests/test_sidecar.py -k test_sidecar_ai_config_update_preserves_active_state_when_omitted -v`
Expected: FAIL (because currently `is_active` defaults to `False`, deactivating openai).

- [ ] **Step 3: Update `ai_config_update` in `engine/src/soatvan/entrypoints/sidecar.py`**

In `engine/src/soatvan/entrypoints/sidecar.py:354-357`, replace:
```python
        is_active = params.get("is_active", False)
        if not isinstance(is_active, bool):
            raise ValueError("INVALID_PARAMS")
```
with:
```python
        is_active_param = params.get("is_active")
        stored = self.ai_config.get_config(provider)
        if is_active_param is None:
            is_active = stored.is_active if stored else False
        elif isinstance(is_active_param, bool):
            is_active = is_active_param
        else:
            raise ValueError("INVALID_PARAMS")
```
*(Move `stored = self.ai_config.get_config(provider)` up before `is_active_param` check).*

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --project engine pytest engine/tests/test_sidecar.py engine/tests/test_ai_config_repository.py -v`
Expected: PASS (all 20+ tests passing).

- [ ] **Step 5: Commit**

```bash
git add engine/src/soatvan/entrypoints/sidecar.py engine/tests/test_sidecar.py
git commit -m "fix(engine): preserve is_active state when omitted in ai_config.update"
```

---

### Task 2: UI Styles for Active AI Selector and Detail Configuration Tabs

**Files:**
- Modify: `apps/desktop/src/styles.css`

**Interfaces:**
- Consumes: CSS tokens (`--color-accent`, `--color-paper-raised`, `--color-status-success`, etc.)
- Produces: CSS classes `.active-ai-selector`, `.active-ai-card`, `.active-ai-radio`, `.config-tabs`, `.config-tab`, `.config-tab__badge`, etc.

- [ ] **Step 1: Add CSS rules for Active AI Selector and Config Tabs in `apps/desktop/src/styles.css`**

Replace/enhance `.provider-tabs` and `.provider-tab` rules in `apps/desktop/src/styles.css`:
```css
/* Active AI Selector (Top Block) */
.active-ai-section {
  margin-block-end: var(--space-md);
  padding: var(--space-xs);
  border: var(--rule-thin) solid var(--color-rule-strong);
  border-radius: var(--radius-md);
  background: var(--color-paper-sunken);
}

.active-ai-section__header {
  margin-block-end: var(--space-2xs);
}

.active-ai-section__header h3 {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 700;
  color: var(--color-ink);
}

.active-ai-section__header p {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-muted);
}

.active-ai-selector {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2xs);
  margin-block-start: var(--space-2xs);
}

.active-ai-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-3xs);
  padding: var(--space-xs);
  border: var(--rule-thin) solid var(--color-rule);
  border-radius: var(--radius-sm);
  background: var(--color-paper-raised);
  color: var(--color-ink);
  cursor: pointer;
  text-align: start;
  position: relative;
  transition: border-color var(--dur-micro) var(--ease-out), background-color var(--dur-micro) var(--ease-out);
}

.active-ai-card:hover:not(:disabled) {
  border-color: var(--color-accent);
}

.active-ai-card:focus-visible {
  outline: var(--rule-focus) solid var(--color-focus);
  outline-offset: var(--rule-focus-offset);
}

.active-ai-card.active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.active-ai-card__top {
  display: flex;
  align-items: center;
  gap: var(--space-2xs);
  width: 100%;
}

.active-ai-radio {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 2px solid var(--color-rule-strong);
  flex-shrink: 0;
}

.active-ai-card.active .active-ai-radio {
  border-color: var(--color-accent);
}

.active-ai-card.active .active-ai-radio::after {
  content: "";
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
}

.active-ai-card__title {
  font-size: var(--text-sm);
  font-weight: 700;
  color: var(--color-ink);
  line-height: 1.2;
}

.active-ai-card__subtitle {
  font-size: var(--text-xs);
  color: var(--color-muted);
  line-height: 1.3;
}

.active-ai-card__badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-block-start: auto;
  font-size: 11px;
  font-weight: 700;
  color: var(--color-accent);
}

.active-ai-card__badge::before {
  content: "●";
  font-size: 8px;
}

/* Detail Configuration Tabs (Bottom Block) */
.config-tabs-container {
  margin-block-start: var(--space-sm);
}

.config-tabs {
  display: flex;
  gap: var(--space-3xs);
  border-bottom: var(--rule-thin) solid var(--color-rule-strong);
  margin-block-end: var(--space-xs);
}

.config-tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3xs);
  padding: var(--space-2xs) var(--space-sm);
  border: var(--rule-thin) solid transparent;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--color-ink-2);
  font-size: var(--text-sm);
  font-weight: 600;
  cursor: pointer;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0;
}

.config-tab:hover:not(:disabled) {
  color: var(--color-ink);
  background: var(--color-paper-raised);
}

.config-tab.selected {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
  background: var(--color-paper-raised);
  font-weight: 700;
}

.config-tab__active-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-accent);
  display: inline-block;
}
```

- [ ] **Step 2: Run build to ensure CSS syntax is valid**

Run: `npm --prefix apps/desktop run build`
Expected: Build passes with no CSS errors.

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/src/styles.css
git commit -m "style(desktop): add active ai selector and configuration tabs styles"
```

---

### Task 3: HTML Structure & Interaction Logic in `apps/desktop/src/main.ts`

**Files:**
- Modify: `apps/desktop/src/main.ts`

**Interfaces:**
- Consumes: `state.aiConfig`, `state.model`, `state.selectedProviderTab`, `state.cloudDrafts`
- Produces:
  - `activeAiSelectorHtml()`: renders top 3 radio cards with readiness labels.
  - `configTabsHtml()`: renders 3 configuration tabs.
  - `handleActiveAiSelect(provider: "local" | "openai" | "gemini")`: switches active provider if ready, or redirects to tab + focuses missing input with guidance message if not ready.
  - `saveCloudConfigOnly()`: saves API key/base URL/model name without altering active provider.
  - `activateCurrentProvider(provider)`: activates chosen provider and syncs state.

- [ ] **Step 1: Helper functions for readiness check & subtitle text**

In `apps/desktop/src/main.ts`, add helper functions:
```typescript
function isProviderReady(provider: ProviderTab): boolean {
  if (provider === "local") {
    return state.model.state === "ready" || state.model.state === "installed";
  }
  const cfg = state.aiConfig?.configs.find(c => c.provider === provider);
  const draft = state.cloudDrafts[provider];
  return Boolean((cfg && cfg.api_key) || (draft && draft.apiKey.trim()));
}

function providerStatusSubtitle(provider: ProviderTab): string {
  if (provider === "local") {
    const installed = state.model.state === "ready" || state.model.state === "installed";
    if (!installed) return "Chưa nạp file model";
    const ver = state.model.version ? `v${state.model.version}` : "";
    return ver ? `Đã cài đặt (${ver})` : "Đã cài đặt model";
  }
  const cfg = state.aiConfig?.configs.find(c => c.provider === provider);
  const draft = state.cloudDrafts[provider];
  if (cfg?.masked_key) return `Đã lưu key: ${cfg.masked_key}`;
  if (draft?.apiKey.trim()) return "Đã nhập key mới";
  return "Chưa lưu API key";
}
```

- [ ] **Step 2: Render Top Active AI Selector & Config Tabs in `settingsContent("models")`**

Update `settingsContent("models")`:
```typescript
  const activeProvider = state.aiConfig.active_provider;
  const currentTab = state.selectedProviderTab;

  const activeAiSelectorHtml = `
    <section class="active-ai-section" aria-label="Nguồn AI rà soát chính">
      <header class="active-ai-section__header">
        <h3>Nguồn AI rà soát văn bản</h3>
        <p>Chọn 1 nguồn AI duy nhất để kiểm tra và rà soát tài liệu.</p>
      </header>
      <div class="active-ai-selector" role="radiogroup" aria-label="Chọn nguồn AI kích hoạt">
        <button class="active-ai-card ${activeProvider === "local" ? "active" : ""}" id="active-ai-card-local" data-active-ai-select="local" type="button" role="radio" aria-checked="${activeProvider === "local"}" ${controlsLocked ? "disabled" : ""}>
          <div class="active-ai-card__top">
            <span class="active-ai-radio" aria-hidden="true"></span>
            <span class="active-ai-card__title">Mô hình cục bộ</span>
          </div>
          <span class="active-ai-card__subtitle">${escape(providerStatusSubtitle("local"))}</span>
          ${activeProvider === "local" ? `<span class="active-ai-card__badge">Đang hoạt động</span>` : ""}
        </button>
        <button class="active-ai-card ${activeProvider === "openai" ? "active" : ""}" id="active-ai-card-openai" data-active-ai-select="openai" type="button" role="radio" aria-checked="${activeProvider === "openai"}" ${controlsLocked ? "disabled" : ""}>
          <div class="active-ai-card__top">
            <span class="active-ai-radio" aria-hidden="true"></span>
            <span class="active-ai-card__title">OpenAI / Tương thích</span>
          </div>
          <span class="active-ai-card__subtitle">${escape(providerStatusSubtitle("openai"))}</span>
          ${activeProvider === "openai" ? `<span class="active-ai-card__badge">Đang hoạt động</span>` : ""}
        </button>
        <button class="active-ai-card ${activeProvider === "gemini" ? "active" : ""}" id="active-ai-card-gemini" data-active-ai-select="gemini" type="button" role="radio" aria-checked="${activeProvider === "gemini"}" ${controlsLocked ? "disabled" : ""}>
          <div class="active-ai-card__top">
            <span class="active-ai-radio" aria-hidden="true"></span>
            <span class="active-ai-card__title">Google Gemini API</span>
          </div>
          <span class="active-ai-card__subtitle">${escape(providerStatusSubtitle("gemini"))}</span>
          ${activeProvider === "gemini" ? `<span class="active-ai-card__badge">Đang hoạt động</span>` : ""}
        </button>
      </div>
    </section>`;

  const configTabsHtml = `
    <div class="config-tabs-container">
      <div class="config-tabs" role="tablist" aria-label="Cấu hình chi tiết nguồn AI">
        <button class="config-tab ${currentTab === "local" ? "selected" : ""}" id="config-tab-local" data-provider-tab="local" type="button" role="tab" aria-selected="${currentTab === "local"}" ${controlsLocked ? "disabled" : ""}>
          🖥️ Mô hình cục bộ ${activeProvider === "local" ? `<span class="config-tab__active-dot" title="Đang kích hoạt"></span>` : ""}
        </button>
        <button class="config-tab ${currentTab === "openai" ? "selected" : ""}" id="config-tab-openai" data-provider-tab="openai" type="button" role="tab" aria-selected="${currentTab === "openai"}" ${controlsLocked ? "disabled" : ""}>
          🌐 OpenAI / Tương thích ${activeProvider === "openai" ? `<span class="config-tab__active-dot" title="Đang kích hoạt"></span>` : ""}
        </button>
        <button class="config-tab ${currentTab === "gemini" ? "selected" : ""}" id="config-tab-gemini" data-provider-tab="gemini" type="button" role="tab" aria-selected="${currentTab === "gemini"}" ${controlsLocked ? "disabled" : ""}>
          ✨ Google Gemini API ${activeProvider === "gemini" ? `<span class="config-tab__active-dot" title="Đang kích hoạt"></span>` : ""}
        </button>
      </div>
    </div>`;
```

- [ ] **Step 3: Update Tab Footer Buttons (Separate Save vs Activate)**

For Cloud tab:
```typescript
    footer: `<footer class="settings-footer">
      <div class="settings-footer__inner">
        <div class="button-row" style="justify-content: space-between; width: 100%;">
          <button class="button button--secondary" id="cloud-test-connection" type="button" ${state.cloudTestLoading || controlsLocked ? 'disabled aria-busy="true"' : ""}>
            ${state.cloudTestLoading ? "Đang kiểm tra…" : "Kiểm tra kết nối"}
          </button>
          <div class="button-row">
            <button class="button button--secondary" id="cloud-save-config" type="button" ${state.cloudSaving || controlsLocked ? 'disabled aria-busy="true"' : ""}>
              ${state.cloudSaving ? "Đang lưu…" : "Lưu cấu hình"}
            </button>
            <button class="button button--primary" id="provider-activate-btn" type="button" ${isCloudCurrentActive || controlsLocked ? "disabled" : ""}>
              ${isCloudCurrentActive ? "✓ Đang kích hoạt" : "Kích hoạt nguồn này"}
            </button>
          </div>
        </div>
      </div>
    </footer>`,
```

For Local tab:
Inside `ggufModelCardHtml`, if model is installed and not active, render `[Kích hoạt nguồn này]` or in the card.

- [ ] **Step 4: Implement Event Handlers in `apps/desktop/src/main.ts`**

1. `handleActiveAiSelect(provider: ProviderTab)`:
```typescript
async function handleActiveAiSelect(provider: ProviderTab): Promise<void> {
  if (state.settingsLoading || state.cloudSaving || modelOperationBusy()) return;
  if (!isProviderReady(provider)) {
    state.selectedProviderTab = provider;
    state.settingsMessage = {
      tone: "status",
      text: provider === "local"
        ? "Vui lòng nhập file model GGUF trước khi kích hoạt mô hình cục bộ."
        : `Vui lòng nhập API Key và lưu cấu hình trước khi kích hoạt ${provider === "openai" ? "OpenAI" : "Gemini"}.`,
    };
    render(provider === "local" ? "#model-import" : "#cloud-api-key");
    return;
  }
  await activateProvider(provider);
}
```

2. `saveCloudConfigOnly()`:
```typescript
async function saveCloudConfigOnly(): Promise<void> {
  const provider = state.selectedProviderTab;
  if (provider === "local" || state.cloudSaving) return;
  syncInputDrafts();
  const draft = state.cloudDrafts[provider];
  const saved = state.aiConfig?.configs.find(c => c.provider === provider);
  if (!draft.apiKey.trim() && (!saved || !saved.api_key)) {
    state.settingsMessage = { tone: "error", text: "Vui lòng nhập API Key trước khi lưu cấu hình." };
    render("#cloud-api-key");
    return;
  }
  state.cloudSaving = true;
  state.settingsMessage = null;
  render("#cloud-save-config");
  try {
    await api.aiConfigUpdate({
      provider,
      apiKey: draft.apiKey.trim() || undefined,
      baseUrl: draft.baseUrl.trim() || undefined,
      modelName: draft.modelName.trim() || undefined,
    });
    state.aiConfig = await api.aiConfigGet();
    state.cloudDrafts[provider].apiKey = "";
    state.settingsMessage = { tone: "status", text: `Đã lưu cấu hình ${provider === "openai" ? "OpenAI / Tương thích" : "Google Gemini"}.` };
  } catch {
    state.settingsMessage = { tone: "error", text: `Không lưu được cấu hình ${provider === "openai" ? "OpenAI" : "Gemini"}. Hãy thử lại.` };
  } finally {
    state.cloudSaving = false;
    render();
  }
}
```

3. `activateProvider(provider: ProviderTab)`:
```typescript
async function activateProvider(provider: ProviderTab): Promise<void> {
  if (provider !== "local") {
    syncInputDrafts();
    const draft = state.cloudDrafts[provider];
    if (draft.apiKey.trim() || draft.baseUrl.trim() || draft.modelName.trim()) {
      await api.aiConfigUpdate({
        provider,
        apiKey: draft.apiKey.trim() || undefined,
        baseUrl: draft.baseUrl.trim() || undefined,
        modelName: draft.modelName.trim() || undefined,
      });
      draft.apiKey = "";
    }
  }
  await api.aiConfigSetActive(provider);
  state.aiConfig = await api.aiConfigGet();
  state.model = await api.modelStatus(true);
  state.useModel = provider !== "local" || modelCanFilter(state.model);
  state.fullReview = state.useModel && (provider !== "local" || state.model.capabilities?.full_review === true);
  saveModelPreference(true);
  state.selectedProviderTab = provider;
  state.settingsMessage = {
    tone: "status",
    text: `Đã kích hoạt ${provider === "local" ? "Mô hình cục bộ (Offline)" : provider === "openai" ? "OpenAI / Tương thích" : "Google Gemini"} làm nguồn rà soát chính.`,
  };
  render();
}
```

4. Bind `[data-active-ai-select]`, `[data-provider-tab]`, `#cloud-save-config`, `#provider-activate-btn` in `bind()`.

- [ ] **Step 5: Verify build with TypeScript compiler**

Run: `npm --prefix apps/desktop run build`
Expected: Build passes with 0 type errors.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/main.ts
git commit -m "feat(desktop): implement top active ai selector and separate save/activate actions"
```

---

### Task 4: Vitest Integration Tests in `apps/desktop/tests/workflow.test.ts`

**Files:**
- Modify: `apps/desktop/tests/workflow.test.ts`

**Interfaces:**
- Consumes: `loadApp` mock harness in `workflow.test.ts`
- Produces: Test coverage for:
  - Top Active AI Selector switching between configured providers.
  - Unconfigured provider radio click redirecting to tab and focusing input.
  - "Lưu cấu hình" saving without changing active provider.
  - "Kích hoạt nguồn này" activating provider and updating workflow badge.

- [ ] **Step 1: Write integration tests in `apps/desktop/tests/workflow.test.ts`**

Add test suite:
```typescript
describe("AI model selection and configuration UX", () => {
  it("shows active AI provider in top selector radio cards", async () => {
    await loadApp({
      aiConfigGet: () => Promise.resolve({
        active_provider: "local",
        configs: [],
      }),
    });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();

    await vi.waitFor(() => expect(document.querySelector("#active-ai-card-local")?.classList.contains("active")).toBe(true));
    expect(document.querySelector("#active-ai-card-openai")?.classList.contains("active")).toBe(false);
  });

  it("redirects and prompts user when clicking an unconfigured AI provider radio", async () => {
    await loadApp({
      aiConfigGet: () => Promise.resolve({
        active_provider: "local",
        configs: [],
      }),
    });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();

    // Click OpenAI radio card when no API key configured
    document.querySelector<HTMLButtonElement>("#active-ai-card-openai")!.click();
    await vi.waitFor(() => {
      expect(document.querySelector("#config-tab-openai")?.classList.contains("selected")).toBe(true);
      expect(document.body.textContent).toContain("Vui lòng nhập API Key");
    });
    // Active radio remains local
    expect(document.querySelector("#active-ai-card-local")?.classList.contains("active")).toBe(true);
  });

  it("saves configuration without changing the active provider", async () => {
    const api = await loadApp({
      aiConfigGet: () => Promise.resolve({
        active_provider: "local",
        configs: [],
      }),
    });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();

    // Switch to OpenAI detail tab
    document.querySelector<HTMLButtonElement>("#config-tab-openai")!.click();
    const keyInput = document.querySelector<HTMLInputElement>("#cloud-api-key")!;
    keyInput.value = "sk-saved-only-key";
    keyInput.dispatchEvent(new InputEvent("input", { bubbles: true }));

    // Click Save Config
    document.querySelector<HTMLButtonElement>("#cloud-save-config")!.click();
    await vi.waitFor(() => expect(api.aiConfigUpdate).toHaveBeenCalledWith(expect.objectContaining({
      provider: "openai",
      apiKey: "sk-saved-only-key",
    })));
    // Should NOT have called setActive
    expect(api.aiConfigSetActive).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(document.body.textContent).toContain("Đã lưu cấu hình OpenAI"));
  });

  it("activates provider via button and updates top selector and workflow badge", async () => {
    const api = await loadApp({
      aiConfigGet: () => Promise.resolve({
        active_provider: "local",
        configs: [
          { provider: "openai", api_key: "sk-test", masked_key: "sk-...test", base_url: "https://api.openai.com/v1", model_name: "gpt-4o-mini", is_active: false },
        ],
      }),
    });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();

    // Switch to OpenAI tab and click Activate
    document.querySelector<HTMLButtonElement>("#config-tab-openai")!.click();
    document.querySelector<HTMLButtonElement>("#provider-activate-btn")!.click();
    await vi.waitFor(() => expect(api.aiConfigSetActive).toHaveBeenCalledWith("openai"));
  });
});
```

- [ ] **Step 2: Run all vitest tests**

Run: `npm --prefix apps/desktop test`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add apps/desktop/tests/workflow.test.ts
git commit -m "test(desktop): add vitest coverage for active ai selector and decoupled save/activate"
```

---

### Task 5: Final Verification & Clean-up

- [ ] **Step 1: Run full test suite (backend & frontend)**
  - Run: `uv run --project engine pytest engine/tests/ -v`
  - Run: `npm --prefix apps/desktop test`
  - Run: `npm --prefix apps/desktop run build`
- [ ] **Step 2: Verify git status is clean**
  - Run: `git status`

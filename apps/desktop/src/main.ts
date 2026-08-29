import "./styles.css";
import { api } from "./api";
import type {
  AiConfigEntry,
  AiConfigState,
  AiTestConnectionResult,
  CustomRule,
  DocumentInfo,
  JobResult,
  ModelStatus,
  Preset,
  RuleOptions,
  Step,
} from "./contracts";

const app = document.querySelector<HTMLDivElement>("#app")!;
let renderedStep: Step | null = null;
let renderedView: AppView["kind"] | null = null;
let documentSelectionSequence = 0;
let outputActionSequence = 0;
let settingsRequestSequence = 0;
let modelOperationSequence = 0;
let customRuleOperationSequence = 0;
let modelStatusRequestSequence = 0;
let customRuleRequestSequence = 0;
let modelOperationBaseline: { model: ModelStatus; useModel: boolean } | null = null;
type SettingsSection = "prompts" | "review-rules" | "models";
type ProviderTab = "local" | "openai" | "gemini";
type AppView =
  | { kind: "workflow" }
  | { kind: "settings"; section: SettingsSection; returnFocus: string };
type SettingsMessage = { tone: "error" | "status"; text: string } | null;
type PendingPromptAction =
  | { kind: "close"; preferredReturnFocus?: string }
  | { kind: "section"; section: SettingsSection }
  | { kind: "new" }
  | { kind: "edit"; id: string };
type OutputAction = "open" | "reveal";

const defaultAiConfigs: Record<string, AiConfigEntry> = {
  openai: {
    provider: "openai",
    base_url: "https://api.openai.com/v1",
    model_name: "gpt-4o-mini",
    temperature: 0.0,
    timeout_seconds: 180,
    is_active: false,
  },
  gemini: {
    provider: "gemini",
    base_url: "https://generativelanguage.googleapis.com/v1beta",
    model_name: "gemini-2.5-flash",
    temperature: 0.0,
    timeout_seconds: 180,
    is_active: false,
  },
};

const state: {
  view: AppView;
  step: Step;
  document: DocumentInfo | null;
  useModel: boolean;
  fullReview: boolean;
  includeRuleFindings: boolean;
  jobId: string;
  progress: number;
  progressStage: string;
  jobStarting: boolean;
  cancelPending: boolean;
  result: JobResult | null;
  model: ModelStatus;
  aiConfig: AiConfigState;
  selectedProviderTab: ProviderTab;
  cloudDrafts: Record<string, { apiKey: string; baseUrl: string; modelName: string }>;
  cloudTestLoading: boolean;
  cloudTestResult: AiTestConnectionResult | null;
  cloudSaving: boolean;
  settingsLoading: boolean;
  customRules: CustomRule[];
  selectedRuleIds: string[];
  customRuleTitleDraft: string;
  customRuleDraft: string;
  customRuleDefaultDraft: boolean;
  editingCustomRuleId: string | null;
  customRulePending: boolean;
  customRuleTitleInvalid: boolean;
  customRulePromptInvalid: boolean;
  pendingPromptAction: PendingPromptAction | null;
  settingsMessage: SettingsMessage;
  lastDeletedRule: CustomRule | null;
  error: string;
  modelRemovalPending: boolean;
  modelRemovalRunning: boolean;
  outputActionPending: OutputAction | null;
  appVersion: string;
} = {
  view: { kind: "workflow" },
  step: "file",
  document: null,
  jobId: "",
  progress: 0,
  progressStage: "",
  jobStarting: false,
  cancelPending: false,
  useModel: false,
  fullReview: false,
  includeRuleFindings: false,
  result: null,
  model: { state: "not_installed" },
  aiConfig: {
    active_provider: "local",
    configs: [defaultAiConfigs.openai, defaultAiConfigs.gemini],
  },
  selectedProviderTab: "local",
  cloudDrafts: {
    openai: { apiKey: "", baseUrl: "https://api.openai.com/v1", modelName: "gpt-4o-mini" },
    gemini: { apiKey: "", baseUrl: "https://generativelanguage.googleapis.com/v1beta", modelName: "gemini-2.5-flash" },
  },
  cloudTestLoading: false,
  cloudTestResult: null,
  cloudSaving: false,
  settingsLoading: false,
  customRules: [],
  selectedRuleIds: [],
  customRuleTitleDraft: "",
  customRuleDraft: "",
  customRuleDefaultDraft: false,
  editingCustomRuleId: null,
  customRulePending: false,
  customRuleTitleInvalid: false,
  customRulePromptInvalid: false,
  pendingPromptAction: null,
  settingsMessage: null,
  lastDeletedRule: null,
  error: "",
  modelRemovalPending: false,
  modelRemovalRunning: false,
  outputActionPending: null,
  appVersion: "0.1.5",
};

const defaultPreset: Preset = "standard";
const defaultRuleOptions: RuleOptions = {
  technical: true,
  repeated_words: true,
  confusions: true,
  syllables: true,
  administrative_capitalization: false,
};
const fixedReviewRules: { id: keyof RuleOptions; name: string; description: string }[] = [
  { id: "technical", name: "Khoảng trắng và dấu câu", description: "Phát hiện khoảng trắng thừa hoặc thiếu và dấu câu đặt sai vị trí." },
  { id: "repeated_words", name: "Từ lặp", description: "Phát hiện từ bị lặp liên tiếp ngoài chủ ý." },
  { id: "confusions", name: "Từ và cụm từ dễ nhầm", description: "Đối chiếu danh sách những cách viết tiếng Việt thường bị nhầm lẫn." },
  { id: "syllables", name: "Âm tiết tiếng Việt", description: "Phát hiện thận trọng các âm tiết có phụ âm đầu không hợp lệ." },
  { id: "administrative_capitalization", name: "Viết hoa hành chính", description: "Kiểm tra quy tắc viết hoa theo Nghị định 30/2020, Phụ lục II." },
];
const customRulePromptLimit = 4000;
const customRuleTitleLimit = 80;
const genericProcessingError = "Không xử lý được tệp. Hãy kiểm tra tệp rồi thử lại; tệp gốc chưa bị thay đổi.";
const processingErrorMessages: Record<string, string> = {
  CUSTOM_PROMPT_CONTEXT_EXCEEDED: "Quy tắc riêng quá dài so với dung lượng ngữ cảnh đang dùng. Hãy rút gọn quy tắc hoặc chọn model có context lớn hơn. Tệp gốc chưa bị thay đổi.",
  MODEL_REVIEW_CONTEXT_TOO_SMALL: "Dung lượng ngữ cảnh của model quá nhỏ để rà soát sâu. Hãy chọn model có context lớn hơn. Tệp gốc chưa bị thay đổi.",
};

function processingErrorMessage(error: unknown): string {
  const code = typeof error === "string"
    ? error
    : error instanceof Error
      ? error.message
      : typeof error === "object" && error !== null && "code" in error && typeof error.code === "string"
        ? error.code
        : "";
  return processingErrorMessages[code] ?? genericProcessingError;
}

function loadModelPreference(): boolean { try { return localStorage.getItem("soatvan.use-model.v1") !== "false"; } catch { return true; } }
function saveModelPreference(enabled: boolean): void { try { localStorage.setItem("soatvan.use-model.v1", String(enabled)); } catch { /* Storage can be unavailable in hardened WebViews. */ } }

function escape(value: string): string { const node = document.createElement("div"); node.textContent = value; return node.innerHTML; }
function formatBytes(value: number): string { return `${(value / 1024 / 1024).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} MB`; }
function focusSelectorFor(element: Element | null): string | null {
  if (!(element instanceof HTMLElement)) return null;
  if (element.id) return `#${element.id}`;
  if (element.dataset.settingsSection) return `[data-settings-section="${element.dataset.settingsSection}"]`;
  if (element.dataset.providerTab) return `[data-provider-tab="${element.dataset.providerTab}"]`;
  if (element.dataset.selectRule) return `[data-select-rule="${element.dataset.selectRule}"]`;
  if (element.dataset.editRule) return `[data-edit-rule="${element.dataset.editRule}"]`;
  if (element.dataset.deleteRule) return `[data-delete-rule="${element.dataset.deleteRule}"]`;
  return null;
}
function documentMetadata(doc: DocumentInfo): string {
  const pages = doc.page_count ? `${doc.page_count.toLocaleString("vi-VN")} trang · ` : "";
  return `${formatBytes(doc.size)} · ${pages}${doc.word_count.toLocaleString("vi-VN")} từ · ${doc.paragraph_count.toLocaleString("vi-VN")} đoạn · ${doc.table_cell_count.toLocaleString("vi-VN")} ô bảng`;
}

function isCloudActive(): boolean {
  return state.aiConfig.active_provider === "openai" || state.aiConfig.active_provider === "gemini";
}

function activeAiLabel(): string {
  if (state.aiConfig.active_provider === "openai") {
    const cfg = state.aiConfig.configs.find(c => c.provider === "openai");
    const model = cfg?.model_name || state.cloudDrafts.openai?.modelName || "gpt-4o-mini";
    return `AI Cloud: OpenAI (${model})`;
  }
  if (state.aiConfig.active_provider === "gemini") {
    const cfg = state.aiConfig.configs.find(c => c.provider === "gemini");
    const model = cfg?.model_name || state.cloudDrafts.gemini?.modelName || "gemini-2.5-flash";
    return `AI Cloud: Gemini (${model})`;
  }
  return "AI Cục bộ";
}

function modelCanFilter(model: ModelStatus): boolean { return model.state === "ready" && model.capabilities?.candidate_filter === true; }
function modelFilterAvailable(): boolean {
  if (isCloudActive()) return true;
  return modelCanFilter(state.model);
}
function fullReviewAvailable(): boolean {
  if (isCloudActive()) return true;
  if (!state.useModel) return false;
  return modelCanFilter(state.model) && state.model.capabilities?.full_review === true;
}
function clearUnavailableFullReview(): void {
  if (!fullReviewAvailable()) state.fullReview = false;
  if (!state.fullReview) state.includeRuleFindings = false;
}

function syncCloudDraftsFromConfig(): void {
  for (const cfg of state.aiConfig.configs) {
    if (cfg.provider === "openai" || cfg.provider === "gemini") {
      state.cloudDrafts[cfg.provider] = {
        apiKey: "",
        baseUrl: cfg.base_url || defaultAiConfigs[cfg.provider]?.base_url || "",
        modelName: cfg.model_name || defaultAiConfigs[cfg.provider]?.model_name || "",
      };
    }
  }
}

function reviewModeDescription(): string {
  if (isCloudActive()) {
    return `${activeAiLabel()} sẽ rà soát và tự tìm lỗi trên toàn bộ nội dung văn bản.`;
  }
  return state.useModel
    ? "AI sẽ dùng prompt tiếng Việt mặc định để rà soát và tự tìm lỗi trên toàn bộ nội dung văn bản."
    : "Bộ quy tắc kiểm tra cơ bản sẽ được áp dụng cho tài liệu.";
}

function selectedCustomRules(): CustomRule[] { return state.customRules.filter(rule => state.selectedRuleIds.includes(rule.id)); }
function compiledCustomPrompt(): string { return (isCloudActive() || state.useModel) ? selectedCustomRules().map(rule => rule.prompt).join("\n\n") : ""; }

function syncDefaultRuleSelection(): void { state.selectedRuleIds = state.customRules.filter(rule => rule.is_default).map(rule => rule.id); }
function customRuleCharacterCount(): number { return state.customRules.reduce((total, rule) => total + [...rule.prompt].length, 0); }
function modelOperationBusy(): boolean { return state.modelRemovalRunning || ["importing", "verifying"].includes(state.model.state); }
function isWorkflowView(): boolean { return state.view.kind === "workflow"; }
function settingsOperationLocked(): boolean { return modelOperationBusy() || modelOperationBaseline !== null || state.modelRemovalPending || state.customRulePending || state.cloudTestLoading || state.cloudSaving; }
function settingsSectionNavigationLocked(): boolean { return state.settingsLoading || settingsOperationLocked(); }
function customRuleDraftDirty(): boolean {
  const editing = state.customRules.find(rule => rule.id === state.editingCustomRuleId);
  return editing
    ? state.customRuleTitleDraft !== editing.title
      || state.customRuleDraft !== editing.prompt
      || state.customRuleDefaultDraft !== editing.is_default
    : state.customRuleTitleDraft.length > 0 || state.customRuleDraft.length > 0 || state.customRuleDefaultDraft;
}
function settingsBusyMessage(): SettingsMessage {
  if (state.customRulePending) return { tone: "status", text: "Đang cập nhật prompt. Hãy chờ thao tác hoàn tất." };
  if (state.cloudSaving) return { tone: "status", text: "Đang lưu cấu hình AI Cloud. Hãy chờ thao tác hoàn tất." };
  if (state.cloudTestLoading) return { tone: "status", text: "Đang kiểm tra kết nối AI Cloud. Hãy chờ thao tác hoàn tất." };
  if (state.modelRemovalRunning) return { tone: "status", text: "Đang gỡ model khỏi máy. Hãy chờ thao tác hoàn tất." };
  if (state.modelRemovalPending) return { tone: "status", text: "Hãy chọn giữ lại hoặc gỡ model trước khi rời mục này." };
  if (state.model.state === "importing") return { tone: "status", text: "Đang nhập gói model. Bạn có thể huỷ thao tác bằng nút bên dưới." };
  if (state.model.state === "verifying") return { tone: "status", text: "Đang xác minh và khởi động model. Hãy chờ thao tác hoàn tất." };
  if (modelOperationBaseline !== null) return { tone: "status", text: "Đang cập nhật trạng thái AI cục bộ. Hãy chờ thao tác hoàn tất." };
  return null;
}
function settingsSectionHeading(section: SettingsSection): string {
  return section === "prompts" ? "#settings-prompts-title" : section === "review-rules" ? "#settings-review-rules-title" : "#settings-models-title";
}

function customRuleSelectionHtml(): string {
  const count = state.customRules.length;
  const cloud = isCloudActive();
  const applies = count > 0 && (cloud || (state.useModel && modelFilterAvailable()));
  const selected = state.selectedRuleIds.filter(id => state.customRules.some(rule => rule.id === id)).length;
  const summary = count === 0
    ? "Không có yêu cầu bổ sung; AI vẫn dùng prompt mặc định."
    : applies
      ? `Đang chọn ${selected.toLocaleString("vi-VN")}/${count.toLocaleString("vi-VN")}; các prompt được chọn sẽ gộp thành yêu cầu bổ sung cho prompt mặc định.`
      : cloud
        ? "Chưa được áp dụng vì AI đang tắt."
        : "Chưa được áp dụng vì AI cục bộ đang tắt hoặc chưa sẵn sàng.";
  const picker = count === 0
    ? `<div class="empty-state"><strong>Chưa có prompt riêng.</strong><span>Tạo prompt trong Cài đặt để cung cấp thuật ngữ hoặc tiêu chí riêng cho AI.</span></div>`
    : `<ul class="prompt-picker">${state.customRules.map(rule => {
        const id = escape(rule.id);
        const checked = state.selectedRuleIds.includes(rule.id) ? "checked" : "";
        return `<li class="prompt-picker__item"><label class="setting-row prompt-picker__row"><span><strong>${escape(rule.title)}</strong></span><input type="checkbox" data-select-rule="${id}" ${checked} ${applies ? "" : "disabled"}></label></li>`;
      }).join("")}</ul>`;
  return `<fieldset class="prompt-picker-group"><legend class="sr-only">Quy tắc riêng áp dụng cho lần rà soát này</legend><div class="prompt-picker-group__header"><div><strong>${count.toLocaleString("vi-VN")} quy tắc riêng</strong><span>${summary}</span></div></div>${picker}</fieldset>${count > 0 && !applies ? `<p class="settings-message settings-message--error" role="alert">${cloud ? "Bật AI để áp dụng các quy tắc riêng." : "Bật AI cục bộ để áp dụng các quy tắc riêng."} <button class="inline-button" id="open-model-settings" type="button">Thiết lập AI</button></p>` : ""}`;
}

function render(preferredFocus?: string): void {
  const previousFocus = focusSelectorFor(document.activeElement);
  const workflowView = isWorkflowView();
  const focusStep = workflowView && renderedStep !== null && renderedStep !== state.step;
  const focusSettings = renderedView !== "settings" && state.view.kind === "settings";
  const doc = state.document;
  const reviewNavDisabled = state.view.kind === "settings" && settingsOperationLocked();
  app.innerHTML = `
    <header class="app-header">
      <div class="brand"><span class="brand__mark" aria-hidden="true">SV</span><div><strong>SoátVăn</strong><span>Kiểm tra văn bản trên máy</span></div></div>
      <nav class="app-header__nav" aria-label="Điều hướng chính">
        <button class="button button--quiet" id="review-nav" type="button" ${workflowView ? 'aria-current="page"' : ""} ${reviewNavDisabled ? 'aria-disabled="true" aria-describedby="settings-message"' : ""}>Rà soát</button>
        <button class="button button--quiet" id="settings" type="button" ${state.view.kind === "settings" ? 'aria-current="page"' : ""} ${workflowView && state.step === "processing" ? "disabled" : ""}>Cài đặt</button>
      </nav>
    </header>
    ${workflowView ? `<nav class="progress-strip" aria-label="Tiến trình kiểm tra">
      <ol class="stepper">${["Chọn tệp", "Chuẩn bị", "Xử lý", "Kết quả"].map((label, index) => {
        const current = stepIndex() === index;
        const complete = stepIndex() > index;
        return `<li ${current ? 'aria-current="step"' : ""} data-complete="${complete}"><span aria-hidden="true">${complete ? "✓" : index + 1}</span><strong>${label}</strong><span class="sr-only">${current ? "Bước hiện tại" : complete ? "Đã hoàn thành" : "Chưa thực hiện"}</span></li>`;
      }).join("")}</ol>
    </nav>
    <main class="workflow-shell">
      ${state.step === "file" ? `<section class="workflow-card workflow-card--file"><div class="section-copy"><h1>Chọn tệp Word cần kiểm tra</h1><p>Ứng dụng tạo một bản kết quả mới và luôn giữ nguyên tệp gốc.</p></div><button class="drop-zone" id="choose"><strong>Chọn hoặc kéo thả tệp .docx</strong><span>Nhấn Ctrl+O để mở nhanh</span></button>${errorHtml()}</section>` : ""}
      ${state.step === "rules" && doc ? `<section class="workflow-card"><div class="workflow-context"><button class="back-button" id="back">← Chọn tệp khác</button><div class="file-chip"><strong>${escape(doc.name)}</strong><span>${documentMetadata(doc)}</span></div></div><div class="workflow-lead"><div class="section-copy"><div class="ai-provider-badge" id="workflow-ai-badge"><span class="badge-dot ${isCloudActive() ? "badge-dot--cloud" : "badge-dot--local"}"></span><span>${escape(activeAiLabel())}</span></div><h1>Chuẩn bị rà soát</h1><p>${reviewModeDescription()}</p></div><div class="workflow-actions workflow-actions--lead"><button class="button button--primary" id="start">Bắt đầu xử lý</button></div></div>${customRuleSelectionHtml()}${errorHtml()}</section>` : ""}
      ${state.step === "processing" ? `<section class="workflow-card processing-panel"><div class="processing-status"><span class="spinner" aria-hidden="true"></span><div class="section-copy"><h1>${progressTitle()}</h1><p>${isCloudActive() ? "Đang xử lý phân tích văn bản qua API AI." : "Mọi xử lý tài liệu diễn ra trên máy này."}</p></div></div><div class="progress-row"><div class="progress" role="progressbar" aria-label="Tiến độ xử lý" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${state.progress}"><span style="--progress-scale:${state.progress / 100}"></span></div><strong class="progress-value">${state.progress}%</strong></div><p class="sr-only progress-announcement" aria-live="polite" aria-atomic="true">${progressTitle()} ${state.progress}%</p><div class="workflow-actions"><button class="button button--secondary" id="cancel" ${state.jobStarting || state.cancelPending ? "disabled" : ""} ${state.jobStarting || state.cancelPending ? 'aria-busy="true"' : ""}>${state.jobStarting ? "Đang chuẩn bị…" : state.cancelPending ? "Đang dừng…" : "Dừng xử lý"}</button></div>${errorHtml()}</section>` : ""}
      ${(state.step === "result" || state.step === "no-findings") ? resultHtml() : ""}
    </main>` : settingsHtml()}
    <footer class="status-bar">${isCloudActive() ? `<span><span aria-hidden="true">☁</span> ${escape(activeAiLabel())} · Gửi dữ liệu qua API đám mây</span>` : `<span><span aria-hidden="true">●</span> Xử lý cục bộ · không gửi nội dung tài liệu lên mạng</span>`}<span>v${escape(state.appVersion)}</span></footer>`;
  bind();
  renderedStep = state.step;
  renderedView = state.view.kind;
  const focusTarget = preferredFocus ?? (focusSettings ? "#settings-title" : focusStep ? ".workflow-card h1" : previousFocus ?? (state.view.kind === "settings" && settingsOperationLocked() ? "#settings-message" : undefined));
  if (focusTarget) queueMicrotask(() => {
    let target = document.querySelector<HTMLElement>(focusTarget);
    const unavailableDuringSettingsOperation = state.view.kind === "settings"
      && settingsOperationLocked()
      && target?.matches(":disabled, [aria-disabled=\"true\"]");
    if (unavailableDuringSettingsOperation) target = document.querySelector<HTMLElement>("#settings-message");
    if (!target) {
      target = state.view.kind === "workflow"
        ? document.querySelector<HTMLElement>(".workflow-card h1") ?? document.querySelector<HTMLElement>("#review-nav")
        : document.querySelector<HTMLElement>(settingsSectionHeading(state.view.section)) ?? document.querySelector<HTMLElement>("#settings-title");
    }
    if (target?.matches("h1, h2, h3")) target.setAttribute("tabindex", "-1");
    target?.focus({ preventScroll: true });
  });
}

function stepIndex(): number { return state.step === "file" ? 0 : state.step === "rules" ? 1 : state.step === "processing" ? 2 : 3; }
function errorHtml(): string { return state.error ? `<p class="error" role="alert">${escape(state.error)}</p>` : ""; }
const progressStageRanks: Record<string, number> = { reading: 0, rules: 1, model: 2, validating: 3, exporting: 4, complete: 5 };
function progressTitle(): string {
  if (state.progressStage === "model") return state.fullReview ? `Đang rà soát thân bài và bảng bằng ${activeAiLabel()}…` : `Đang phân loại các trường hợp nghi ngờ bằng ${activeAiLabel()}…`;
  const titles: Record<string, string> = {
    reading: "Đang đọc cấu trúc tệp Word…",
    rules: "Đang áp dụng quy tắc…",
    validating: "Đang kiểm tra vị trí cảnh báo…",
    exporting: "Đang tạo tệp kết quả…",
    complete: "Đang hoàn tất xử lý…",
  };
  return titles[state.progressStage] ?? (state.progress < 25 ? titles.reading : state.progress < 75 ? titles.rules : state.progress < 90 ? titles.validating : titles.exporting);
}
function applyProgress(stage: string, percent: number): void {
  const previousProgress = state.progress;
  const normalized = Number.isFinite(percent) ? Math.min(100, Math.max(0, Math.round(percent))) : previousProgress;
  state.progress = Math.max(previousProgress, normalized);
  const currentRank = progressStageRanks[state.progressStage];
  const nextRank = progressStageRanks[stage];
  if (!state.progressStage || (nextRank !== undefined && (currentRank === undefined || nextRank >= currentRank)) || (nextRank === undefined && state.progress > previousProgress)) {
    state.progressStage = stage;
  }
  const title = progressTitle();
  const heading = document.querySelector<HTMLElement>(".workflow-card h1");
  const progress = document.querySelector<HTMLElement>('[role="progressbar"]');
  const progressFill = progress?.querySelector<HTMLElement>("span");
  const value = document.querySelector<HTMLElement>(".progress-value");
  const announcement = document.querySelector<HTMLElement>(".progress-announcement");
  if (heading) heading.textContent = title;
  progress?.setAttribute("aria-valuenow", String(state.progress));
  progressFill?.style.setProperty("--progress-scale", String(state.progress / 100));
  if (value) value.textContent = `${state.progress}%`;
  if (announcement) announcement.textContent = `${title} ${state.progress}%`;
}
function updateCancelControl(): void {
  const button = document.querySelector<HTMLButtonElement>("#cancel");
  if (!button) return;
  const pending = state.jobStarting || state.cancelPending;
  button.disabled = pending;
  if (pending) button.setAttribute("aria-busy", "true");
  else button.removeAttribute("aria-busy");
  button.textContent = state.jobStarting ? "Đang chuẩn bị…" : state.cancelPending ? "Đang dừng…" : "Dừng xử lý";
}
function isPartialReview(): boolean { return state.result?.review?.status === "partial" || state.result?.status === "partial"; }
function reviewCoverageWarningHtml(): string {
  const review = state.result?.review;
  if (!isPartialReview()) return "";
  if (!review) return `<aside class="review-warning" role="alert"><strong>Rà soát sâu chưa hoàn tất</strong><p>AI chưa rà xong toàn bộ nội dung; không thể kết luận tài liệu không có lỗi.</p></aside>`;
  const reviewed = review.reviewed_chunks.toLocaleString("vi-VN");
  const total = review.total_chunks.toLocaleString("vi-VN");
  const failed = review.failed_chunks.toLocaleString("vi-VN");
  const reasons = [
    review.timeout_chunks ? `${review.timeout_chunks.toLocaleString("vi-VN")} quá thời gian` : "",
    review.invalid_output_chunks ? `${review.invalid_output_chunks.toLocaleString("vi-VN")} trả kết quả không hợp lệ` : "",
    review.inference_error_chunks ? `${review.inference_error_chunks.toLocaleString("vi-VN")} lỗi xử lý model` : "",
  ].filter(Boolean).join("; ");
  const retry = review.retried_chunks
    ? ` Đã tự chia nhỏ và thử lại ${review.retried_chunks.toLocaleString("vi-VN")} phần${review.recovered_chunks ? `, khôi phục hoàn tất ${review.recovered_chunks.toLocaleString("vi-VN")} phần` : ""}.`
    : "";
  return `<aside class="review-warning" role="alert"><strong>Rà soát sâu chưa hoàn tất</strong><p>AI đã rà ${reviewed}/${total} phần nội dung; ${failed} phần không hoàn tất${reasons ? ` (${reasons})` : ""}.${retry} Không thể kết luận toàn bộ tài liệu không có lỗi.</p></aside>`;
}
function resultHtml(): string {
  const partial = isPartialReview();
  const path = state.result?.output_path ?? "";
  if (partial && !path) return `<section class="workflow-card result-panel"><div class="result-heading"><div class="partial-symbol" aria-hidden="true">!</div><div class="section-copy"><h1>Rà soát chỉ hoàn tất một phần</h1><p>Không tạo bản sao vì chưa ghi nhận cảnh báo. Tệp gốc vẫn giữ nguyên.</p></div></div>${reviewCoverageWarningHtml()}<div class="workflow-actions"><button class="button button--primary" id="restart">Kiểm tra tệp khác</button></div>${errorHtml()}</section>`;
  if (state.step === "no-findings") return `<section class="workflow-card result-panel"><div class="result-heading"><div class="success" aria-hidden="true">✓</div><div class="section-copy"><h1>Không phát hiện cảnh báo</h1><p>Không tạo bản sao; tệp gốc vẫn giữ nguyên.</p></div></div><div class="workflow-actions"><button class="button button--primary" id="restart">Kiểm tra tệp khác</button></div>${errorHtml()}</section>`;
  const outputPending = state.outputActionPending !== null;
  return `<section class="workflow-card result-panel"><div class="result-heading"><div class="${partial ? "partial-symbol" : "success"}" aria-hidden="true">${partial ? "!" : "✓"}</div><div class="section-copy"><h1>${partial ? "Đã tạo tệp kết quả khi AI rà chưa hết" : "Đã tạo tệp kết quả"}</h1><p>${partial ? "Tệp gồm các cảnh báo AI đã ghi nhận ở phần xử lý thành công; một số nội dung chưa được rà hoàn tất." : "Ứng dụng không xem trước tài liệu. Hãy mở bằng Microsoft Word để xem các vị trí được đánh dấu."}</p></div></div>${reviewCoverageWarningHtml()}<div class="result-file"><strong>${escape(path.split(/[\\/]/).pop() ?? path)}</strong><span>${escape(path)}</span></div><dl class="summary"><div><dt>Cảnh báo</dt><dd>${state.result?.finding_count ?? 0}</dd></div><div><dt>Tệp gốc</dt><dd>Không thay đổi</dd></div></dl><div class="result-actions"><div class="button-row"><button class="button button--primary" id="open" ${outputPending ? "disabled" : ""} ${state.outputActionPending === "open" ? 'aria-busy="true"' : ""}>${state.outputActionPending === "open" ? "Đang mở tệp…" : "Mở tệp kết quả"}</button><button class="button button--secondary" id="reveal" ${outputPending ? "disabled" : ""} ${state.outputActionPending === "reveal" ? 'aria-busy="true"' : ""}>${state.outputActionPending === "reveal" ? "Đang mở thư mục…" : "Mở thư mục"}</button></div><button class="back-button" id="restart">Xử lý tệp khác</button></div>${errorHtml()}</section>`;
}

function settingsHtml(): string {
  if (state.view.kind !== "settings") return "";
  const section = state.view.section;
  const sections: [SettingsSection, string][] = [["prompts", "Prompt"], ["review-rules", "Quy tắc"], ["models", "AI & Mô hình"]];
  const operationLocked = settingsOperationLocked();
  const content = settingsContent(section);
  return `<main class="settings-page" id="settings-page" aria-labelledby="settings-title" aria-describedby="settings-description" ${state.settingsLoading ? 'aria-busy="true"' : ""}>
    <header class="settings-page__header">
       <button class="back-button" id="settings-back" type="button" ${operationLocked ? 'aria-disabled="true" aria-describedby="settings-message"' : ""}>← Quay lại rà soát</button>
      <div><h1 id="settings-title">Cài đặt</h1><p id="settings-description">Quản lý prompt, xem bộ quy tắc cố định và thiết lập nguồn AI.</p></div>
    </header>
    <div class="settings-layout">
       <nav class="settings-nav" aria-label="Mục cài đặt">${sections.map(([id, label]) => `<button class="settings-nav__item" type="button" id="settings-nav-${id}" data-settings-section="${id}" ${section === id ? 'aria-current="page"' : ""} ${state.settingsLoading ? "disabled" : operationLocked ? 'aria-disabled="true" aria-describedby="settings-message"' : ""}>${label}</button>`).join("")}</nav>
      <div class="settings-workspace">
         ${state.settingsLoading ? `<p class="settings-loading" role="status">Đang tải cài đặt trên máy…</p>` : ""}
         ${settingsMessageHtml()}
         ${promptDiscardConfirmationHtml()}
         ${content.body}
        ${content.footer}
      </div>
    </div>
    ${state.lastDeletedRule ? `<div class="undo-toast" role="status"><span>Đã xoá một prompt.</span><button class="button button--secondary button--small" id="undo-delete-rule" ${state.customRulePending ? "disabled" : ""}>Hoàn tác</button></div>` : ""}
  </main>`;
}
function settingsMessageHtml(): string {
  const message = state.settingsMessage ?? settingsBusyMessage();
  return message ? `<p class="settings-message settings-message--${message.tone}" id="settings-message" tabindex="-1" role="${message.tone === "error" ? "alert" : "status"}">${escape(message.text)}</p>` : "";
}
function promptDiscardConfirmationHtml(): string {
  if (!state.pendingPromptAction) return "";
  return `<div class="destructive-confirm" id="prompt-discard-confirmation" role="alert" aria-labelledby="prompt-discard-title"><p id="prompt-discard-title"><strong>Prompt có thay đổi chưa lưu.</strong> Tiếp tục chỉnh sửa hoặc bỏ thay đổi để thực hiện thao tác vừa chọn.</p><div class="button-row"><button class="button button--secondary button--small" id="cancel-prompt-discard" type="button">Tiếp tục chỉnh sửa</button><button class="button button--danger button--small" id="confirm-prompt-discard" type="button">Bỏ thay đổi</button></div></div>`;
}
function settingsContent(section: SettingsSection): { body: string; footer: string } {
  if (section === "prompts") {
    const editing = state.customRules.find(rule => rule.id === state.editingCustomRuleId);
    const existingLength = editing ? [...editing.prompt].length : 0;
    const available = Math.max(0, customRulePromptLimit - customRuleCharacterCount() + existingLength);
    const draftLength = [...state.customRuleDraft].length;
    const controlsLocked = state.customRulePending || state.settingsLoading;
    const saveDisabled = controlsLocked || !state.customRuleTitleDraft.trim() || !state.customRuleDraft.trim();
    const promptList = state.customRules.length
      ? state.customRules.map((rule, index) => {
          const id = escape(rule.id);
          const selected = rule.id === state.editingCustomRuleId;
          const badge = rule.is_default ? `<em class="prompt-row__badge">Chọn sẵn</em>` : "";
          return `<div class="prompt-list__item"><button class="prompt-row" type="button" data-prompt-id="${id}" data-edit-rule="${id}" aria-label="Sửa prompt ${index + 1}: ${escape(rule.title)}" ${selected ? 'aria-current="true"' : ""} ${controlsLocked ? "disabled" : ""}><strong class="prompt-row__title"><span>${escape(rule.title)}</span>${badge}</strong><span class="prompt-row__snippet">${escape(rule.prompt)}</span></button><button class="delete-button" type="button" data-delete-rule="${id}" aria-label="Xoá prompt ${index + 1}: ${escape(rule.title)}" ${controlsLocked ? "disabled" : ""}>Xoá</button></div>`;
        }).join("")
      : `<div class="empty-state"><strong>Chưa có prompt riêng.</strong><span>Tạo một prompt để cung cấp thuật ngữ, ngữ cảnh hoặc tiêu chí kiểm tra riêng cho AI.</span></div>`;
    return {
      body: `<section class="settings-section settings-prompts" id="settings-prompts" aria-labelledby="settings-prompts-title"><header class="settings-section__header"><div class="section-copy"><h2 id="settings-prompts-title">Prompt</h2><p>Mỗi mục là một đoạn hướng dẫn bổ sung cho AI; ứng dụng tự quản lý định dạng kết quả và vị trí bôi vàng.</p></div><button class="button button--secondary" id="new-custom-rule" type="button" ${controlsLocked ? "disabled" : ""}>Prompt mới</button></header><div class="prompt-manager"><div class="prompt-manager__master"><nav class="prompt-list" aria-label="Danh sách prompt" aria-live="polite">${promptList}</nav></div><section class="prompt-manager__detail" aria-labelledby="custom-rule-editor-title"><div class="section-copy"><h3 id="custom-rule-editor-title">${editing ? "Sửa prompt" : "Tạo prompt"}</h3><p>${editing ? "Chỉnh nội dung rồi lưu thay đổi, hoặc huỷ để trở về chế độ tạo mới." : "Mô tả thuật ngữ, ngữ cảnh hoặc tiêu chí mà AI cần chú ý."}</p></div><form class="custom-rule-form" id="custom-rule-form"><div class="control"><label for="custom-rule-title">Tiêu đề</label><input type="text" id="custom-rule-title" name="title" required maxlength="${customRuleTitleLimit}" aria-describedby="custom-rule-title-help${state.customRuleTitleInvalid ? " settings-message" : ""}" ${state.customRuleTitleInvalid ? 'aria-invalid="true"' : ""} placeholder="Ví dụ: Thuật ngữ khách hàng" value="${escape(state.customRuleTitleDraft)}" ${controlsLocked ? "disabled" : ""}><span class="field__helper" id="custom-rule-title-help">Bắt buộc, tối đa ${customRuleTitleLimit} ký tự. Tiêu đề hiển thị ở bước Chuẩn bị rà soát và không được gửi cho AI.</span></div><div class="control"><label for="custom-rule-prompt">Nội dung prompt</label><textarea id="custom-rule-prompt" name="prompt" rows="7" required maxlength="${available}" aria-describedby="custom-rule-help custom-rule-count${state.customRulePromptInvalid ? " settings-message" : ""}" ${state.customRulePromptInvalid ? 'aria-invalid="true"' : ""} placeholder="Ví dụ: Dùng thuật ngữ “khách hàng”, không dùng “client”." ${controlsLocked ? "disabled" : ""}>${escape(state.customRuleDraft)}</textarea><div class="field__meta"><span class="field__helper" id="custom-rule-help">Không yêu cầu AI trả cả câu/đoạn hoặc tự đặt cấu trúc output. Tổng tối đa 4.000 ký tự.</span><small id="custom-rule-count">${draftLength.toLocaleString("vi-VN")}/${available.toLocaleString("vi-VN")} ký tự còn dùng được cho mục này</small></div></div><label class="setting-row"><span><strong>Chọn sẵn ở bước Chuẩn bị rà soát</strong><small>Prompt này sẽ được tick mặc định khi bắt đầu một lần rà soát mới.</small></span><input type="checkbox" id="custom-rule-default" ${state.customRuleDefaultDraft ? "checked" : ""} ${controlsLocked ? "disabled" : ""}></label></form></section></div></section>`,
      footer: `<footer class="settings-footer"><div class="button-row settings-footer__actions"><button class="button button--primary" type="submit" form="custom-rule-form" ${saveDisabled ? "disabled" : ""}>${state.customRulePending ? "Đang lưu…" : editing ? "Lưu thay đổi" : "Thêm prompt"}</button>${editing ? `<button class="button button--secondary" id="cancel-rule-edit" type="button" ${controlsLocked ? "disabled" : ""}>Huỷ sửa</button>` : ""}</div></footer>`,
    };
  }
  if (section === "review-rules") {
    return {
      body: `<section class="settings-section settings-review-rules" id="settings-review-rules" aria-labelledby="settings-review-rules-title"><header class="settings-section__header"><div class="section-copy"><h2 id="settings-review-rules-title">Quy tắc rà soát</h2><p>Danh sách minh bạch của bộ kiểm tra cố định. Từng nhóm kiểm tra không thể bật hoặc tắt riêng tại đây.</p></div></header><ul class="review-rule-inventory">${fixedReviewRules.map(rule => `<li class="review-rule-row" data-review-rule="${rule.id}"><div><strong>${escape(rule.name)}</strong><p>${escape(rule.description)}</p></div><span class="review-rule-status">${defaultRuleOptions[rule.id] ? "Áp dụng trong kiểm tra cơ bản" : "Không chạy trong cấu hình chuẩn"}</span></li>`).join("")}</ul><p class="notice">Khi dùng AI rà soát toàn văn, bạn có thể chọn chạy bổ sung toàn bộ bộ quy tắc code ở bước Chuẩn bị rà soát. Mục này chỉ cung cấp thông tin và không thay đổi lượt xử lý.</p></section>`,
      footer: "",
    };
  }

  const installed = state.model.state === "ready" || state.model.state === "installed";
  const busy = modelOperationBusy();
  const controlsLocked = busy || modelOperationBaseline !== null || state.modelRemovalPending || state.settingsLoading || state.cloudTestLoading || state.cloudSaving;
  const activeModelId = state.model.model_id;
  const title = state.modelRemovalRunning ? "Đang gỡ model…" : state.model.state === "ready" ? (state.model.release_approved ? "AI cục bộ đã sẵn sàng" : "AI cục bộ đang ở chế độ đánh giá") : state.model.state === "installed" ? (state.model.code ? "Model đã cài nhưng chưa thể khởi động" : "Model đã cài; AI đang tắt") : state.model.state === "importing" ? "Đang nhập gói model…" : state.model.state === "verifying" ? "Đang xác minh và khởi động model…" : state.model.state === "cancelled" ? "Đã dừng thao tác model" : state.model.state === "invalid" || state.model.state === "incompatible" ? "Gói model không hợp lệ hoặc không tương thích" : state.model.state === "error" ? "Không thể cài model" : "Chưa cài AI cục bộ";

  const currentTab = state.selectedProviderTab;

  const providerTabsHtml = `
    <div class="provider-tabs" role="tablist" aria-label="Nguồn AI">
      <button class="provider-tab ${currentTab === "local" ? "selected" : ""}" id="provider-tab-local" data-provider-tab="local" type="button" role="tab" aria-selected="${currentTab === "local"}" ${controlsLocked ? "disabled" : ""}>
        <span class="provider-tab__badge">${state.aiConfig.active_provider === "local" ? "Đang dùng" : "Offline"}</span>
        <strong>Mô hình cục bộ</strong>
        <small>Offline GGUF / .svmodel</small>
      </button>
      <button class="provider-tab ${currentTab === "openai" ? "selected" : ""}" id="provider-tab-openai" data-provider-tab="openai" type="button" role="tab" aria-selected="${currentTab === "openai"}" ${controlsLocked ? "disabled" : ""}>
        <span class="provider-tab__badge">${state.aiConfig.active_provider === "openai" ? "Đang dùng" : "Cloud"}</span>
        <strong>OpenAI / Tương thích</strong>
        <small>OpenAI, DeepSeek, Groq...</small>
      </button>
      <button class="provider-tab ${currentTab === "gemini" ? "selected" : ""}" id="provider-tab-gemini" data-provider-tab="gemini" type="button" role="tab" aria-selected="${currentTab === "gemini"}" ${controlsLocked ? "disabled" : ""}>
        <span class="provider-tab__badge">${state.aiConfig.active_provider === "gemini" ? "Đang dùng" : "Cloud"}</span>
        <strong>Google Gemini API</strong>
        <small>Gemini 2.5 Flash</small>
      </button>
    </div>`;

  if (currentTab === "local") {
    const isLocalActive = state.aiConfig.active_provider === "local";
    return {
      body: `
      <section class="settings-section settings-models" id="settings-models" aria-labelledby="settings-models-title">
      <header class="settings-section__header"><div class="section-copy"><h2 id="settings-models-title">Mô hình AI</h2><p>Chọn nguồn AI: mô hình chạy offline trên máy hoặc kết nối qua API đám mây.</p></div></header>
      ${providerTabsHtml}
      <div class="model-card">
        <div class="model-card__header">
          <div>
            <strong id="model-status-title">${title}</strong>
            <p>${installed ? `${escape(activeModelId ?? "model")} · ${escape(state.model.version ?? "1.0.0")}` : "Ứng dụng vẫn kiểm tra đầy đủ bằng bộ quy tắc cục bộ."}</p>
          </div>
          ${installed && !state.modelRemovalPending ? `<button class="delete-button" id="model-remove" type="button" ${controlsLocked ? "disabled" : ""}>Gỡ model</button>` : ""}
        </div>
        ${state.modelRemovalPending ? `<div class="destructive-confirm" role="alert"><p>Gỡ model sẽ giải phóng dung lượng, nhưng bạn phải nhập lại gói nếu muốn dùng AI sau này.</p><div class="button-row"><button class="button button--secondary button--small" id="cancel-model-remove" type="button" ${state.modelRemovalRunning ? "disabled" : ""}>Giữ lại</button><button class="button button--danger button--small" id="confirm-model-remove" type="button" ${state.modelRemovalRunning ? 'disabled aria-busy="true"' : ""}>${state.modelRemovalRunning ? "Đang gỡ…" : "Gỡ model"}</button></div></div>` : ""}
      </div>
      ${installed && state.model.trust === "local_unverified" ? `<p class="settings-message settings-message--error" role="status">Model GGUF nhập cục bộ chưa có chữ ký và benchmark phát hành. Có thể rà soát sâu để đánh giá trên máy này, nhưng kết quả chưa được phê duyệt cho phát hành.</p>` : ""}
      ${installed && isLocalActive ? `<p class="settings-message settings-message--status" role="status">✓ Đang sử dụng mô hình AI cục bộ này để rà soát.</p>` : ""}
      ${installed && !isLocalActive ? `<div class="activate-provider-row"><button class="button button--primary" id="activate-local-provider" type="button" ${controlsLocked ? "disabled" : ""}>Kích hoạt mô hình cục bộ</button></div>` : ""}
      <div class="section-copy model-section-copy">
        <h3>Nhập gói model</h3>
        <p>Chấp nhận gói <code>.svmodel</code> đã ký hoặc tệp <code>.gguf</code> nhập cục bộ. Gói ký số mới được coi là đã phê duyệt phát hành.</p>
      </div>
      <p class="notice">Ứng dụng không kết nối mạng để lấy model. Nội dung tài liệu không được gửi đi.</p>
      </section>`,
      footer: `<footer class="settings-footer"><div class="button-row settings-footer__actions"><button class="button button--primary" id="model-import" type="button" ${controlsLocked ? "disabled" : ""}>Nhập gói có sẵn</button><button class="button button--secondary" id="model-action" type="button" ${busy && !state.modelRemovalRunning ? "" : "disabled"} ${busy ? 'aria-busy="true"' : ""}>Huỷ thao tác</button></div></footer>`,
    };
  }

  // Cloud provider tab (openai or gemini)
  const provider = currentTab;
  const isCloudCurrentActive = state.aiConfig.active_provider === provider;
  const providerName = provider === "openai" ? "OpenAI / Tương thích" : "Google Gemini API";
  const savedCfg = state.aiConfig.configs.find(c => c.provider === provider);
  const draft = state.cloudDrafts[provider] ?? { apiKey: "", baseUrl: defaultAiConfigs[provider]?.base_url ?? "", modelName: defaultAiConfigs[provider]?.model_name ?? "" };

  const cloudTestResultHtml = () => {
    if (state.cloudTestLoading) {
      return `<p class="settings-message settings-message--status" id="cloud-test-status" role="status">Đang kiểm tra kết nối tới ${escape(providerName)}…</p>`;
    }
    if (state.cloudTestResult) {
      if (state.cloudTestResult.ok) {
        return `<p class="settings-message settings-message--status" id="cloud-test-status" role="status">✓ Kết nối thành công! Model: ${escape(state.cloudTestResult.model ?? draft.modelName)}</p>`;
      }
      return `<p class="settings-message settings-message--error" id="cloud-test-status" role="alert">✕ ${escape(state.cloudTestResult.message || state.cloudTestResult.error || "Không thể kết nối")}</p>`;
    }
    return "";
  };

  return {
    body: `
    <section class="settings-section settings-models" id="settings-models" aria-labelledby="settings-models-title">
    <header class="settings-section__header"><div class="section-copy"><h2 id="settings-models-title">Mô hình AI</h2><p>Chọn nguồn AI: mô hình chạy offline trên máy hoặc kết nối qua API đám mây.</p></div></header>
    ${providerTabsHtml}
    <div class="privacy-banner" role="note">
      <div class="privacy-banner__icon" aria-hidden="true">🔒</div>
      <div>
        <strong>Lưu ý quyền riêng tư (${escape(providerName)})</strong>
        <p>Khi kích hoạt chế độ Cloud AI, nội dung tài liệu sẽ được gửi qua mạng Internet đến API của ${escape(providerName)} để phân tích. Hãy đảm bảo tài liệu không chứa dữ liệu mật nội bộ hoặc tuân thủ chính sách dữ liệu của tổ chức.</p>
      </div>
    </div>
    ${isCloudCurrentActive ? `<p class="active-provider-badge">● Nguồn AI này đang được kích hoạt cho rà soát</p>` : ""}
    <div class="cloud-form">
      <div class="control">
        <label for="cloud-base-url">Base URL</label>
        <input type="url" id="cloud-base-url" value="${escape(draft.baseUrl)}" placeholder="${provider === "openai" ? "https://api.openai.com/v1" : "https://generativelanguage.googleapis.com/v1beta"}" ${controlsLocked ? "disabled" : ""}>
        <span class="field__helper">Địa chỉ endpoint của API${provider === "openai" ? " (OpenAI, DeepSeek, Groq, OpenRouter...)" : " Google Gemini"}</span>
      </div>
      <div class="control">
        <label for="cloud-api-key">API Key</label>
        <input type="password" id="cloud-api-key" value="${escape(draft.apiKey)}" placeholder="${savedCfg?.masked_key ? `Đã lưu key: ${savedCfg.masked_key}` : provider === "openai" ? "sk-..." : "AIza..."}" ${controlsLocked ? "disabled" : ""}>
        <span class="field__helper">${savedCfg?.masked_key ? `Key đã lưu: <code>${escape(savedCfg.masked_key)}</code>. Để trống nếu giữ nguyên.` : "Bắt buộc nhập API Key để kết nối."}</span>
      </div>
      <div class="control">
        <label for="cloud-model-name">Model Name</label>
        <input type="text" id="cloud-model-name" value="${escape(draft.modelName)}" placeholder="${provider === "openai" ? "gpt-4o-mini" : "gemini-2.5-flash"}" ${controlsLocked ? "disabled" : ""}>
        <span class="field__helper">Tên mô hình (Ví dụ: ${provider === "openai" ? "gpt-4o-mini, deepseek-chat, llama-3.3-70b-versatile" : "gemini-2.5-flash, gemini-1.5-pro"})</span>
      </div>
    </div>
    ${cloudTestResultHtml()}
    </section>`,
    footer: `<footer class="settings-footer"><div class="button-row settings-footer__actions"><button class="button button--secondary" id="cloud-test-connection" type="button" ${state.cloudTestLoading || controlsLocked ? 'disabled aria-busy="true"' : ""}>${state.cloudTestLoading ? "Đang kiểm tra…" : "Kiểm tra kết nối"}</button><button class="button button--primary" id="cloud-save-active" type="button" ${state.cloudSaving || controlsLocked ? 'disabled aria-busy="true"' : ""}>${state.cloudSaving ? "Đang lưu…" : isCloudCurrentActive ? "Lưu cấu hình" : "Lưu & Kích hoạt"}</button></div></footer>`,
  };
}

function bind(): void {
  document.querySelector("#review-nav")?.addEventListener("click", () => { if (state.view.kind === "settings") closeSettings("#review-nav"); });
  document.querySelector("#settings")?.addEventListener("click", () => { if (isWorkflowView()) void openSettings("prompts", "#settings"); });
  document.querySelector("#settings-back")?.addEventListener("click", () => closeSettings());
  document.querySelectorAll<HTMLButtonElement>("[data-settings-section]").forEach(button => button.addEventListener("click", () => activateSettingsSection(button.dataset.settingsSection as SettingsSection)));
  document.querySelector("#choose")?.addEventListener("click", choose);
  document.querySelector("#back")?.addEventListener("click", reset);
  document.querySelector("#restart")?.addEventListener("click", reset);
  document.querySelector("#start")?.addEventListener("click", start);
  document.querySelector("#cancel")?.addEventListener("click", () => void cancel());
  document.querySelector("#open")?.addEventListener("click", () => void openResult(false));
  document.querySelector("#reveal")?.addEventListener("click", () => void openResult(true));
  document.querySelector("#open-model-settings")?.addEventListener("click", () => void openSettings("models", "#open-model-settings"));
  document.querySelector("#manage-custom-rules")?.addEventListener("click", () => void openSettings("prompts", "#manage-custom-rules"));
  document.querySelector("#manage-review-rules")?.addEventListener("click", () => void openSettings("review-rules", "#manage-review-rules"));
  document.querySelector<HTMLInputElement>("#use-model")?.addEventListener("change", event => { void setModelEnabled((event.target as HTMLInputElement).checked); });
  document.querySelectorAll<HTMLInputElement>("[data-select-rule]").forEach(input => input.addEventListener("change", () => {
    const id = input.dataset.selectRule!;
    state.selectedRuleIds = input.checked
      ? [...new Set([...state.selectedRuleIds, id])]
      : state.selectedRuleIds.filter(item => item !== id);
    render(`[data-select-rule="${id}"]`);
  }));
  document.querySelector<HTMLInputElement>("#custom-rule-title")?.addEventListener("input", event => {
    state.customRuleTitleDraft = (event.target as HTMLInputElement).value;
    if (state.customRuleTitleInvalid) {
      state.customRuleTitleInvalid = false;
      state.settingsMessage = null;
      (event.target as HTMLInputElement).removeAttribute("aria-invalid");
      (event.target as HTMLInputElement).setAttribute("aria-describedby", "custom-rule-title-help");
      document.querySelector("#settings-message")?.remove();
    }
    updateCustomRuleSaveControl();
  });
  document.querySelector<HTMLInputElement>("#custom-rule-default")?.addEventListener("change", event => {
    state.customRuleDefaultDraft = (event.target as HTMLInputElement).checked;
  });
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
    const available = Math.max(0, customRulePromptLimit - customRuleCharacterCount() + (editing ? [...editing.prompt].length : 0));
    const counter = document.querySelector<HTMLElement>("#custom-rule-count");
    if (counter) counter.textContent = `${[...state.customRuleDraft].length.toLocaleString("vi-VN")}/${available.toLocaleString("vi-VN")} ký tự còn dùng được cho mục này`;
    updateCustomRuleSaveControl();
  });
  document.querySelector<HTMLFormElement>("#custom-rule-form")?.addEventListener("submit", event => void saveCustomRule(event));
  document.querySelector("#new-custom-rule")?.addEventListener("click", () => newCustomRule());
  document.querySelector("#cancel-rule-edit")?.addEventListener("click", cancelCustomRuleEdit);
  document.querySelector("#cancel-prompt-discard")?.addEventListener("click", cancelPromptDiscard);
  document.querySelector("#confirm-prompt-discard")?.addEventListener("click", confirmPromptDiscard);
  document.querySelectorAll<HTMLButtonElement>("[data-edit-rule]").forEach(button => button.addEventListener("click", () => editCustomRule(button.dataset.editRule!)));
  document.querySelectorAll<HTMLButtonElement>("[data-delete-rule]").forEach(button => button.addEventListener("click", () => void deleteCustomRule(button.dataset.deleteRule!)));
  document.querySelector("#undo-delete-rule")?.addEventListener("click", () => void undoDeleteCustomRule());

  // Model & AI Provider events
  document.querySelectorAll<HTMLButtonElement>("[data-provider-tab]").forEach(button => {
    button.addEventListener("click", () => {
      const tab = button.dataset.providerTab as ProviderTab;
      if (tab) {
        state.selectedProviderTab = tab;
        state.cloudTestResult = null;
        render(`[data-provider-tab="${tab}"]`);
      }
    });
  });
  document.querySelector("#activate-local-provider")?.addEventListener("click", () => void activateLocalProvider());
  document.querySelector<HTMLInputElement>("#cloud-base-url")?.addEventListener("input", event => {
    const tab = state.selectedProviderTab;
    if (tab !== "local") {
      state.cloudDrafts[tab].baseUrl = (event.target as HTMLInputElement).value;
    }
  });
  document.querySelector<HTMLInputElement>("#cloud-api-key")?.addEventListener("input", event => {
    const tab = state.selectedProviderTab;
    if (tab !== "local") {
      state.cloudDrafts[tab].apiKey = (event.target as HTMLInputElement).value;
    }
  });
  document.querySelector<HTMLInputElement>("#cloud-model-name")?.addEventListener("input", event => {
    const tab = state.selectedProviderTab;
    if (tab !== "local") {
      state.cloudDrafts[tab].modelName = (event.target as HTMLInputElement).value;
    }
  });
  document.querySelector("#cloud-test-connection")?.addEventListener("click", () => void testCloudConnection());
  document.querySelector("#cloud-save-active")?.addEventListener("click", () => void saveAndActivateCloud());

  document.querySelector("#model-import")?.addEventListener("click", () => void modelImport());
  document.querySelector("#model-action")?.addEventListener("click", () => void modelAction());
  document.querySelector("#model-remove")?.addEventListener("click", () => { state.modelRemovalPending = true; state.settingsMessage = null; render("#cancel-model-remove"); });
  document.querySelector("#cancel-model-remove")?.addEventListener("click", () => { state.modelRemovalPending = false; state.settingsMessage = null; render("#model-remove"); });
  document.querySelector("#confirm-model-remove")?.addEventListener("click", () => void removeModel());
}

function updateCustomRuleSaveControl(): void {
  const submit = document.querySelector<HTMLButtonElement>('button[type="submit"][form="custom-rule-form"]');
  if (submit) submit.disabled = !state.customRuleTitleDraft.trim() || !state.customRuleDraft.trim();
}

function requestPromptAction(action: PendingPromptAction): boolean {
  if (state.view.kind !== "settings" || state.view.section !== "prompts" || !customRuleDraftDirty()) return false;
  state.pendingPromptAction = action;
  state.settingsMessage = null;
  render("#cancel-prompt-discard");
  return true;
}
function cancelPromptDiscard(): void {
  if (!state.pendingPromptAction) return;
  state.pendingPromptAction = null;
  render("#custom-rule-prompt");
}
function confirmPromptDiscard(): void {
  const action = state.pendingPromptAction;
  if (!action) return;
  state.pendingPromptAction = null;
  state.customRuleTitleDraft = "";
  state.customRuleDraft = "";
  state.customRuleDefaultDraft = false;
  state.editingCustomRuleId = null;
  state.customRuleTitleInvalid = false;
  state.customRulePromptInvalid = false;
  state.settingsMessage = null;
  if (action.kind === "close") closeSettings(action.preferredReturnFocus, true);
  else if (action.kind === "section") activateSettingsSection(action.section, true);
  else if (action.kind === "new") newCustomRule(true);
  else editCustomRule(action.id, true);
}
function activateSettingsSection(section: SettingsSection, discardConfirmed = false): void {
  if (state.view.kind !== "settings") return;
  if (settingsSectionNavigationLocked()) {
    state.settingsMessage = { tone: "status", text: "Hãy chờ thao tác hiện tại hoàn tất hoặc huỷ thao tác model trước khi chuyển mục cài đặt." };
    render("#settings-message");
    return;
  }
  if (state.view.section === section) return;
  if (!discardConfirmed && requestPromptAction({ kind: "section", section })) return;
  if (state.view.section === "prompts") {
    state.customRuleTitleInvalid = false;
    state.customRulePromptInvalid = false;
  }
  state.view = { ...state.view, section };
  state.pendingPromptAction = null;
  state.settingsMessage = null;
  render(settingsSectionHeading(section));
}

function useDocument(document: DocumentInfo): void {
  documentSelectionSequence += 1;
  outputActionSequence += 1;
  settingsRequestSequence += 1;
  Object.assign(state, { document, step: "rules", result: null, jobId: "", jobStarting: false, cancelPending: false, outputActionPending: null, progress: 0, progressStage: "", error: "", settingsLoading: false });
  render();
}
async function choose(): Promise<void> {
  if (!isWorkflowView() || state.step === "processing") return;
  const selection = ++documentSelectionSequence;
  const requestedFromStep = state.step;
  const requestedView = state.view;
  try {
    const selected = await api.chooseDocument();
    if (selection !== documentSelectionSequence || state.view !== requestedView || state.step !== requestedFromStep) return;
    if (selected) useDocument(selected);
  } catch {
    if (selection !== documentSelectionSequence || state.view !== requestedView || state.step !== requestedFromStep) return;
    state.error = "Không mở được tệp DOCX. Hãy kiểm tra định dạng tệp rồi thử lại; tệp gốc chưa bị thay đổi.";
    render("#choose");
  }
}
function reset(): void {
  documentSelectionSequence += 1;
  outputActionSequence += 1;
  settingsRequestSequence += 1;
  Object.assign(state, { step: "file", document: null, result: null, progress: 0, progressStage: "", jobStarting: false, cancelPending: false, outputActionPending: null, error: "", jobId: "", settingsLoading: false });
  render();
}
async function start(): Promise<void> {
  if (!state.document) return;
  const cloudActive = isCloudActive();
  const effectiveUseModel = cloudActive || (state.useModel && modelFilterAvailable());
  const effectiveFullReview = cloudActive || (state.fullReview && fullReviewAvailable());
  console.log("[SoatVan-UI] Starting review:", {
    cloudActive,
    activeProvider: state.aiConfig.active_provider,
    effectiveUseModel,
    effectiveFullReview,
    selectedRuleIds: state.selectedRuleIds,
    customRulesCount: state.customRules.length,
    compiledPromptLength: compiledCustomPrompt().length,
  });
  if (effectiveFullReview && !fullReviewAvailable()) {
    clearUnavailableFullReview();
    state.error = cloudActive
      ? "Rà soát sâu cần AI Cloud được cấu hình hợp lệ. Hãy kiểm tra cài đặt AI rồi thử lại."
      : "Rà soát sâu cần AI cục bộ đang bật và sẵn sàng. Hãy bật AI rồi thử lại.";
    render("#start");
    return;
  }
  documentSelectionSequence += 1;
  settingsRequestSequence += 1;
  state.settingsLoading = false;
  const currentJobId = crypto.randomUUID();
  const customPrompt = effectiveUseModel ? compiledCustomPrompt() : "";
  console.log("[SoatVan-UI] Final customPrompt to send:", customPrompt);
  state.step = "processing";
  state.progress = 0;
  state.progressStage = "";
  state.jobId = currentJobId;
  state.jobStarting = true;
  state.cancelPending = false;
  state.error = "";
  render();
  let unlisten: (() => void) | undefined;
  try {
    unlisten = await api.onProgress(event => {
      console.log("[SoatVan-UI] onProgress event:", event);
      if (state.jobId === currentJobId && state.step === "processing" && event.job_id === currentJobId) applyProgress(event.stage, event.percent);
    }, currentJobId);
  } catch {
    if (state.jobId === currentJobId && state.step === "processing") {
      state.jobId = "";
      state.jobStarting = false;
      state.step = "rules";
      state.error = "Không thể theo dõi tiến độ xử lý. Hãy thử bắt đầu lại; tác vụ chưa được chạy.";
      render("#start");
    }
    return;
  }
  if (state.jobId !== currentJobId || state.step !== "processing") {
    try { unlisten(); } catch { /* Listener cleanup must not break the workflow. */ }
    return;
  }
  state.jobStarting = false;
  updateCancelControl();
  try {
    console.log("[SoatVan-UI] Dispatching api.startJob with jobId:", currentJobId);
    const result = await api.startJob(currentJobId, state.document.path, defaultPreset, customPrompt, effectiveUseModel, defaultRuleOptions, [], effectiveFullReview, effectiveFullReview && state.includeRuleFindings);
    console.log("[SoatVan-UI] api.startJob result:", result);
    if (state.jobId === currentJobId && state.step === "processing") {
      state.jobId = "";
      state.cancelPending = false;
      state.result = result;
      state.error = "";
      state.step = result.status === "no_findings" && result.review?.status !== "partial" ? "no-findings" : "result";
      render();
    }
  } catch (error) {
    console.error("[SoatVan-UI] startJob error:", error);
    if (state.jobId === currentJobId && state.step === "processing") {
      const cancellationWasPending = state.cancelPending;
      state.jobId = "";
      state.cancelPending = false;
      state.step = "rules";
      state.error = cancellationWasPending ? "" : processingErrorMessage(error);
      render(cancellationWasPending ? "#start" : undefined);
    }
  } finally {
    try { unlisten(); } catch { /* Listener cleanup must not break the workflow. */ }
  }
}
async function cancel(): Promise<void> {
  const cancelledJobId = state.jobId;
  if (!cancelledJobId || state.step !== "processing" || state.jobStarting || state.cancelPending) return;
  state.cancelPending = true;
  state.error = "";
  document.querySelector(".workflow-card .error")?.remove();
  updateCancelControl();
  try {
    const cancelled = await api.cancelJob(cancelledJobId);
    if (state.jobId !== cancelledJobId || state.step !== "processing") return;
    state.cancelPending = false;
    if (cancelled) {
      state.jobId = "";
      state.step = "rules";
      state.progress = 0;
      state.progressStage = "";
      render("#start");
    } else {
      state.error = "Tác vụ không còn ở trạng thái có thể dừng. Ứng dụng sẽ tiếp tục chờ kết quả.";
      render("#cancel");
    }
  } catch {
    if (state.jobId !== cancelledJobId || state.step !== "processing") return;
    state.cancelPending = false;
    state.error = "Không gửi được yêu cầu dừng. Tác vụ vẫn đang chạy; hãy thử lại hoặc chờ tác vụ hoàn tất.";
    render("#cancel");
  }
}
async function openResult(reveal: boolean): Promise<void> {
  const path = state.result?.output_path;
  if (!path || state.outputActionPending) return;
  const action: OutputAction = reveal ? "reveal" : "open";
  const sequence = ++outputActionSequence;
  const result = state.result;
  state.outputActionPending = action;
  state.error = "";
  document.querySelector(".workflow-card .error")?.remove();
  document.querySelectorAll<HTMLButtonElement>("#open, #reveal").forEach(button => {
    button.disabled = true;
    const active = button.id === action;
    if (active) button.setAttribute("aria-busy", "true");
    if (active) button.textContent = reveal ? "Đang mở thư mục…" : "Đang mở tệp…";
  });
  let error = "";
  try { await api.openOutput(path, reveal); }
  catch { error = reveal ? "Không mở được thư mục kết quả. Hãy kiểm tra đường dẫn hiển thị ở trên." : "Không mở được tệp kết quả. Hãy mở thủ công từ đường dẫn hiển thị ở trên."; }
  if (sequence !== outputActionSequence || state.result !== result || state.step !== "result") return;
  state.outputActionPending = null;
  state.error = error;
  render(reveal ? "#reveal" : "#open");
}
function closeSettings(preferredReturnFocus?: string, discardConfirmed = false): void {
  if (state.view.kind !== "settings") return;
  if (settingsOperationLocked()) {
    state.settingsMessage = { tone: "status", text: "Hãy chờ thao tác hiện tại hoàn tất hoặc huỷ thao tác model trước khi quay lại." };
    render("#settings-message");
    return;
  }
  if (!discardConfirmed && requestPromptAction({ kind: "close", preferredReturnFocus })) return;
  const returnFocus = preferredReturnFocus ?? state.view.returnFocus;
  settingsRequestSequence += 1;
  state.view = { kind: "workflow" };
  state.settingsLoading = false;
  state.modelRemovalPending = false;
  state.settingsMessage = null;
  state.lastDeletedRule = null;
  state.editingCustomRuleId = null;
  state.customRuleTitleDraft = "";
  state.customRuleDraft = "";
  state.customRuleDefaultDraft = false;
  state.customRuleTitleInvalid = false;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  render(returnFocus);
}
async function openSettings(section: SettingsSection = "prompts", returnFocus = "#settings"): Promise<void> {
  if (state.step === "processing") return;
  if (state.view.kind === "settings") {
    activateSettingsSection(section);
    return;
  }
  const request = ++settingsRequestSequence;
  const modelSequence = modelOperationSequence;
  const customRuleSequence = customRuleOperationSequence;
  const modelStatusRequest = ++modelStatusRequestSequence;
  const customRuleRequest = ++customRuleRequestSequence;
  const requestedStep = state.step;
  documentSelectionSequence += 1;
  outputActionSequence += 1;
  state.view = { kind: "settings", section, returnFocus };
  state.settingsLoading = true;
  state.settingsMessage = null;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  render(returnFocus === "#settings" ? "#settings-title" : settingsSectionHeading(section));
  const [modelResult, customRulesResult, aiConfigResult] = await Promise.allSettled([
    api.modelStatus(state.useModel),
    api.customRuleList(),
    api.aiConfigGet(),
  ]);
  if (request !== settingsRequestSequence || state.view.kind !== "settings" || state.step !== requestedStep) {
    if (request === settingsRequestSequence) {
      state.settingsLoading = false;
      render();
    }
    return;
  }
  if (modelResult.status === "fulfilled" && modelSequence === modelOperationSequence && modelStatusRequest === modelStatusRequestSequence) {
    state.model = modelResult.value;
  } else if (modelResult.status === "rejected") {
    state.settingsMessage = { tone: "error", text: "Không đọc được trạng thái AI cục bộ. Quy tắc riêng vẫn có thể được chỉnh sửa." };
  }
  if (customRulesResult.status === "fulfilled" && customRuleSequence === customRuleOperationSequence && customRuleRequest === customRuleRequestSequence) {
    state.customRules = customRulesResult.value;
    syncDefaultRuleSelection();
  } else if (customRulesResult.status === "rejected") {
    state.settingsMessage = { tone: "error", text: "Không tải được prompt riêng. Hãy quay lại màn rà soát, mở Cài đặt và thử lần nữa." };
  }
  if (aiConfigResult.status === "fulfilled") {
    state.aiConfig = aiConfigResult.value;
    syncCloudDraftsFromConfig();
    if (state.selectedProviderTab === "local" && isCloudActive()) {
      state.selectedProviderTab = state.aiConfig.active_provider as ProviderTab;
    }
  }
  clearUnavailableFullReview();
  state.settingsLoading = false;
  render();
}
function applyRuleDefaultSelection(rule: CustomRule): void {
  state.selectedRuleIds = rule.is_default
    ? [...new Set([...state.selectedRuleIds, rule.id])]
    : state.selectedRuleIds.filter(id => id !== rule.id);
}
async function saveCustomRule(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  if (state.customRulePending) return;
  const title = state.customRuleTitleDraft.trim().normalize("NFC");
  const prompt = state.customRuleDraft.trim().normalize("NFC");
  if (!title || [...title].length > customRuleTitleLimit) {
    state.customRuleTitleInvalid = true;
    state.settingsMessage = { tone: "error", text: `Tiêu đề là bắt buộc và tối đa ${customRuleTitleLimit} ký tự.` };
    render("#custom-rule-title");
    return;
  }
  if (!prompt) return;
  const operation = ++customRuleOperationSequence;
  state.customRulePending = true;
  state.customRuleTitleInvalid = false;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  state.settingsMessage = null;
  render("#custom-rule-prompt");
  try {
    const saved = await api.customRuleUpsert(state.editingCustomRuleId, title, prompt, state.customRuleDefaultDraft);
    if (operation !== customRuleOperationSequence) return;
    const existing = state.customRules.findIndex(rule => rule.id === saved.id);
    state.customRules = existing >= 0
      ? state.customRules.map(rule => rule.id === saved.id ? saved : rule)
      : [...state.customRules, saved];
    state.customRules.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    applyRuleDefaultSelection(saved);
    state.customRuleTitleDraft = "";
    state.customRuleDraft = "";
    state.customRuleDefaultDraft = false;
    state.editingCustomRuleId = null;
    state.lastDeletedRule = null;
    state.settingsMessage = { tone: "status", text: "Đã lưu prompt." };
  } catch {
    if (operation === customRuleOperationSequence) {
      state.customRulePromptInvalid = true;
      state.settingsMessage = { tone: "error", text: "Không lưu được prompt. Tổng nội dung tối đa 4.000 ký tự; hãy rút gọn rồi thử lại." };
    }
  } finally {
    if (operation === customRuleOperationSequence) {
      state.customRulePending = false;
      render("#custom-rule-prompt");
    }
  }
}
function newCustomRule(discardConfirmed = false): void {
  if (state.customRulePending || state.settingsLoading) return;
  if (!discardConfirmed && requestPromptAction({ kind: "new" })) return;
  state.editingCustomRuleId = null;
  state.customRuleTitleDraft = "";
  state.customRuleDraft = "";
  state.customRuleDefaultDraft = false;
  state.customRuleTitleInvalid = false;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  state.lastDeletedRule = null;
  state.settingsMessage = null;
  render("#custom-rule-title");
}
function editCustomRule(id: string, discardConfirmed = false): void {
  const rule = state.customRules.find(item => item.id === id);
  if (!rule || state.customRulePending) return;
  if (state.editingCustomRuleId === id) {
    render("#custom-rule-prompt");
    return;
  }
  if (!discardConfirmed && requestPromptAction({ kind: "edit", id })) return;
  state.editingCustomRuleId = id;
  state.customRuleTitleDraft = rule.title;
  state.customRuleDraft = rule.prompt;
  state.customRuleDefaultDraft = rule.is_default;
  state.customRuleTitleInvalid = false;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  state.lastDeletedRule = null;
  state.settingsMessage = null;
  render("#custom-rule-prompt");
}
function cancelCustomRuleEdit(): void {
  if (state.customRulePending) return;
  state.editingCustomRuleId = null;
  state.customRuleTitleDraft = "";
  state.customRuleDraft = "";
  state.customRuleDefaultDraft = false;
  state.customRuleTitleInvalid = false;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  state.settingsMessage = null;
  render("#custom-rule-prompt");
}
async function deleteCustomRule(id: string): Promise<void> {
  if (state.customRulePending) return;
  const rule = state.customRules.find(item => item.id === id);
  if (!rule) return;
  const operation = ++customRuleOperationSequence;
  state.customRulePending = true;
  state.settingsMessage = null;
  render(`[data-delete-rule="${id}"]`);
  try {
    const deleted = await api.customRuleDelete(id);
    if (operation !== customRuleOperationSequence) return;
    if (!deleted) throw new Error("not deleted");
    state.customRules = state.customRules.filter(item => item.id !== id);
    state.selectedRuleIds = state.selectedRuleIds.filter(item => item !== id);
    state.lastDeletedRule = rule;
    if (state.editingCustomRuleId === id) {
      state.editingCustomRuleId = null;
      state.customRuleTitleDraft = "";
      state.customRuleDraft = "";
      state.customRuleDefaultDraft = false;
      state.customRuleTitleInvalid = false;
      state.customRulePromptInvalid = false;
      state.pendingPromptAction = null;
    }
  } catch {
    if (operation === customRuleOperationSequence) state.settingsMessage = { tone: "error", text: "Không xoá được quy tắc riêng. Dữ liệu chưa thay đổi." };
  } finally {
    if (operation === customRuleOperationSequence) {
      state.customRulePending = false;
      render("#custom-rule-prompt");
    }
  }
}
async function undoDeleteCustomRule(): Promise<void> {
  const rule = state.lastDeletedRule;
  if (!rule || state.customRulePending) return;
  const operation = ++customRuleOperationSequence;
  state.customRulePending = true;
  state.settingsMessage = null;
  render("#undo-delete-rule");
  try {
    const restored = await api.customRuleUpsert(rule.id, rule.title, rule.prompt, rule.is_default);
    if (operation !== customRuleOperationSequence) return;
    state.customRules = [...state.customRules, restored].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    applyRuleDefaultSelection(restored);
    state.lastDeletedRule = null;
    state.settingsMessage = { tone: "status", text: "Đã khôi phục quy tắc riêng." };
  } catch {
    if (operation === customRuleOperationSequence) state.settingsMessage = { tone: "error", text: "Không khôi phục được quy tắc. Hãy thêm lại bằng biểu mẫu." };
  } finally {
    if (operation === customRuleOperationSequence) {
      state.customRulePending = false;
      render("#custom-rule-prompt");
    }
  }
}
async function setModelEnabled(enabled: boolean): Promise<void> {
  if (modelOperationBusy() || state.modelRemovalPending) return;
  const operation = ++modelOperationSequence;
  const previous = { model: state.model, useModel: state.useModel };
  modelOperationBaseline = previous;
  state.settingsMessage = null;
  try {
    if (enabled) {
      state.model = { ...state.model, state: "verifying" };
      render("#use-model");
      const next = await api.modelStatus(true);
      if (operation !== modelOperationSequence) return;
      state.model = next;
      state.useModel = modelCanFilter(next);
      state.fullReview = state.useModel && next.capabilities?.full_review === true;
      if (!state.useModel) state.settingsMessage = { tone: "error", text: "Model chưa cung cấp capability AI filter hoặc chưa thể khởi động. Hãy kiểm tra gói model." };
    } else {
      render("#use-model");
      const next = await api.modelDeactivate();
      if (operation !== modelOperationSequence) return;
      state.model = next;
      state.useModel = false;
    }
    saveModelPreference(state.useModel);
  } catch {
    if (operation === modelOperationSequence) {
      state.model = previous.model;
      state.useModel = previous.useModel;
      state.settingsMessage = { tone: "error", text: "Không đổi được trạng thái AI cục bộ. Trạng thái trước đó đã được giữ nguyên." };
    }
  } finally {
    if (operation === modelOperationSequence) modelOperationBaseline = null;
  }
  clearUnavailableFullReview();
  if (operation === modelOperationSequence) render("#use-model");
}
async function modelImport(): Promise<void> {
  if (modelOperationBusy() || state.modelRemovalPending) return;
  const operation = ++modelOperationSequence;
  const previous = { model: state.model, useModel: state.useModel };
  modelOperationBaseline = previous;
  state.model = { ...state.model, state: "importing" };
  state.settingsMessage = null;
  render("#model-import");
  try {
    const status = await api.modelImport();
    if (operation !== modelOperationSequence) return;
    if (!status) {
      state.model = previous.model;
      state.useModel = previous.useModel;
      state.settingsMessage = { tone: "status", text: "Không có tệp model nào được chọn; trạng thái hiện tại được giữ nguyên." };
      return;
    }
    state.model = status;
    state.useModel = modelCanFilter(status);
    state.fullReview = state.useModel && status.capabilities?.full_review === true;
    state.includeRuleFindings = false;
    saveModelPreference(state.useModel);
  } catch {
    if (operation === modelOperationSequence) {
      state.model = previous.model;
      state.useModel = previous.useModel;
      state.settingsMessage = { tone: "error", text: "Không nhập được gói model. Model đang dùng trước đó được giữ nguyên." };
    }
  } finally {
    if (operation === modelOperationSequence) {
      modelOperationBaseline = null;
      clearUnavailableFullReview();
      render("#model-import");
    }
  }
}
async function cancelModelOperation(): Promise<void> {
  if (!modelOperationBusy() || state.modelRemovalRunning) return;
  try {
    const cancelled = await api.modelCancel();
    if (!cancelled) throw new Error("not cancelled");
    const baseline = modelOperationBaseline;
    modelOperationSequence += 1;
    if (baseline) {
      state.model = baseline.model;
      state.useModel = baseline.useModel;
    } else {
      state.model = { state: "cancelled" };
      state.useModel = false;
    }
    modelOperationBaseline = null;
    state.settingsMessage = { tone: "status", text: "Đã dừng thao tác model." };
  } catch {
    state.settingsMessage = { tone: "error", text: "Không dừng được thao tác model. Hãy chờ tác vụ hiện tại kết thúc." };
  }
  clearUnavailableFullReview();
  render("#model-action");
}
async function removeModel(): Promise<void> {
  if (!state.modelRemovalPending || state.modelRemovalRunning || modelOperationBusy()) return;
  const operation = ++modelOperationSequence;
  const previous = { model: state.model, useModel: state.useModel };
  state.modelRemovalRunning = true;
  state.settingsMessage = null;
  render("#confirm-model-remove");
  try {
    const next = await api.modelRemove();
    if (operation !== modelOperationSequence) return;
    state.model = next;
    state.useModel = false;
    state.fullReview = false;
    state.includeRuleFindings = false;
    saveModelPreference(false);
    state.modelRemovalPending = false;
  } catch {
    if (operation === modelOperationSequence) {
      state.model = previous.model;
      state.useModel = previous.useModel;
      state.settingsMessage = { tone: "error", text: "Không gỡ được model. Model hiện tại được giữ nguyên." };
    }
  } finally {
    if (operation === modelOperationSequence) {
      state.modelRemovalRunning = false;
      render("#confirm-model-remove");
    }
  }
}
async function modelAction(): Promise<void> {
  if (state.modelRemovalPending || state.modelRemovalRunning) return;
  if (modelOperationBusy()) await cancelModelOperation();
}

async function testCloudConnection(): Promise<void> {
  const provider = state.selectedProviderTab;
  if (provider === "local" || state.cloudTestLoading) return;
  const keyInput = document.querySelector<HTMLInputElement>("#cloud-api-key")?.value;
  const urlInput = document.querySelector<HTMLInputElement>("#cloud-base-url")?.value;
  const modelInput = document.querySelector<HTMLInputElement>("#cloud-model-name")?.value;
  const draft = state.cloudDrafts[provider];
  if (keyInput !== undefined) draft.apiKey = keyInput;
  if (urlInput !== undefined) draft.baseUrl = urlInput;
  if (modelInput !== undefined) draft.modelName = modelInput;

  const saved = state.aiConfig?.configs.find(c => c.provider === provider);
  if (!draft.apiKey.trim() && (!saved || !saved.api_key)) {
    state.cloudTestResult = {
      ok: false,
      error: "API_KEY_REQUIRED",
      message: "Vui lòng nhập API Key trước khi kiểm tra kết nối.",
    };
    render("#cloud-api-key");
    return;
  }

  state.cloudTestLoading = true;
  state.cloudTestResult = null;
  render("#cloud-test-connection");
  try {
    const result = await api.aiConfigTestConnection({
      provider,
      apiKey: draft.apiKey.trim() || undefined,
      baseUrl: draft.baseUrl.trim() || undefined,
      modelName: draft.modelName.trim() || undefined,
    });
    state.cloudTestResult = result;
  } catch (err) {
    state.cloudTestResult = {
      ok: false,
      error: "TEST_FAILED",
      message: err instanceof Error ? err.message : String(err),
    };
  } finally {
    state.cloudTestLoading = false;
    render("#cloud-test-connection");
  }
}

async function saveAndActivateCloud(): Promise<void> {
  const provider = state.selectedProviderTab;
  if (provider === "local" || state.cloudSaving) return;
  const keyInput = document.querySelector<HTMLInputElement>("#cloud-api-key")?.value;
  const urlInput = document.querySelector<HTMLInputElement>("#cloud-base-url")?.value;
  const modelInput = document.querySelector<HTMLInputElement>("#cloud-model-name")?.value;
  const draft = state.cloudDrafts[provider];
  if (keyInput !== undefined) draft.apiKey = keyInput;
  if (urlInput !== undefined) draft.baseUrl = urlInput;
  if (modelInput !== undefined) draft.modelName = modelInput;

  const saved = state.aiConfig?.configs.find(c => c.provider === provider);
  if (!draft.apiKey.trim() && (!saved || !saved.api_key)) {
    state.settingsMessage = {
      tone: "error",
      text: "Vui lòng nhập API Key trước khi lưu và kích hoạt.",
    };
    render("#cloud-api-key");
    return;
  }

  state.cloudSaving = true;
  state.settingsMessage = null;
  render("#cloud-save-active");
  try {
    await api.aiConfigUpdate({
      provider,
      apiKey: draft.apiKey.trim() || undefined,
      baseUrl: draft.baseUrl.trim() || undefined,
      modelName: draft.modelName.trim() || undefined,
      isActive: true,
    });
    await api.aiConfigSetActive(provider);
    const updatedState = await api.aiConfigGet();
    state.aiConfig = updatedState;
    state.model = await api.modelStatus(true);
    state.useModel = true;
    state.fullReview = true;
    saveModelPreference(true);
    const updatedSaved = updatedState.configs.find(c => c.provider === provider);
    if (updatedSaved) {
      state.cloudDrafts[provider] = {
        apiKey: "",
        baseUrl: updatedSaved.base_url || draft.baseUrl,
        modelName: updatedSaved.model_name || draft.modelName,
      };
    }
    state.settingsMessage = {
      tone: "status",
      text: `Đã lưu và kích hoạt ${provider === "openai" ? "OpenAI / Tương thích" : "Google Gemini"}.`,
    };
  } catch {
    state.settingsMessage = {
      tone: "error",
      text: `Không lưu được cấu hình ${provider === "openai" ? "OpenAI" : "Gemini"}. Hãy thử lại.`,
    };
  } finally {
    state.cloudSaving = false;
    render("#cloud-save-active");
  }
}

async function activateLocalProvider(): Promise<void> {
  state.settingsMessage = null;
  try {
    await api.aiConfigSetActive("local");
    const updatedState = await api.aiConfigGet();
    state.aiConfig = updatedState;
    state.model = await api.modelStatus(true);
    state.useModel = modelCanFilter(state.model);
    state.fullReview = state.useModel && state.model.capabilities?.full_review === true;
    state.settingsMessage = {
      tone: "status",
      text: "Đã kích hoạt chế độ AI cục bộ (Offline).",
    };
  } catch {
    state.settingsMessage = {
      tone: "error",
      text: "Không chuyển được sang AI cục bộ. Hãy thử lại.",
    };
  }
  render("#activate-local-provider");
}

// Initial render immediately paints the UI
render();

window.addEventListener("keydown", event => {
  if (!event.ctrlKey || event.key.toLowerCase() !== "o") return;
  event.preventDefault();
  if (isWorkflowView()) void choose();
});
window.addEventListener("dragover", event => {
  event.preventDefault();
  if (isWorkflowView()) document.documentElement.classList.add("is-dragging");
});
window.addEventListener("dragleave", event => { if (!event.relatedTarget) document.documentElement.classList.remove("is-dragging"); });
window.addEventListener("drop", event => {
  event.preventDefault();
  document.documentElement.classList.remove("is-dragging");
  if (!isWorkflowView() || state.step === "processing") return;
  const path = (event.dataTransfer?.files[0] as File & { path?: string })?.path;
  if (!path) return;
  const selection = ++documentSelectionSequence;
  const requestedFromStep = state.step;
  const requestedView = state.view;
  void api.inspectDropped(path).then(info => {
    if (selection === documentSelectionSequence && state.view === requestedView && state.step === requestedFromStep) useDocument(info);
  }).catch(() => {
    if (selection !== documentSelectionSequence || state.view !== requestedView || state.step !== requestedFromStep) return;
    state.error = "SoátVăn chỉ nhận tệp .docx hợp lệ. Hãy chọn một tài liệu Word khác.";
    render("#choose");
  });
});

try {
  void api.onFileDrop(path => {
    if (!isWorkflowView() || state.step === "processing") return;
    const selection = ++documentSelectionSequence;
    const requestedFromStep = state.step;
    const requestedView = state.view;
    void api.inspectDropped(path).then(info => {
      if (selection === documentSelectionSequence && state.view === requestedView && state.step === requestedFromStep) useDocument(info);
    }).catch(() => {
      if (selection !== documentSelectionSequence || state.view !== requestedView || state.step !== requestedFromStep) return;
      state.error = "SoátVăn chỉ nhận tệp .docx hợp lệ.";
      render();
    });
  }).catch(() => {});
} catch { /* noop */ }

try {
  const initialModelPreference = loadModelPreference();
  const modelSequence = modelOperationSequence;
  const statusRequest = ++modelStatusRequestSequence;
  const customRuleSequence = customRuleOperationSequence;
  const request = ++customRuleRequestSequence;

  void Promise.allSettled([
    api.aiConfigGet(),
    api.modelStatus(initialModelPreference),
    api.customRuleList(),
  ]).then(([aiConfigRes, modelRes, rulesRes]) => {
    if (aiConfigRes.status === "fulfilled") {
      state.aiConfig = aiConfigRes.value;
      syncCloudDraftsFromConfig();
      if (isCloudActive()) {
        state.selectedProviderTab = state.aiConfig.active_provider as ProviderTab;
        state.useModel = true;
        state.fullReview = true;
      }
    }
    if (modelRes.status === "fulfilled" && modelSequence === modelOperationSequence && statusRequest === modelStatusRequestSequence) {
      state.model = modelRes.value;
      if (!isCloudActive()) {
        state.useModel = initialModelPreference && modelCanFilter(modelRes.value);
        state.fullReview = state.useModel && modelRes.value.capabilities?.full_review === true;
      }
    }
    if (rulesRes.status === "fulfilled" && customRuleSequence === customRuleOperationSequence && request === customRuleRequestSequence) {
      state.customRules = rulesRes.value;
      syncDefaultRuleSelection();
    }
    state.includeRuleFindings = false;
    clearUnavailableFullReview();
    render();
  }).catch(() => {});
} catch { /* noop */ }

try { void api.appVersion().then(version => { state.appVersion = version; render(); }).catch(() => {}); } catch { /* noop */ }


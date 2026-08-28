import "./styles.css";
import { api } from "./api";
import type { CustomRule, DocumentInfo, JobResult, ModelStatus, Preset, RuleOptions, Step } from "./contracts";

export interface Gemma4Model {
  id: string;
  name: string;
  badge: string;
  badgeClass?: string;
  ram: string;
  size: string;
  description: string;
}

const gemma4Catalog: Gemma4Model[] = [
  {
    id: "gemma-4-e2b",
    name: "Gemma 4 E2B Instruct",
    badge: "Nhẹ",
    ram: "RAM 8GB / CPU",
    size: "~1.5 GB",
    description: "Gói khoảng 1,5 GB dành cho máy có 8 GB RAM và có thể chạy bằng CPU.",
  },
  {
    id: "gemma-4-e4b",
    name: "Gemma 4 E4B Instruct",
    badge: "Trung bình",
    ram: "RAM 8GB – 16GB",
    size: "~2.8 GB",
    description: "Gói khoảng 2,8 GB dành cho máy có từ 8 GB đến 16 GB RAM.",
  },
  {
    id: "gemma-4-12b",
    name: "Gemma 4 12B Instruct",
    badge: "Lớn",
    ram: "RAM ≥ 16GB / GPU",
    size: "~7.5 GB",
    description: "Gói khoảng 7,5 GB dành cho máy có ít nhất 16 GB RAM và GPU.",
  },
];

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
  modelProgress: number;
  modelReceived: number;
  modelTotal: number;
  result: JobResult | null;
  model: ModelStatus;
  settingsLoading: boolean;
  customRules: CustomRule[];
  customRuleDraft: string;
  editingCustomRuleId: string | null;
  customRulePending: boolean;
  customRulePromptInvalid: boolean;
  pendingPromptAction: PendingPromptAction | null;
  settingsMessage: SettingsMessage;
  lastDeletedRule: CustomRule | null;
  error: string;
  selectedModelId: string;
  downloadingModelId: string | null;
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
  modelProgress: 0,
  modelReceived: 0,
  modelTotal: 0,
  result: null,
  model: { state: "not_installed" },
  settingsLoading: false,
  customRules: [],
  customRuleDraft: "",
  editingCustomRuleId: null,
  customRulePending: false,
  customRulePromptInvalid: false,
  pendingPromptAction: null,
  settingsMessage: null,
  lastDeletedRule: null,
  error: "",
  selectedModelId: "gemma-4-e4b",
  downloadingModelId: null,
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

function loadModelPreference(): boolean { try { return localStorage.getItem("soatvan.use-model.v1") !== "false"; } catch { return true; } }
function saveModelPreference(enabled: boolean): void { try { localStorage.setItem("soatvan.use-model.v1", String(enabled)); } catch { /* Storage can be unavailable in hardened WebViews. */ } }

function escape(value: string): string { const node = document.createElement("div"); node.textContent = value; return node.innerHTML; }
function formatBytes(value: number): string { return `${(value / 1024 / 1024).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} MB`; }
function selectedModel() { return gemma4Catalog.find(item => item.id === state.selectedModelId) ?? gemma4Catalog[1]; }
function focusSelectorFor(element: Element | null): string | null {
  if (!(element instanceof HTMLElement)) return null;
  if (element.id) return `#${element.id}`;
  if (element.dataset.settingsSection) return `[data-settings-section="${element.dataset.settingsSection}"]`;
  if (element instanceof HTMLInputElement && element.name === "gemma-select") return `input[name="gemma-select"][value="${element.value}"]`;
  if (element.dataset.editRule) return `[data-edit-rule="${element.dataset.editRule}"]`;
  if (element.dataset.deleteRule) return `[data-delete-rule="${element.dataset.deleteRule}"]`;
  return null;
}
function documentMetadata(doc: DocumentInfo): string {
  const pages = doc.page_count ? `${doc.page_count.toLocaleString("vi-VN")} trang · ` : "";
  return `${formatBytes(doc.size)} · ${pages}${doc.word_count.toLocaleString("vi-VN")} từ · ${doc.paragraph_count.toLocaleString("vi-VN")} đoạn · ${doc.table_cell_count.toLocaleString("vi-VN")} ô bảng`;
}
function modelCanFilter(model: ModelStatus): boolean { return model.state === "ready" && model.capabilities?.candidate_filter === true; }
function modelFilterAvailable(): boolean { return modelCanFilter(state.model); }
function fullReviewAvailable(): boolean { return state.useModel && modelFilterAvailable() && state.model.capabilities?.full_review === true; }
function clearUnavailableFullReview(): void {
  if (!fullReviewAvailable()) state.fullReview = false;
  if (!state.fullReview) state.includeRuleFindings = false;
}
function fullReviewOptionHtml(): string {
  if (!fullReviewAvailable()) return "";
  const experimental = state.model.trust === "local_unverified" ? " Đây là chế độ thử nghiệm vì model chưa được benchmark và phê duyệt phát hành." : "";
  const fullReviewLabel = state.includeRuleFindings ? "AI rà soát toàn văn" : "Chỉ dùng AI để rà soát";
  const includeRules = state.fullReview
    ? `<label class="setting-row include-rule-findings-option"><span><strong>Bổ sung cảnh báo từ bộ quy tắc code</strong><small>AI vẫn rà toàn văn; bộ quy tắc code chỉ chạy thêm và cộng các cảnh báo hợp lệ.</small></span><input type="checkbox" id="include-rule-findings" ${state.includeRuleFindings ? "checked" : ""}></label>`
    : "";
  return `<div class="full-review-options"><label class="setting-row full-review-option"><span><strong>${fullReviewLabel}</strong><small>Bật mặc định. AI dùng prompt tiếng Việt tích hợp để đọc toàn bộ thân bài và bảng.${experimental}</small></span><input type="checkbox" id="full-review" ${state.fullReview ? "checked" : ""}></label>${includeRules}</div>`;
}
function reviewModeDescription(): string {
  if (!state.fullReview) return "Bộ kiểm tra cơ bản sẽ tạo candidate, sau đó AI có thể lọc lại kết quả.";
  return state.includeRuleFindings
    ? "AI là lớp rà soát chính; bộ quy tắc code sẽ chạy thêm để bổ sung cảnh báo."
    : "AI sẽ dùng prompt tiếng Việt mặc định để tự tìm lỗi trong toàn bộ nội dung; bộ quy tắc code không chạy.";
}
function compiledCustomPrompt(): string { return state.useModel ? state.customRules.map(rule => rule.prompt).join("\n\n") : ""; }
function customRuleCharacterCount(): number { return state.customRules.reduce((total, rule) => total + [...rule.prompt].length, 0); }
function modelOperationBusy(): boolean { return state.modelRemovalRunning || ["downloading", "importing", "verifying"].includes(state.model.state); }
function isWorkflowView(): boolean { return state.view.kind === "workflow"; }
function settingsOperationLocked(): boolean { return modelOperationBusy() || modelOperationBaseline !== null || state.modelRemovalPending || state.customRulePending; }
function settingsSectionNavigationLocked(): boolean { return state.settingsLoading || settingsOperationLocked(); }
function customRuleDraftDirty(): boolean {
  const editing = state.customRules.find(rule => rule.id === state.editingCustomRuleId);
  return editing ? state.customRuleDraft !== editing.prompt : state.customRuleDraft.length > 0;
}
function settingsBusyMessage(): SettingsMessage {
  if (state.customRulePending) return { tone: "status", text: "Đang cập nhật prompt. Hãy chờ thao tác hoàn tất." };
  if (state.modelRemovalRunning) return { tone: "status", text: "Đang gỡ model khỏi máy. Hãy chờ thao tác hoàn tất." };
  if (state.modelRemovalPending) return { tone: "status", text: "Hãy chọn giữ lại hoặc gỡ model trước khi rời mục này." };
  if (state.model.state === "downloading") return { tone: "status", text: "Đang tải model. Bạn có thể huỷ thao tác bằng nút bên dưới." };
  if (state.model.state === "importing") return { tone: "status", text: "Đang nhập gói model. Bạn có thể huỷ thao tác bằng nút bên dưới." };
  if (state.model.state === "verifying") return { tone: "status", text: "Đang xác minh và khởi động model. Hãy chờ thao tác hoàn tất." };
  if (modelOperationBaseline !== null) return { tone: "status", text: "Đang cập nhật trạng thái AI cục bộ. Hãy chờ thao tác hoàn tất." };
  return null;
}
function settingsSectionHeading(section: SettingsSection): string {
  return section === "prompts" ? "#settings-prompts-title" : section === "review-rules" ? "#settings-review-rules-title" : "#settings-models-title";
}

function render(preferredFocus?: string): void {
  const previousFocus = focusSelectorFor(document.activeElement);
  const workflowView = isWorkflowView();
  const focusStep = workflowView && renderedStep !== null && renderedStep !== state.step;
  const focusSettings = renderedView !== "settings" && state.view.kind === "settings";
  const doc = state.document;
  const customRulesApply = state.customRules.length > 0 && state.useModel && modelFilterAvailable();
  const customRuleCount = state.customRules.length;
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
      ${state.step === "rules" && doc ? `<section class="workflow-card"><div class="workflow-context"><button class="back-button" id="back">← Chọn tệp khác</button><div class="file-chip"><strong>${escape(doc.name)}</strong><span>${documentMetadata(doc)}</span></div></div><div class="section-copy"><h1>Chuẩn bị rà soát</h1><p>${reviewModeDescription()}</p></div><div class="custom-rules-summary"><div><strong>${customRuleCount.toLocaleString("vi-VN")} quy tắc riêng</strong><span>${customRuleCount === 0 ? "Không có yêu cầu bổ sung; AI vẫn dùng prompt mặc định." : customRulesApply ? "Sẽ được gộp thành yêu cầu bổ sung cho prompt mặc định." : "Chưa được áp dụng vì AI cục bộ đang tắt hoặc chưa sẵn sàng."}</span></div><div class="button-row"><button class="button button--secondary" id="manage-custom-rules" type="button">Quản lý prompt</button><button class="button button--quiet" id="manage-review-rules" type="button">Xem quy tắc rà soát</button></div></div>${customRuleCount > 0 && !customRulesApply ? `<p class="settings-message settings-message--error" role="alert">Bật AI cục bộ để áp dụng các quy tắc riêng. <button class="inline-button" id="open-model-settings" type="button">Thiết lập AI</button></p>` : ""}${fullReviewOptionHtml()}<div class="workflow-actions"><button class="button button--primary" id="start">Bắt đầu xử lý</button></div>${errorHtml()}</section>` : ""}
      ${state.step === "processing" ? `<section class="workflow-card processing-panel"><div class="processing-status"><span class="spinner" aria-hidden="true"></span><div class="section-copy"><h1>${progressTitle()}</h1><p>Mọi xử lý tài liệu diễn ra trên máy này.</p></div></div><div class="progress-row"><div class="progress" role="progressbar" aria-label="Tiến độ xử lý" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${state.progress}"><span style="--progress-scale:${state.progress / 100}"></span></div><strong class="progress-value">${state.progress}%</strong></div><p class="sr-only progress-announcement" aria-live="polite" aria-atomic="true">${progressTitle()} ${state.progress}%</p><div class="workflow-actions"><button class="button button--secondary" id="cancel" ${state.jobStarting || state.cancelPending ? "disabled" : ""} ${state.jobStarting || state.cancelPending ? 'aria-busy="true"' : ""}>${state.jobStarting ? "Đang chuẩn bị…" : state.cancelPending ? "Đang dừng…" : "Dừng xử lý"}</button></div>${errorHtml()}</section>` : ""}
      ${(state.step === "result" || state.step === "no-findings") ? resultHtml() : ""}
    </main>` : settingsHtml()}
    <footer class="status-bar"><span><span aria-hidden="true">●</span> Xử lý cục bộ · không gửi nội dung tài liệu lên mạng</span><span>v${escape(state.appVersion)}</span></footer>`;
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
  if (state.progressStage === "model") return state.fullReview ? "Đang rà soát thân bài và bảng bằng AI cục bộ…" : "Đang phân loại các trường hợp nghi ngờ bằng model cục bộ…";
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
  const sections: [SettingsSection, string][] = [["prompts", "Prompt"], ["review-rules", "Quy tắc"], ["models", "AI cục bộ"]];
  const operationLocked = settingsOperationLocked();
  const content = settingsContent(section);
  return `<main class="settings-page" id="settings-page" aria-labelledby="settings-title" aria-describedby="settings-description" ${state.settingsLoading ? 'aria-busy="true"' : ""}>
    <header class="settings-page__header">
       <button class="back-button" id="settings-back" type="button" ${operationLocked ? 'aria-disabled="true" aria-describedby="settings-message"' : ""}>← Quay lại rà soát</button>
      <div><h1 id="settings-title">Cài đặt</h1><p id="settings-description">Quản lý prompt, xem bộ quy tắc cố định và model chạy trên máy.</p></div>
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
function progressHtml(value: number, label: string, id?: string): string { return `<div class="progress" ${id ? `id="${id}"` : ""} role="progressbar" aria-label="${escape(label)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${value}"><span style="--progress-scale:${value / 100}"></span></div>`; }
function settingsContent(section: SettingsSection): { body: string; footer: string } {
  if (section === "prompts") {
    const editing = state.customRules.find(rule => rule.id === state.editingCustomRuleId);
    const existingLength = editing ? [...editing.prompt].length : 0;
    const available = Math.max(0, customRulePromptLimit - customRuleCharacterCount() + existingLength);
    const draftLength = [...state.customRuleDraft].length;
    const controlsLocked = state.customRulePending || state.settingsLoading;
    const promptList = state.customRules.length
      ? state.customRules.map((rule, index) => {
          const id = escape(rule.id);
          const selected = rule.id === state.editingCustomRuleId;
          return `<div class="prompt-list__item"><button class="prompt-row" type="button" data-prompt-id="${id}" data-edit-rule="${id}" aria-label="Sửa prompt ${index + 1}" ${selected ? 'aria-current="true"' : ""} ${controlsLocked ? "disabled" : ""}><strong>Prompt ${index + 1}</strong><span>${escape(rule.prompt)}</span></button><button class="delete-button" type="button" data-delete-rule="${id}" aria-label="Xoá prompt ${index + 1}" ${controlsLocked ? "disabled" : ""}>Xoá</button></div>`;
        }).join("")
      : `<div class="empty-state"><strong>Chưa có prompt riêng.</strong><span>Tạo một prompt để cung cấp thuật ngữ, ngữ cảnh hoặc tiêu chí kiểm tra riêng cho AI.</span></div>`;
    return {
      body: `<section class="settings-section settings-prompts" id="settings-prompts" aria-labelledby="settings-prompts-title"><header class="settings-section__header"><div class="section-copy"><h2 id="settings-prompts-title">Prompt</h2><p>Mỗi mục là một đoạn hướng dẫn bổ sung cho AI; ứng dụng tự quản lý định dạng kết quả và vị trí bôi vàng.</p></div><button class="button button--secondary" id="new-custom-rule" type="button" ${controlsLocked ? "disabled" : ""}>Prompt mới</button></header><div class="prompt-manager"><div class="prompt-manager__master"><nav class="prompt-list" aria-label="Danh sách prompt" aria-live="polite">${promptList}</nav></div><section class="prompt-manager__detail" aria-labelledby="custom-rule-editor-title"><div class="section-copy"><h3 id="custom-rule-editor-title">${editing ? "Sửa prompt" : "Tạo prompt"}</h3><p>${editing ? "Chỉnh nội dung rồi lưu thay đổi, hoặc huỷ để trở về chế độ tạo mới." : "Mô tả thuật ngữ, ngữ cảnh hoặc tiêu chí mà AI cần chú ý."}</p></div><form class="custom-rule-form" id="custom-rule-form"><div class="control"><label for="custom-rule-prompt">Nội dung prompt</label><textarea id="custom-rule-prompt" name="prompt" rows="7" required maxlength="${available}" aria-describedby="custom-rule-help custom-rule-count${state.customRulePromptInvalid ? " settings-message" : ""}" ${state.customRulePromptInvalid ? 'aria-invalid="true"' : ""} placeholder="Ví dụ: Dùng thuật ngữ “khách hàng”, không dùng “client”." ${controlsLocked ? "disabled" : ""}>${escape(state.customRuleDraft)}</textarea><div class="field__meta"><span class="field__helper" id="custom-rule-help">Không yêu cầu AI trả cả câu/đoạn hoặc tự đặt cấu trúc output. Tổng tối đa 4.000 ký tự.</span><small id="custom-rule-count">${draftLength.toLocaleString("vi-VN")}/${available.toLocaleString("vi-VN")} ký tự còn dùng được cho mục này</small></div></div></form></section></div></section>`,
      footer: `<footer class="settings-footer"><div class="button-row settings-footer__actions"><button class="button button--primary" type="submit" form="custom-rule-form" ${controlsLocked || !state.customRuleDraft.trim() ? "disabled" : ""}>${state.customRulePending ? "Đang lưu…" : editing ? "Lưu thay đổi" : "Thêm prompt"}</button>${editing ? `<button class="button button--secondary" id="cancel-rule-edit" type="button" ${controlsLocked ? "disabled" : ""}>Huỷ sửa</button>` : ""}</div></footer>`,
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
  const progressBusy = ["downloading", "importing", "verifying"].includes(state.model.state);
  const controlsLocked = busy || modelOperationBaseline !== null || state.modelRemovalPending || state.settingsLoading;
  const activeModelId = state.model.model_id;
  const downloadInfo = state.model.state === "downloading" && state.modelTotal > 0
    ? `Đang tải model… ${formatBytes(state.modelReceived)} / ${formatBytes(state.modelTotal)} (${state.modelProgress}%)`
    : state.model.state === "downloading"
    ? `Đang tải model… ${state.modelProgress}%`
    : "";
  const title = state.modelRemovalRunning ? "Đang gỡ model…" : state.model.state === "ready" ? (state.model.release_approved ? "AI cục bộ đã sẵn sàng" : "AI cục bộ đang ở chế độ đánh giá") : state.model.state === "installed" ? (state.model.code ? "Model đã cài nhưng chưa thể khởi động" : "Model đã cài; AI đang tắt") : state.model.state === "downloading" ? downloadInfo : state.model.state === "importing" ? "Đang nhập gói model…" : state.model.state === "verifying" ? "Đang xác minh và khởi động model…" : state.model.state === "cancelled" ? "Đã dừng thao tác model" : state.model.state === "invalid" || state.model.state === "incompatible" ? "Gói model không hợp lệ hoặc không tương thích" : state.model.state === "error" ? "Không thể cài model" : "Chưa cài AI cục bộ";
  const chosen = selectedModel();
  const selectedIsActive = installed && activeModelId === chosen.id;
  const actionLabel = busy ? "Huỷ thao tác" : selectedIsActive ? "Đang sử dụng" : `Tải ${chosen.name}`;
  return {
    body: `
    <section class="settings-section settings-models" id="settings-models" aria-labelledby="settings-models-title">
    <header class="settings-section__header"><div class="section-copy"><h2 id="settings-models-title">AI cục bộ</h2><p>Chọn và quản lý model chạy hoàn toàn trên máy này.</p></div></header>
    <div class="model-card">
      <div class="model-card__header">
        <div>
          <strong id="model-status-title">${title}</strong>
          <p>${installed ? `${escape(activeModelId ?? "model")} · ${escape(state.model.version ?? "1.0.0")}` : "Ứng dụng vẫn kiểm tra đầy đủ bằng bộ quy tắc cục bộ."}</p>
        </div>
        ${installed && !state.modelRemovalPending ? `<button class="delete-button" id="model-remove" type="button" ${controlsLocked ? "disabled" : ""}>Gỡ model</button>` : ""}
      </div>
      ${progressBusy ? progressHtml(state.modelProgress, "Tiến độ thao tác model", "model-download-progress") : ""}
      ${state.modelRemovalPending ? `<div class="destructive-confirm" role="alert"><p>Gỡ model sẽ giải phóng dung lượng, nhưng bạn phải tải lại nếu muốn dùng AI sau này.</p><div class="button-row"><button class="button button--secondary button--small" id="cancel-model-remove" type="button" ${state.modelRemovalRunning ? "disabled" : ""}>Giữ lại</button><button class="button button--danger button--small" id="confirm-model-remove" type="button" ${state.modelRemovalRunning ? 'disabled aria-busy="true"' : ""}>${state.modelRemovalRunning ? "Đang gỡ…" : "Gỡ model"}</button></div></div>` : ""}
    </div>
    ${installed && state.model.trust === "local_unverified" ? `<p class="settings-message settings-message--error" role="status">Model GGUF nhập cục bộ chưa có chữ ký và benchmark phát hành. Có thể rà soát sâu để đánh giá trên máy này, nhưng kết quả chưa được phê duyệt cho phát hành.</p>` : ""}
    ${installed ? `<label class="setting-row"><span><strong>Dùng AI với quy tắc riêng</strong><small>Tắt để giải phóng bộ nhớ; bộ kiểm tra cơ bản vẫn tiếp tục hoạt động.</small></span><input type="checkbox" id="use-model" ${state.useModel ? "checked" : ""} ${controlsLocked ? "disabled" : ""}></label>` : ""}
    <div class="section-copy model-section-copy">
      <h3>Chọn phiên bản Gemma 4</h3>
      <p>Dung lượng tải và yêu cầu thiết bị được ghi rõ cho từng phiên bản.</p>
    </div>
    <fieldset class="model-catalog"><legend class="sr-only">Phiên bản Gemma 4</legend>
      ${gemma4Catalog.map(item => {
        const isActive = installed && activeModelId === item.id;
        const isSelected = state.selectedModelId === item.id;
        return `
          <label class="model-option ${isActive ? "active" : ""}">
            <div class="model-option-header">
              <div class="model-option-title">
                <input type="radio" name="gemma-select" value="${item.id}" ${isSelected ? "checked" : ""} ${controlsLocked ? "disabled" : ""}>
                <span>${escape(item.name)}</span>
              </div>
              <div class="model-badges">
                <span class="model-badge ${item.badgeClass ?? ""}">${escape(item.badge)}</span>
                ${isActive ? `<span class="model-badge model-badge--active">✓ Đang dùng</span>` : ""}
              </div>
            </div>
            <div class="model-meta">
              <span><strong>Dung lượng</strong> ${escape(item.size)}</span>
              <span><strong>Thiết bị</strong> ${escape(item.ram)}</span>
            </div>
            <p>${escape(item.description)}</p>
          </label>
        `;
      }).join("")}
    </fieldset>
    <p class="notice">Ứng dụng chỉ kết nối mạng khi bạn chủ động tải model. Nội dung tài liệu không được gửi đi.</p>
    </section>`,
    footer: `<footer class="settings-footer"><div class="button-row settings-footer__actions"><button class="button button--secondary" id="model-import" type="button" ${controlsLocked ? "disabled" : ""}>Nhập gói có sẵn</button><button class="button button--primary" id="model-action" type="button" ${state.settingsLoading || state.modelRemovalPending || (!busy && modelOperationBaseline !== null) || (selectedIsActive && !busy) ? "disabled" : ""} ${busy ? 'aria-busy="true"' : ""}>${escape(actionLabel)}</button></div></footer>`,
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
  document.querySelector<HTMLInputElement>("#full-review")?.addEventListener("change", event => {
    state.fullReview = fullReviewAvailable() && (event.target as HTMLInputElement).checked;
    if (!state.fullReview) state.includeRuleFindings = false;
    render("#full-review");
  });
  document.querySelector<HTMLInputElement>("#include-rule-findings")?.addEventListener("change", event => {
    state.includeRuleFindings = state.fullReview && fullReviewAvailable() && (event.target as HTMLInputElement).checked;
    render("#include-rule-findings");
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
    const submit = document.querySelector<HTMLButtonElement>('button[type="submit"][form="custom-rule-form"]');
    if (submit) submit.disabled = !state.customRuleDraft.trim();
  });
  document.querySelector<HTMLFormElement>("#custom-rule-form")?.addEventListener("submit", event => void saveCustomRule(event));
  document.querySelector("#new-custom-rule")?.addEventListener("click", () => newCustomRule());
  document.querySelector("#cancel-rule-edit")?.addEventListener("click", cancelCustomRuleEdit);
  document.querySelector("#cancel-prompt-discard")?.addEventListener("click", cancelPromptDiscard);
  document.querySelector("#confirm-prompt-discard")?.addEventListener("click", confirmPromptDiscard);
  document.querySelectorAll<HTMLButtonElement>("[data-edit-rule]").forEach(button => button.addEventListener("click", () => editCustomRule(button.dataset.editRule!)));
  document.querySelectorAll<HTMLButtonElement>("[data-delete-rule]").forEach(button => button.addEventListener("click", () => void deleteCustomRule(button.dataset.deleteRule!)));
  document.querySelector("#undo-delete-rule")?.addEventListener("click", () => void undoDeleteCustomRule());
  document.querySelector("#model-import")?.addEventListener("click", () => void modelImport());
  document.querySelector("#model-action")?.addEventListener("click", () => void modelAction());
  document.querySelector("#model-remove")?.addEventListener("click", () => { state.modelRemovalPending = true; state.settingsMessage = null; render("#cancel-model-remove"); });
  document.querySelector("#cancel-model-remove")?.addEventListener("click", () => { state.modelRemovalPending = false; state.settingsMessage = null; render("#model-remove"); });
  document.querySelector("#confirm-model-remove")?.addEventListener("click", () => void removeModel());
  document.querySelectorAll<HTMLInputElement>('input[name="gemma-select"]').forEach(input => input.addEventListener("change", () => { state.selectedModelId = input.value; render(`input[name="gemma-select"][value="${input.value}"]`); }));
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
  state.customRuleDraft = "";
  state.editingCustomRuleId = null;
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
  if (state.view.section === "prompts") state.customRulePromptInvalid = false;
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
  if (state.fullReview && !fullReviewAvailable()) {
    clearUnavailableFullReview();
    state.error = "Rà soát sâu cần AI cục bộ đang bật và sẵn sàng. Hãy bật AI rồi thử lại.";
    render("#start");
    return;
  }
  documentSelectionSequence += 1;
  settingsRequestSequence += 1;
  state.settingsLoading = false;
  const currentJobId = crypto.randomUUID();
  const effectiveUseModel = state.useModel && modelFilterAvailable();
  const customPrompt = effectiveUseModel ? compiledCustomPrompt() : "";
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
    const result = await api.startJob(currentJobId, state.document.path, defaultPreset, customPrompt, effectiveUseModel, defaultRuleOptions, [], state.fullReview, state.fullReview && state.includeRuleFindings);
    if (state.jobId === currentJobId && state.step === "processing") {
      state.jobId = "";
      state.cancelPending = false;
      state.result = result;
      state.error = "";
      state.step = result.status === "no_findings" && result.review?.status !== "partial" ? "no-findings" : "result";
      render();
    }
  } catch {
    if (state.jobId === currentJobId && state.step === "processing") {
      const cancellationWasPending = state.cancelPending;
      state.jobId = "";
      state.cancelPending = false;
      state.step = "rules";
      state.error = cancellationWasPending ? "" : "Không xử lý được tệp. Hãy kiểm tra tệp rồi thử lại; tệp gốc chưa bị thay đổi.";
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
  state.customRuleDraft = "";
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
  const [modelResult, customRulesResult] = await Promise.allSettled([api.modelStatus(state.useModel), api.customRuleList()]);
  if (request !== settingsRequestSequence || state.view.kind !== "settings" || state.step !== requestedStep) {
    if (request === settingsRequestSequence) {
      state.settingsLoading = false;
      render();
    }
    return;
  }
  if (modelResult.status === "fulfilled" && modelSequence === modelOperationSequence && modelStatusRequest === modelStatusRequestSequence) {
    state.model = modelResult.value;
    if (state.model.model_id && gemma4Catalog.some(item => item.id === state.model.model_id)) state.selectedModelId = state.model.model_id;
  } else if (modelResult.status === "rejected") {
    state.settingsMessage = { tone: "error", text: "Không đọc được trạng thái AI cục bộ. Quy tắc riêng vẫn có thể được chỉnh sửa." };
  }
  if (customRulesResult.status === "fulfilled" && customRuleSequence === customRuleOperationSequence && customRuleRequest === customRuleRequestSequence) {
    state.customRules = customRulesResult.value;
  } else if (customRulesResult.status === "rejected") {
    state.settingsMessage = { tone: "error", text: "Không tải được prompt riêng. Hãy quay lại màn rà soát, mở Cài đặt và thử lần nữa." };
  }
  clearUnavailableFullReview();
  state.settingsLoading = false;
  render();
}
async function saveCustomRule(event: SubmitEvent): Promise<void> {
  event.preventDefault();
  if (state.customRulePending) return;
  const prompt = state.customRuleDraft.trim().normalize("NFC");
  if (!prompt) return;
  const operation = ++customRuleOperationSequence;
  state.customRulePending = true;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  state.settingsMessage = null;
  render("#custom-rule-prompt");
  try {
    const saved = await api.customRuleUpsert(state.editingCustomRuleId, prompt);
    if (operation !== customRuleOperationSequence) return;
    const existing = state.customRules.findIndex(rule => rule.id === saved.id);
    state.customRules = existing >= 0
      ? state.customRules.map(rule => rule.id === saved.id ? saved : rule)
      : [...state.customRules, saved];
    state.customRules.sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    state.customRuleDraft = "";
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
  state.customRuleDraft = "";
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  state.lastDeletedRule = null;
  state.settingsMessage = null;
  render("#custom-rule-prompt");
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
  state.customRuleDraft = rule.prompt;
  state.customRulePromptInvalid = false;
  state.pendingPromptAction = null;
  state.lastDeletedRule = null;
  state.settingsMessage = null;
  render("#custom-rule-prompt");
}
function cancelCustomRuleEdit(): void {
  if (state.customRulePending) return;
  state.editingCustomRuleId = null;
  state.customRuleDraft = "";
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
    state.lastDeletedRule = rule;
    if (state.editingCustomRuleId === id) {
      state.editingCustomRuleId = null;
      state.customRuleDraft = "";
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
    const restored = await api.customRuleUpsert(rule.id, rule.prompt);
    if (operation !== customRuleOperationSequence) return;
    state.customRules = [...state.customRules, restored].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
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
  state.modelProgress = 0;
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
async function downloadGemmaModel(modelId: string): Promise<void> {
  if (modelOperationBusy() || state.modelRemovalPending) return;
  const operation = ++modelOperationSequence;
  const previous = { model: state.model, useModel: state.useModel };
  modelOperationBaseline = previous;
  state.selectedModelId = modelId;
  state.downloadingModelId = modelId;
  state.model = { state: "downloading" };
  state.modelProgress = 0;
  state.modelReceived = 0;
  state.modelTotal = 0;
  state.settingsMessage = null;
  render("#model-action");
  try {
    const status = await api.modelDownload(modelId);
    if (operation !== modelOperationSequence) return;
    state.model = status;
    state.useModel = modelCanFilter(status);
    state.fullReview = state.useModel && status.capabilities?.full_review === true;
    state.includeRuleFindings = false;
    saveModelPreference(state.useModel);
  } catch {
    if (operation === modelOperationSequence) {
      state.model = previous.model;
      state.useModel = previous.useModel;
      state.settingsMessage = { tone: "error", text: "Không tải được model. Model đang dùng trước đó được giữ nguyên." };
    }
  } finally {
    if (operation === modelOperationSequence) {
      state.downloadingModelId = null;
      modelOperationBaseline = null;
      clearUnavailableFullReview();
      render("#model-action");
    }
  }
}
async function cancelModelDownload(): Promise<void> {
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
    state.downloadingModelId = null;
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
  if (modelOperationBusy()) {
    await cancelModelDownload();
    return;
  }
  if (["ready", "installed"].includes(state.model.state) && state.model.model_id === state.selectedModelId) return;
  await downloadGemmaModel(state.selectedModelId);
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
  void api.modelStatus(initialModelPreference).then(model => {
    if (modelSequence !== modelOperationSequence || statusRequest !== modelStatusRequestSequence) return;
    state.model = model;
    state.useModel = initialModelPreference && modelCanFilter(model);
    state.fullReview = state.useModel && model.capabilities?.full_review === true;
    state.includeRuleFindings = false;
    clearUnavailableFullReview();
    if (model.model_id && gemma4Catalog.some(item => item.id === model.model_id)) state.selectedModelId = model.model_id;
    render();
  }).catch(() => {});
} catch { /* noop */ }

try {
  const customRuleSequence = customRuleOperationSequence;
  const request = ++customRuleRequestSequence;
  void api.customRuleList().then(rules => {
    if (customRuleSequence !== customRuleOperationSequence || request !== customRuleRequestSequence) return;
    state.customRules = rules;
    render();
  }).catch(() => {});
} catch { /* noop */ }

try { void api.appVersion().then(version => { state.appVersion = version; render(); }).catch(() => {}); } catch { /* noop */ }

try {
  void api.onModelProgress(event => {
    state.modelReceived = event.received;
    state.modelTotal = event.total;
    state.modelProgress = event.total > 0
      ? Math.min(100, Math.round((event.received / event.total) * 1000) / 10)
      : event.percent;
    if (state.model.state === "downloading") {
      const progress = document.querySelector<HTMLElement>("#model-download-progress");
      progress?.setAttribute("aria-valuenow", String(state.modelProgress));
      progress?.querySelector<HTMLElement>("span")?.style.setProperty("--progress-scale", String(state.modelProgress / 100));
      const title = document.querySelector<HTMLElement>("#model-status-title");
      if (title) title.textContent = event.total > 0
        ? `Đang tải model… ${formatBytes(event.received)} / ${formatBytes(event.total)} (${state.modelProgress}%)`
        : `Đang tải model… ${state.modelProgress}%`;
    }
  }).catch(() => {});
} catch { /* noop */ }

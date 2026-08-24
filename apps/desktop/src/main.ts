import "./styles.css";
import { api } from "./api";
import type { DictionaryEntry, DocumentInfo, JobResult, ModelStatus, Preset, RuleOptions, Step } from "./contracts";

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
    badge: "Siêu nhẹ",
    ram: "RAM 8GB / CPU",
    size: "~1.5 GB",
    description: "Tối ưu cho máy phổ thông, khởi động nhanh, phản hồi tức thì và tốn ít RAM.",
  },
  {
    id: "gemma-4-e4b",
    name: "Gemma 4 E4B Instruct",
    badge: "Khuyên dùng",
    badgeClass: "model-badge--recommended",
    ram: "RAM 8GB – 16GB",
    size: "~2.8 GB",
    description: "Cân bằng tối ưu giữa khả năng ngữ cảnh tiếng Việt và tốc độ xử lý trên máy cá nhân.",
  },
  {
    id: "gemma-4-12b",
    name: "Gemma 4 12B Instruct",
    badge: "Nâng cao",
    ram: "RAM ≥ 16GB / GPU",
    size: "~7.5 GB",
    description: "Độ thông minh và chính xác cao nhất cho văn bản phức tạp, thích hợp cho máy trạm.",
  },
];

const app = document.querySelector<HTMLDivElement>("#app")!;
let renderedStep: Step | null = null;
let renderedSettings = false;
const state: {
  step: Step;
  document: DocumentInfo | null;
  preset: Preset;
  prompt: string;
  ignoredWords: string;
  useModel: boolean;
  ruleOptions: RuleOptions;
  jobId: string;
  progress: number;
  progressStage: string;
  modelProgress: number;
  modelReceived: number;
  modelTotal: number;
  result: JobResult | null;
  model: ModelStatus;
  settings: boolean;
  tab: string;
  dictionary: DictionaryEntry[];
  error: string;
  selectedModelId: string;
  downloadingModelId: string | null;
} = {
  step: "file",
  document: null,
  preset: "standard",
  prompt: "",
  ignoredWords: "",
  jobId: "",
  progress: 0,
  progressStage: "",
  useModel: false,
  ruleOptions: loadRuleOptions(),
  modelProgress: 0,
  modelReceived: 0,
  modelTotal: 0,
  result: null,
  model: { state: "not_installed" },
  settings: false,
  tab: "dictionary",
  dictionary: [],
  error: "",
  selectedModelId: "gemma-4-e4b",
  downloadingModelId: null,
};

const presets: Record<Preset, { title: string; copy: string }> = {
  standard: { title: "Soát tiêu chuẩn", copy: "Chính tả, từ dễ nhầm, khoảng trắng, dấu câu và từ lặp." },
  administrative: { title: "Văn bản hành chính", copy: "Thêm quy tắc viết hoa tên cơ quan và cách trình bày hành chính." },
  spelling: { title: "Chỉ kiểm tra chính tả", copy: "Không đề xuất thay đổi văn phong hoặc cách diễn đạt." },
};

function optionsForPreset(preset: Preset): RuleOptions {
  return {
    technical: preset !== "spelling",
    repeated_words: preset !== "spelling",
    confusions: true,
    syllables: true,
    administrative_capitalization: preset === "administrative",
  };
}

function loadRuleOptions(): RuleOptions {
  try {
    const value = JSON.parse(localStorage.getItem("soatvan.rule-options.v1") ?? "null") as Partial<RuleOptions> | null;
    const defaults = optionsForPreset("standard");
    return value && (Object.keys(defaults) as (keyof RuleOptions)[]).every(key => typeof value[key] === "boolean") ? value as RuleOptions : defaults;
  } catch { return optionsForPreset("standard"); }
}
function saveRuleOptions(): void { try { localStorage.setItem("soatvan.rule-options.v1", JSON.stringify(state.ruleOptions)); } catch { /* Storage can be unavailable in hardened WebViews. */ } }
function loadModelPreference(): boolean { try { return localStorage.getItem("soatvan.use-model.v1") !== "false"; } catch { return true; } }
function saveModelPreference(enabled: boolean): void { try { localStorage.setItem("soatvan.use-model.v1", String(enabled)); } catch { /* Storage can be unavailable in hardened WebViews. */ } }

function escape(value: string): string { const node = document.createElement("div"); node.textContent = value; return node.innerHTML; }
function formatBytes(value: number): string { return `${(value / 1024 / 1024).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} MB`; }
function documentMetadata(doc: DocumentInfo): string {
  const pages = doc.page_count ? `${doc.page_count.toLocaleString("vi-VN")} trang · ` : "";
  return `${formatBytes(doc.size)} · ${pages}${doc.word_count.toLocaleString("vi-VN")} từ · ${doc.paragraph_count.toLocaleString("vi-VN")} đoạn · ${doc.table_cell_count.toLocaleString("vi-VN")} ô bảng`;
}

function render(): void {
  const focusStep = renderedStep !== state.step;
  const focusSettings = !renderedSettings && state.settings;
  const doc = state.document;
  app.innerHTML = `
    <header class="app-header"><div class="brand"><span class="brand__mark">SV</span><div><strong>SoátVăn</strong><span>Kiểm tra văn bản trên máy</span></div></div><button class="button button--quiet" id="settings" ${state.step === "processing" ? "disabled" : ""}>⚙ Cài đặt</button></header>
    <main class="workflow-shell"><ol class="stepper" aria-label="Tiến trình">${["Chọn file", "Quy tắc", "Xử lý", "Kết quả"].map((label, index) => `<li class="${stepIndex() === index ? "active" : ""} ${stepIndex() > index ? "done" : ""}"><span>${index + 1}</span><strong>${label}</strong></li>`).join("")}</ol>
    ${state.step === "file" ? `<section class="workflow-card"><div class="section-copy"><p class="step-label">Bước 1</p><h1>Chọn tệp Word cần kiểm tra</h1><p>Ứng dụng tạo file kết quả mới; file gốc luôn bất biến.</p></div><button class="drop-zone" id="choose"><span class="drop-icon">⇧</span><strong>Chọn hoặc kéo thả tệp .docx</strong><span>Ctrl+O để mở nhanh</span></button>${errorHtml()}</section>` : ""}
    ${state.step === "rules" && doc ? `<section class="workflow-card"><button class="back-button" id="back">← Chọn file khác</button><div class="file-chip"><strong>${escape(doc.name)}</strong><span>${documentMetadata(doc)}</span></div><div class="section-copy"><p class="step-label">Bước 2</p><h1>Chọn quy tắc kiểm tra</h1></div><div class="preset-grid">${(Object.keys(presets) as Preset[]).map(key => `<label class="preset ${state.preset === key ? "selected" : ""}"><input type="radio" name="preset" value="${key}" ${state.preset === key ? "checked" : ""}><strong>${presets[key].title}</strong><span>${presets[key].copy}</span></label>`).join("")}</div><label class="field"><span>Từ bỏ qua trong lần này <small>Ngăn cách bằng dấu phẩy hoặc xuống dòng</small></span><textarea id="ignored-words" rows="2" maxlength="60000" placeholder="Ví dụ: SoátVăn, tên đơn vị…">${escape(state.ignoredWords)}</textarea></label><label class="field"><span>Prompt quy tắc riêng <small>${state.model.state === "ready" && state.useModel ? "Model đã bật" : "Cần model AI ở trạng thái sẵn sàng"}</small></span><textarea id="prompt" rows="4" maxlength="1000" aria-describedby="prompt-count" ${state.model.state !== "ready" || !state.useModel ? "disabled" : ""} placeholder="Chỉ áp dụng cho các trường hợp nghi ngờ đã được hệ thống tìm thấy…">${escape(state.prompt)}</textarea><small id="prompt-count">${state.prompt.length.toLocaleString("vi-VN")}/1.000 ký tự</small></label><div class="workflow-actions"><button class="button button--primary" id="start">Bắt đầu xử lý →</button></div>${errorHtml()}</section>` : ""}
    ${state.step === "processing" ? `<section class="workflow-card centered" role="status" aria-live="polite"><span class="spinner"></span><div class="section-copy center"><p class="step-label">Bước 3</p><h1>${progressTitle()}</h1><p>Mọi xử lý tài liệu diễn ra trên máy này.</p></div><div class="progress"><span style="width:${state.progress}%"></span></div><strong>${state.progress}%</strong><button class="button button--secondary" id="cancel">Dừng xử lý</button></section>` : ""}
    ${(state.step === "result" || state.step === "no-findings") ? resultHtml() : ""}
    </main><footer class="status-bar"><span>● Xử lý cục bộ · không gửi nội dung tài liệu lên mạng</span><span>v0.1.0</span></footer>${state.settings ? settingsHtml() : ""}`;
  bind();
  renderedStep = state.step;
  renderedSettings = state.settings;
  if (focusStep || focusSettings) queueMicrotask(() => { const target = document.querySelector<HTMLElement>(focusSettings ? "#settings-title" : ".workflow-card h1"); target?.setAttribute("tabindex", "-1"); target?.focus(); });
}

function stepIndex(): number { return state.step === "file" ? 0 : state.step === "rules" ? 1 : state.step === "processing" ? 2 : 3; }
function errorHtml(): string { return state.error ? `<p class="error" role="alert">${escape(state.error)}</p>` : ""; }
function progressTitle(): string { return state.progressStage === "model" ? "Đang phân loại các trường hợp nghi ngờ bằng model cục bộ…" : state.progress < 25 ? "Đang đọc cấu trúc tệp Word…" : state.progress < 75 ? "Đang áp dụng quy tắc…" : state.progress < 90 ? "Đang kiểm tra vị trí cảnh báo…" : "Đang tạo file kết quả…"; }
function resultHtml(): string {
  if (state.step === "no-findings") return `<section class="workflow-card centered"><div class="success">✓</div><div class="section-copy center"><p class="step-label">Bước 4</p><h1>Không phát hiện cảnh báo</h1><p>Không tạo file bản sao. File gốc vẫn giữ nguyên.</p></div><button class="button button--primary" id="restart">Kiểm tra file khác</button></section>`;
  const path = state.result?.output_path ?? "";
  return `<section class="workflow-card centered"><div class="success">✓</div><div class="section-copy center"><p class="step-label">Bước 4</p><h1>Đã tạo file kết quả</h1><p>Ứng dụng không hiển thị preview. Hãy mở bằng Microsoft Word để xem đánh dấu.</p></div><div class="result-file"><strong>${escape(path.split(/[\\/]/).pop() ?? path)}</strong><span>${escape(path)}</span></div><div class="summary"><div><span>Cảnh báo</span><strong>${state.result?.finding_count ?? 0}</strong></div><div><span>File gốc</span><strong>Không thay đổi</strong></div></div><div class="button-row"><button class="button button--primary" id="open">Mở file kết quả</button><button class="button button--secondary" id="reveal">Mở thư mục</button></div><button class="back-button" id="restart">Xử lý file khác</button></section>`;
}

function settingsHtml(): string {
  return `<div class="modal" role="presentation"><section class="settings" role="dialog" aria-modal="true" aria-labelledby="settings-title"><header><div><h2 id="settings-title">Cài đặt</h2><p>Từ điển, preset và model cục bộ.</p></div><button class="icon-button" id="close-settings" aria-label="Đóng">×</button></header><nav class="tabs">${[["dictionary","Từ điển"],["rules","Quy tắc / preset"],["model","Model"]].map(([id,label]) => `<button data-tab="${id}" class="${state.tab === id ? "active" : ""}">${label}</button>`).join("")}</nav><div class="settings-body">${settingsBody()}</div></section></div>`;
}
function settingsBody(): string {
  if (state.tab === "dictionary") return `<div class="toolbar"><input id="dictionary-search" placeholder="Tìm từ…"><button class="button button--secondary" id="import-csv">Nhập CSV</button><button class="button button--secondary" id="export-csv">Xuất CSV</button></div><form class="dictionary-form" id="dictionary-form"><input name="word" required maxlength="120" placeholder="Từ được chấp nhận"><input name="note" maxlength="500" placeholder="Ghi chú"><button class="button button--primary">Thêm / cập nhật</button></form><div class="dictionary-list">${state.dictionary.length ? state.dictionary.map(entry => `<div><span><strong>${escape(entry.word)}</strong><small>${escape(entry.note || "Không có ghi chú")}</small></span><button data-delete="${escape(entry.word)}" aria-label="Xoá ${escape(entry.word)}">Xoá</button></div>`).join("") : "<p>Chưa có mục từ nào.</p>"}</div>`;
  if (state.tab === "rules") return `<div class="section-copy"><h3>Nhóm quy tắc đang dùng</h3><p>Các lựa chọn này áp dụng cho lần xử lý tiếp theo.</p></div>${([['technical','Khoảng trắng và dấu câu'],['repeated_words','Từ lặp'],['confusions','Từ dễ nhầm'],['syllables','Âm tiết tiếng Việt'],['administrative_capitalization','Viết hoa hành chính']] as const).map(([key,label]) => `<label class="setting-row"><span><strong>${label}</strong></span><input type="checkbox" data-rule="${key}" ${state.ruleOptions[key] ? "checked" : ""}></label>`).join("")}`;
  const installed = state.model.state === "ready" || state.model.state === "installed";
  const busy = ["downloading", "importing", "verifying"].includes(state.model.state);
  const activeModelId = state.model.model_id;
  const downloadInfo = state.model.state === "downloading" && state.modelTotal > 0
    ? `Đang tải model… ${formatBytes(state.modelReceived)} / ${formatBytes(state.modelTotal)} (${state.modelProgress}%)`
    : state.model.state === "downloading"
    ? `Đang tải model… ${state.modelProgress}%`
    : "";
  const title = state.model.state === "ready" ? "Model đã sẵn sàng" : state.model.state === "installed" ? (state.model.code ? "Model đã cài · runtime chưa sẵn sàng" : "Model đã cài · AI đang tắt") : state.model.state === "downloading" ? downloadInfo : state.model.state === "importing" ? "Đang nhập gói model…" : state.model.state === "verifying" ? "Đang xác minh và khởi động model…" : state.model.state === "invalid" || state.model.state === "incompatible" ? "Model không hợp lệ hoặc không tương thích" : state.model.state === "error" ? "Không thể cài model" : "Chưa cài model AI";
  return `
    <div class="model-card">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:0.5rem; flex-wrap:wrap;">
        <div>
          <strong>${title}</strong>
          <p style="margin:0.25rem 0 0 0;">${installed ? `${escape(activeModelId ?? "model")} · ${escape(state.model.version ?? "1.0.0")}` : "Ứng dụng vẫn chạy đầy đủ bằng tầng luật."}</p>
        </div>
        ${installed ? `<button class="button button--secondary button--small" id="model-remove" ${busy ? "disabled" : ""}>Xoá model</button>` : ""}
      </div>
      ${busy ? `<div class="progress" style="margin-top:0.75rem;"><span style="width:${state.modelProgress}%"></span></div>` : ""}
    </div>
    ${installed ? `<label class="setting-row"><span><strong>Dùng AI để lọc candidate</strong><small>Tắt sẽ giải phóng runtime và chạy hoàn toàn bằng tầng luật.</small></span><input type="checkbox" id="use-model" ${state.useModel ? "checked" : ""} ${busy ? "disabled" : ""}></label>` : ""}
    <div class="section-copy" style="margin-top:0.5rem;">
      <h3>Chọn phiên bản Gemma 4</h3>
      <p>Chọn phiên bản phù hợp với cấu hình máy để tải về và kích hoạt:</p>
    </div>
    <div class="model-catalog">
      ${gemma4Catalog.map(item => {
        const isActive = installed && activeModelId === item.id;
        const isDownloading = state.model.state === "downloading" && state.downloadingModelId === item.id;
        const isSelected = state.selectedModelId === item.id;
        return `
          <div class="model-option ${isActive ? "active" : ""}" data-model-id="${item.id}">
            <div class="model-option-header">
              <div class="model-option-title">
                <input type="radio" name="gemma-select" value="${item.id}" ${isSelected ? "checked" : ""} ${busy ? "disabled" : ""}>
                <span>${escape(item.name)}</span>
              </div>
              <div style="display:flex; gap:0.35rem; align-items:center;">
                <span class="model-badge ${item.badgeClass ?? ""}">${escape(item.badge)}</span>
                ${isActive ? `<span class="model-badge model-badge--active">✓ Đang dùng</span>` : ""}
              </div>
            </div>
            <div class="model-meta">
              <span>💾 ${escape(item.size)}</span>
              <span>⚡ ${escape(item.ram)}</span>
            </div>
            <p>${escape(item.description)}</p>
            <div class="model-option-actions">
              ${isDownloading
                ? `<button class="button button--secondary button--small" data-model-cancel="${item.id}">Huỷ · ${state.modelTotal > 0 ? `${formatBytes(state.modelReceived)} / ${formatBytes(state.modelTotal)}` : `${state.modelProgress}%`}</button>`
                : isActive
                ? `<span style="font-size:0.75rem; color:var(--color-success); font-weight:700;">✓ Đang kích hoạt</span>`
                : `<button class="button button--primary button--small" data-model-download="${item.id}" ${busy ? "disabled" : ""}>Tải & Kích hoạt</button>`
              }
            </div>
          </div>
        `;
      }).join("")}
    </div>
    <p class="notice">Chỉ kết nối mạng sau khi bạn chủ động bấm tải. Nội dung tài liệu không bao giờ được gửi đi.</p>
    <div class="button-row">
      <button class="button button--secondary" id="model-import" ${busy ? "disabled" : ""}>Nhập gói từ máy (.svmodel / .zip)</button>
      <button class="button button--primary" id="model-action">${busy ? "Huỷ" : installed ? "Xoá model" : "Tải model"}</button>
    </div>`;
}

function bind(): void {
  document.querySelector("#settings")?.addEventListener("click", openSettings);
  document.querySelector("#close-settings")?.addEventListener("click", () => { state.settings = false; render(); });
  document.querySelector("#choose")?.addEventListener("click", choose);
  document.querySelector("#back")?.addEventListener("click", reset);
  document.querySelector("#restart")?.addEventListener("click", reset);
  document.querySelector("#start")?.addEventListener("click", start);
  document.querySelector("#cancel")?.addEventListener("click", () => void cancel());
  document.querySelector("#open")?.addEventListener("click", () => void api.openOutput(state.result!.output_path!));
  document.querySelector("#reveal")?.addEventListener("click", () => void api.openOutput(state.result!.output_path!, true));
  document.querySelectorAll<HTMLInputElement>('input[name="preset"]').forEach(input => input.addEventListener("change", () => { state.preset = input.value as Preset; state.ruleOptions = optionsForPreset(state.preset); saveRuleOptions(); render(); }));
  document.querySelector<HTMLTextAreaElement>("#prompt")?.addEventListener("input", event => {
    state.prompt = (event.target as HTMLTextAreaElement).value;
    const counter = document.querySelector<HTMLElement>("#prompt-count");
    if (counter) counter.textContent = `${state.prompt.length.toLocaleString("vi-VN")}/1.000 ký tự`;
  });
  document.querySelector<HTMLTextAreaElement>("#ignored-words")?.addEventListener("input", event => { state.ignoredWords = (event.target as HTMLTextAreaElement).value; });
  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach(button => button.addEventListener("click", () => { state.tab = button.dataset.tab!; render(); }));
  document.querySelectorAll<HTMLInputElement>("[data-rule]").forEach(input => input.addEventListener("change", () => { const key = input.dataset.rule as keyof RuleOptions; state.ruleOptions = { ...state.ruleOptions, [key]: input.checked }; saveRuleOptions(); }));
  document.querySelector<HTMLInputElement>("#use-model")?.addEventListener("change", event => { void setModelEnabled((event.target as HTMLInputElement).checked); });
  document.querySelector<HTMLFormElement>("#dictionary-form")?.addEventListener("submit", event => void saveWord(event));
  document.querySelector<HTMLInputElement>("#dictionary-search")?.addEventListener("input", event => void loadDictionary((event.target as HTMLInputElement).value));
  document.querySelectorAll<HTMLButtonElement>("[data-delete]").forEach(button => button.addEventListener("click", () => void deleteWord(button.dataset.delete!)));
  document.querySelector("#import-csv")?.addEventListener("click", () => void importCsv());
  document.querySelector("#export-csv")?.addEventListener("click", () => void api.dictionaryExport());
  document.querySelector("#model-import")?.addEventListener("click", () => void modelImport());
  document.querySelector("#model-action")?.addEventListener("click", () => void modelAction());
  document.querySelector("#model-remove")?.addEventListener("click", () => void removeModel());
  document.querySelectorAll<HTMLInputElement>('input[name="gemma-select"]').forEach(input => input.addEventListener("change", () => { state.selectedModelId = input.value; render(); }));
  document.querySelectorAll<HTMLElement>(".model-option").forEach(el => el.addEventListener("click", event => { if ((event.target as HTMLElement).tagName !== "BUTTON" && (event.target as HTMLElement).tagName !== "INPUT") { state.selectedModelId = el.dataset.modelId!; render(); } }));
  document.querySelectorAll<HTMLButtonElement>("[data-model-download]").forEach(btn => btn.addEventListener("click", event => { event.stopPropagation(); void downloadGemmaModel(btn.dataset.modelDownload!); }));
  document.querySelectorAll<HTMLButtonElement>("[data-model-cancel]").forEach(btn => btn.addEventListener("click", event => { event.stopPropagation(); void cancelModelDownload(); }));
}

async function choose(): Promise<void> { try { const selected = await api.chooseDocument(); if (selected) { state.document = selected; state.step = "rules"; state.error = ""; render(); } } catch { state.error = "Không thể mở tệp DOCX này."; render(); } }
function reset(): void { Object.assign(state, { step: "file", document: null, result: null, progress: 0, progressStage: "", error: "", prompt: "", ignoredWords: "", jobId: "" }); render(); }
async function start(): Promise<void> {
  if (!state.document) return;
  const currentJobId = crypto.randomUUID();
  state.step = "processing"; state.progress = 0; state.progressStage = ""; state.jobId = currentJobId; state.error = ""; render();
  const unlisten = await api.onProgress(event => { if (state.jobId === currentJobId && event.job_id === currentJobId) { state.progress = event.percent; state.progressStage = event.stage; render(); } });
  const ignoredWords = [...new Set(state.ignoredWords.split(/[\n,]/).map(word => word.trim()).filter(Boolean))];
  try { const result = await api.startJob(currentJobId, state.document.path, state.preset, state.prompt, state.useModel, state.ruleOptions, ignoredWords); if (state.jobId === currentJobId && state.step === "processing") { state.result = result; state.step = result.status === "no_findings" ? "no-findings" : "result"; } }
  catch { if (state.jobId === currentJobId && state.step === "processing") { state.step = "rules"; state.error = "Không thể xử lý tệp. File gốc không bị thay đổi."; } }
  finally { unlisten(); render(); }
}
async function cancel(): Promise<void> { const cancelledJobId = state.jobId; await api.cancelJob(cancelledJobId); if (state.jobId === cancelledJobId) { state.jobId = ""; state.step = "rules"; state.progress = 0; state.progressStage = ""; render(); } }
async function openSettings(): Promise<void> {
  state.settings = true;
  render();
  try { state.model = await api.modelStatus(state.useModel); }
  catch { state.model = { state: "error", code: "MODEL_STATUS_FAILED" }; }
  await loadDictionary();
}
async function loadDictionary(query = ""): Promise<void> { try { state.dictionary = await api.dictionaryList(query); } catch { state.dictionary = []; } render(); }
async function saveWord(event: SubmitEvent): Promise<void> { event.preventDefault(); const data = new FormData(event.currentTarget as HTMLFormElement); await api.dictionaryUpsert(String(data.get("word")), String(data.get("note"))); await loadDictionary(); }
async function deleteWord(word: string): Promise<void> { await api.dictionaryDelete(word); await loadDictionary(); }
async function importCsv(): Promise<void> { await api.dictionaryImport(); await loadDictionary(); }
async function setModelEnabled(enabled: boolean): Promise<void> {
  try {
    if (enabled) {
      state.model = { ...state.model, state: "verifying" };
      render();
      state.model = await api.modelStatus(true);
      state.useModel = state.model.state === "ready";
    } else {
      state.model = await api.modelDeactivate();
      state.useModel = false;
      state.prompt = "";
    }
    saveModelPreference(state.useModel);
  } catch {
    state.useModel = false;
    saveModelPreference(false);
    state.model = { state: "error", code: "MODEL_TOGGLE_FAILED" };
  }
  render();
}
async function modelImport(): Promise<void> { state.model = { state: "importing" }; state.modelProgress = 0; render(); try { const status = await api.modelImport(); if (state.model.state !== "cancelled") { state.model = status ?? { state: "not_installed" }; state.useModel = state.model.state === "ready"; if (state.useModel) saveModelPreference(true); } } catch { if (state.model.state !== "cancelled") state.model = { state: "error", code: "MODEL_IMPORT_FAILED" }; } render(); }
async function downloadGemmaModel(modelId: string): Promise<void> {
  state.selectedModelId = modelId;
  state.downloadingModelId = modelId;
  state.model = { state: "downloading" };
  state.modelProgress = 0;
  state.modelReceived = 0;
  state.modelTotal = 0;
  render();
  try {
    const status = await api.modelDownload(modelId);
    if (state.model.state !== "cancelled") {
      state.model = status;
      state.useModel = state.model.state === "ready";
      if (state.useModel) saveModelPreference(true);
    }
  } catch {
    if (state.model.state !== "cancelled") state.model = { state: "error", code: "MODEL_OPERATION_FAILED" };
  } finally {
    state.downloadingModelId = null;
    render();
  }
}
async function cancelModelDownload(): Promise<void> {
  await api.modelCancel();
  state.model = { state: "cancelled" };
  state.downloadingModelId = null;
  render();
}
async function removeModel(): Promise<void> {
  try {
    state.model = await api.modelRemove();
    state.useModel = false;
    saveModelPreference(false);
    state.prompt = "";
  } catch {
    state.model = { state: "error", code: "MODEL_OPERATION_FAILED" };
  }
  render();
}
async function modelAction(): Promise<void> {
  if (["downloading", "importing", "verifying"].includes(state.model.state)) {
    await cancelModelDownload();
    return;
  }
  if (["ready", "installed"].includes(state.model.state)) {
    await removeModel();
  } else {
    await downloadGemmaModel(state.selectedModelId);
  }
}

// Initial render immediately paints the UI
render();

window.addEventListener("keydown", event => { const target = event.target; const editing = target instanceof HTMLElement && target.matches("input, textarea, select"); if (event.ctrlKey && event.key.toLowerCase() === "o") { event.preventDefault(); void choose(); } if (event.key === "Escape" && state.settings && !editing) { state.settings = false; render(); } });
window.addEventListener("dragover", event => event.preventDefault());
window.addEventListener("drop", event => { event.preventDefault(); const path = (event.dataTransfer?.files[0] as File & { path?: string })?.path; if (path) void api.inspectDropped(path).then(info => { state.document = info; state.step = "rules"; render(); }); });

try {
  void api.onFileDrop(path => {
    void api.inspectDropped(path).then(info => {
      state.document = info;
      state.step = "rules";
      state.error = "";
      render();
    }).catch(() => {
      state.error = "SoátVăn chỉ nhận tệp .docx hợp lệ.";
      render();
    });
  });
} catch { /* noop */ }

try {
  const initialModelPreference = loadModelPreference();
  void api.modelStatus(initialModelPreference).then(model => {
    state.model = model;
    state.useModel = initialModelPreference && model.state === "ready";
    render();
  }).catch(() => {});
} catch { /* noop */ }

try {
  void api.onModelProgress(event => {
    state.modelReceived = event.received;
    state.modelTotal = event.total;
    state.modelProgress = event.total > 0
      ? Math.min(100, Math.round((event.received / event.total) * 1000) / 10)
      : event.percent;
    if (state.model.state === "downloading") render();
  }).catch(() => {});
} catch { /* noop */ }

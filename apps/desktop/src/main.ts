import "./styles.css";
import { api } from "./api";
import type { DictionaryEntry, DocumentInfo, JobResult, ModelStatus, Preset, Step } from "./contracts";

const app = document.querySelector<HTMLDivElement>("#app")!;
const state: { step: Step; document: DocumentInfo | null; preset: Preset; prompt: string; jobId: string; progress: number; result: JobResult | null; model: ModelStatus; settings: boolean; tab: string; dictionary: DictionaryEntry[]; error: string } = {
  step: "file", document: null, preset: "standard", prompt: "", jobId: "", progress: 0,
  result: null, model: { state: "not_installed" }, settings: false, tab: "dictionary", dictionary: [], error: "",
};

const presets: Record<Preset, { title: string; copy: string }> = {
  standard: { title: "Soát tiêu chuẩn", copy: "Chính tả, từ dễ nhầm, khoảng trắng, dấu câu và từ lặp." },
  administrative: { title: "Văn bản hành chính", copy: "Thêm quy tắc viết hoa tên cơ quan và cách trình bày hành chính." },
  spelling: { title: "Chỉ kiểm tra chính tả", copy: "Không đề xuất thay đổi văn phong hoặc cách diễn đạt." },
};

function escape(value: string): string { const node = document.createElement("div"); node.textContent = value; return node.innerHTML; }
function formatBytes(value: number): string { return `${(value / 1024 / 1024).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} MB`; }

function render(): void {
  const doc = state.document;
  app.innerHTML = `
    <header class="app-header"><div class="brand"><span class="brand__mark">SV</span><div><strong>SoátVăn</strong><span>Kiểm tra văn bản trên máy</span></div></div><button class="button button--quiet" id="settings">⚙ Cài đặt</button></header>
    <main class="workflow-shell"><ol class="stepper" aria-label="Tiến trình">${["Chọn file", "Quy tắc", "Xử lý", "Kết quả"].map((label, index) => `<li class="${stepIndex() === index ? "active" : ""} ${stepIndex() > index ? "done" : ""}"><span>${index + 1}</span><strong>${label}</strong></li>`).join("")}</ol>
    ${state.step === "file" ? `<section class="workflow-card"><div class="section-copy"><p class="step-label">Bước 1</p><h1>Chọn tệp Word cần kiểm tra</h1><p>Ứng dụng tạo file kết quả mới; file gốc luôn bất biến.</p></div><button class="drop-zone" id="choose"><span class="drop-icon">⇧</span><strong>Chọn hoặc kéo thả tệp .docx</strong><span>Ctrl+O để mở nhanh</span></button>${errorHtml()}</section>` : ""}
    ${state.step === "rules" && doc ? `<section class="workflow-card"><button class="back-button" id="back">← Chọn file khác</button><div class="file-chip"><strong>${escape(doc.name)}</strong><span>${formatBytes(doc.size)} · ${doc.paragraph_count} đoạn · ${doc.table_cell_count} ô bảng</span></div><div class="section-copy"><p class="step-label">Bước 2</p><h1>Chọn quy tắc kiểm tra</h1></div><div class="preset-grid">${(Object.keys(presets) as Preset[]).map(key => `<label class="preset ${state.preset === key ? "selected" : ""}"><input type="radio" name="preset" value="${key}" ${state.preset === key ? "checked" : ""}><strong>${presets[key].title}</strong><span>${presets[key].copy}</span></label>`).join("")}</div><label class="field"><span>Prompt quy tắc riêng <small>${state.model.state === "ready" ? "Model đã sẵn sàng" : "Cần cài model AI"}</small></span><textarea id="prompt" rows="4" maxlength="1000" ${state.model.state !== "ready" ? "disabled" : ""} placeholder="Ví dụ: ưu tiên thuật ngữ của đơn vị…">${escape(state.prompt)}</textarea></label><div class="workflow-actions"><button class="button button--primary" id="start">Bắt đầu xử lý →</button></div>${errorHtml()}</section>` : ""}
    ${state.step === "processing" ? `<section class="workflow-card centered"><span class="spinner"></span><div class="section-copy center"><p class="step-label">Bước 3</p><h1>${progressTitle()}</h1><p>Mọi xử lý tài liệu diễn ra trên máy này.</p></div><div class="progress"><span style="width:${state.progress}%"></span></div><strong>${state.progress}%</strong><button class="button button--secondary" id="cancel">Dừng xử lý</button></section>` : ""}
    ${(state.step === "result" || state.step === "no-findings") ? resultHtml() : ""}
    </main><footer class="status-bar"><span>● Xử lý cục bộ · không gửi nội dung tài liệu lên mạng</span><span>v0.1.0</span></footer>${state.settings ? settingsHtml() : ""}`;
  bind();
}

function stepIndex(): number { return state.step === "file" ? 0 : state.step === "rules" ? 1 : state.step === "processing" ? 2 : 3; }
function errorHtml(): string { return state.error ? `<p class="error" role="alert">${escape(state.error)}</p>` : ""; }
function progressTitle(): string { return state.progress < 25 ? "Đang đọc cấu trúc tệp Word…" : state.progress < 75 ? "Đang áp dụng quy tắc…" : state.progress < 90 ? "Đang kiểm tra vị trí cảnh báo…" : "Đang tạo file kết quả…"; }
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
  if (state.tab === "rules") return `<div class="section-copy"><h3>Preset tích hợp</h3><p>Ba preset được version cùng rule engine. Chỉnh sửa preset tổ chức sẽ được mở sau khi có schema migration.</p></div>${Object.values(presets).map(item => `<div class="setting-row"><strong>${item.title}</strong><span>${item.copy}</span></div>`).join("")}`;
  const installed = state.model.state === "ready" || state.model.state === "installed";
  const title = state.model.state === "ready" ? "Model đã sẵn sàng" : state.model.state === "installed" ? "Model đã xác minh · chờ benchmark" : state.model.state === "invalid" ? "Model không hợp lệ" : "Chưa cài model AI";
  return `<div class="model-card"><strong>${title}</strong><p>${installed ? `${escape(state.model.model_id ?? "model")} · ${escape(state.model.version ?? "")}` : "Ứng dụng vẫn chạy đầy đủ bằng tầng luật."}</p></div><p class="notice">Chỉ kết nối mạng sau khi bạn chủ động bấm tải. Nội dung tài liệu không bao giờ được gửi đi.</p><div class="button-row"><button class="button button--secondary" id="model-import">Nhập gói từ máy</button><button class="button button--primary" id="model-action">${installed ? "Xoá model" : "Tải model"}</button></div>`;
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
  document.querySelectorAll<HTMLInputElement>('input[name="preset"]').forEach(input => input.addEventListener("change", () => { state.preset = input.value as Preset; render(); }));
  document.querySelector<HTMLTextAreaElement>("#prompt")?.addEventListener("input", event => { state.prompt = (event.target as HTMLTextAreaElement).value; });
  document.querySelectorAll<HTMLButtonElement>("[data-tab]").forEach(button => button.addEventListener("click", () => { state.tab = button.dataset.tab!; render(); }));
  document.querySelector<HTMLFormElement>("#dictionary-form")?.addEventListener("submit", event => void saveWord(event));
  document.querySelector<HTMLInputElement>("#dictionary-search")?.addEventListener("input", event => void loadDictionary((event.target as HTMLInputElement).value));
  document.querySelectorAll<HTMLButtonElement>("[data-delete]").forEach(button => button.addEventListener("click", () => void deleteWord(button.dataset.delete!)));
  document.querySelector("#import-csv")?.addEventListener("click", () => void importCsv());
  document.querySelector("#export-csv")?.addEventListener("click", () => void api.dictionaryExport());
  document.querySelector("#model-import")?.addEventListener("click", () => void modelImport());
  document.querySelector("#model-action")?.addEventListener("click", () => void modelAction());
}

async function choose(): Promise<void> { try { const selected = await api.chooseDocument(); if (selected) { state.document = selected; state.step = "rules"; state.error = ""; render(); } } catch { state.error = "Không thể mở tệp DOCX này."; render(); } }
function reset(): void { Object.assign(state, { step: "file", document: null, result: null, progress: 0, error: "", prompt: "", jobId: "" }); render(); }
async function start(): Promise<void> {
  if (!state.document) return;
  state.step = "processing"; state.progress = 0; state.jobId = crypto.randomUUID(); state.error = ""; render();
  const unlisten = await api.onProgress(event => { state.progress = event.percent; render(); });
  try { const result = await api.startJob(state.jobId, state.document.path, state.preset, state.prompt); state.result = result; state.step = result.status === "no_findings" ? "no-findings" : "result"; }
  catch { state.step = "rules"; state.error = "Không thể xử lý tệp. File gốc không bị thay đổi."; }
  finally { unlisten(); render(); }
}
async function cancel(): Promise<void> { await api.cancelJob(state.jobId); state.step = "rules"; state.progress = 0; render(); }
async function openSettings(): Promise<void> { state.settings = true; state.model = await api.modelStatus(); await loadDictionary(); }
async function loadDictionary(query = ""): Promise<void> { try { state.dictionary = await api.dictionaryList(query); } catch { state.dictionary = []; } render(); }
async function saveWord(event: SubmitEvent): Promise<void> { event.preventDefault(); const data = new FormData(event.currentTarget as HTMLFormElement); await api.dictionaryUpsert(String(data.get("word")), String(data.get("note"))); await loadDictionary(); }
async function deleteWord(word: string): Promise<void> { await api.dictionaryDelete(word); await loadDictionary(); }
async function importCsv(): Promise<void> { await api.dictionaryImport(); await loadDictionary(); }
async function modelImport(): Promise<void> { const status = await api.modelImport(); if (status) state.model = status; render(); }
async function modelAction(): Promise<void> { state.model = ["ready", "installed"].includes(state.model.state) ? await api.modelRemove() : await api.modelDownload(); render(); }

window.addEventListener("keydown", event => { if (event.ctrlKey && event.key.toLowerCase() === "o") { event.preventDefault(); void choose(); } if (event.key === "Escape" && state.settings) { state.settings = false; render(); } });
window.addEventListener("dragover", event => event.preventDefault());
window.addEventListener("drop", event => { event.preventDefault(); const path = (event.dataTransfer?.files[0] as File & { path?: string })?.path; if (path) void api.inspectDropped(path).then(info => { state.document = info; state.step = "rules"; render(); }); });

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

void api.modelStatus().then(model => { state.model = model; render(); });
render();

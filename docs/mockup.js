(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const templates = {
    standard: "Kiểm tra chính tả tiếng Việt, từ ghép dễ nhầm, viết hoa và lỗi kỹ thuật như khoảng trắng, dấu câu hoặc từ lặp.",
    administrative: "Ưu tiên quy tắc trình bày văn bản hành chính. Kiểm tra viết hoa tên cơ quan, chức danh, địa danh và các từ ghép dễ nhầm.",
    spelling: "Chỉ kiểm tra lỗi chính tả tiếng Việt. Không đề xuất thay đổi cách diễn đạt hoặc văn phong.",
    custom: "",
  };

  const processingStages = [
    { until: 24, title: "Đang đọc cấu trúc tệp Word…", detail: "Kiểm tra đoạn văn, bảng và các thành phần được hỗ trợ." },
    { until: 72, title: "Đang áp dụng quy tắc…", detail: "Đối chiếu chính tả, viết hoa và quy tắc riêng." },
    { until: 90, title: "Đang kiểm tra kết quả…", detail: "Xác minh lại vị trí cảnh báo trước khi xuất." },
    { until: 101, title: "Đang tạo file kết quả…", detail: "File gốc được giữ nguyên." },
  ];

  const state = {
    step: "file",
    fileName: "",
    fileSize: "",
    processingTimer: null,
    progress: 0,
    modelState: "empty",
    modelProgress: 0,
    modelTimer: null,
    snackbarTimer: null,
  };

  const elements = {
    workflow: $("#workflow"),
    fileInput: $("#file-input"),
    modelInput: $("#model-input"),
    dropZone: $("#drop-zone"),
    fileError: $("#file-error"),
    selectedFileName: $("#selected-file-name"),
    selectedFileSize: $("#selected-file-size"),
    promptTemplate: $("#prompt-template"),
    customPrompt: $("#custom-prompt"),
    promptCount: $("#prompt-count"),
    promptHelp: $("#prompt-help"),
    processingTitle: $("#processing-title"),
    processingDetail: $("#processing-detail"),
    processingProgress: $("#processing-progress"),
    progressLabel: $("#progress-label"),
    outputFileName: $("#output-file-name"),
    findingCount: $("#finding-count"),
    settingsDialog: $("#settings-dialog"),
    modelPanel: $("#model-panel"),
    modelTitle: $("#model-title"),
    modelCopy: $("#model-copy"),
    modelProgressWrap: $("#model-progress-wrap"),
    modelProgress: $("#model-progress"),
    modelProgressLabel: $("#model-progress-label"),
    downloadModelButton: $("#download-model-button"),
    importModelButton: $("#import-model-button"),
    modelInlineTitle: $("#model-inline-title"),
    modelInlineCopy: $("#model-inline-copy"),
    snackbar: $("#snackbar"),
  };

  function showStep(step, focus = true) {
    state.step = step;
    const order = ["file", "rules", "processing", "result"];
    const currentIndex = order.indexOf(step);

    $$('[data-step-panel]').forEach((panel) => {
      panel.classList.toggle("is-hidden", panel.dataset.stepPanel !== step);
    });

    $$('[data-step-indicator]').forEach((indicator, index) => {
      const current = index === currentIndex;
      indicator.toggleAttribute("aria-current", current);
      if (current) indicator.setAttribute("aria-current", "step");
      indicator.dataset.complete = String(index < currentIndex);
    });

    if (focus) {
      const heading = $(`#step-${step} h1`);
      window.setTimeout(() => heading?.focus?.({ preventScroll: true }), 0);
    }
  }

  function validDocx(file) {
    return file?.name?.toLocaleLowerCase("vi").endsWith(".docx");
  }

  function formatFileSize(bytes) {
    if (!bytes) return "Dung lượng minh hoạ: 1,8 MB";
    return `${Math.max(bytes / 1024 / 1024, 0.01).toLocaleString("vi-VN", { maximumFractionDigits: 2 })} MB`;
  }

  function selectFile(file, sample = false) {
    if (!validDocx(file)) {
      elements.fileError.textContent = "SoátVăn chỉ hỗ trợ tệp Word có phần mở rộng .docx.";
      elements.fileError.classList.remove("is-hidden");
      return;
    }

    state.fileName = file.name;
    state.fileSize = sample ? "1,8 MB · dữ liệu minh hoạ" : formatFileSize(file.size);
    elements.selectedFileName.textContent = state.fileName;
    elements.selectedFileSize.textContent = state.fileSize;
    elements.outputFileName.textContent = state.fileName.replace(/\.docx$/i, "-soat.docx");
    elements.fileError.classList.add("is-hidden");
    showStep("rules");
  }

  function updatePrompt() {
    elements.promptCount.textContent = `${elements.customPrompt.value.length.toLocaleString("vi-VN")}/1.000`;
    const invalid = !elements.customPrompt.value.trim();
    elements.customPrompt.toggleAttribute("aria-invalid", invalid);
    elements.promptHelp.textContent = invalid
      ? "Hãy chọn một mẫu hoặc nhập ít nhất một quy tắc."
      : "Quy tắc chỉ áp dụng cho lần xử lý này.";
    elements.promptHelp.classList.toggle("field-message--error", invalid);
  }

  function applyTemplate() {
    elements.customPrompt.value = templates[elements.promptTemplate.value];
    updatePrompt();
    if (elements.promptTemplate.value === "custom") elements.customPrompt.focus({ preventScroll: true });
  }

  function renderProcessing() {
    const stage = processingStages.find((candidate) => state.progress < candidate.until) ?? processingStages.at(-1);
    elements.processingTitle.textContent = stage.title;
    elements.processingDetail.textContent = stage.detail;
    elements.processingProgress.style.setProperty("--progress-ratio", String(state.progress / 100));
    elements.processingProgress.setAttribute("aria-valuenow", String(state.progress));
    elements.progressLabel.textContent = `${state.progress}%`;
  }

  function startProcessing() {
    if (!elements.customPrompt.value.trim()) {
      updatePrompt();
      elements.customPrompt.focus({ preventScroll: true });
      return;
    }

    window.clearInterval(state.processingTimer);
    state.progress = 0;
    renderProcessing();
    showStep("processing");

    state.processingTimer = window.setInterval(() => {
      state.progress = Math.min(100, state.progress + 4);
      renderProcessing();
      if (state.progress >= 100) {
        window.clearInterval(state.processingTimer);
        elements.findingCount.textContent = state.modelState === "ready" ? "7" : "5";
        window.setTimeout(() => showStep("result"), 250);
      }
    }, 120);
  }

  function cancelProcessing() {
    window.clearInterval(state.processingTimer);
    state.progress = 0;
    showStep("rules");
    showSnackbar("Đã dừng xử lý. Tệp vẫn đang được chọn.");
  }

  function resetWorkflow() {
    window.clearInterval(state.processingTimer);
    state.fileName = "";
    state.fileSize = "";
    state.progress = 0;
    elements.fileInput.value = "";
    showStep("file");
  }

  function showSnackbar(message) {
    window.clearTimeout(state.snackbarTimer);
    elements.snackbar.textContent = message;
    elements.snackbar.classList.remove("is-hidden");
    state.snackbarTimer = window.setTimeout(() => elements.snackbar.classList.add("is-hidden"), 3000);
  }

  function renderModel() {
    const ready = state.modelState === "ready";
    const busy = state.modelState === "loading";
    elements.modelPanel.dataset.state = state.modelState;
    elements.modelProgressWrap.classList.toggle("is-hidden", !busy);
    elements.modelProgress.style.setProperty("--progress-ratio", String(state.modelProgress / 100));
    elements.modelProgress.setAttribute("aria-valuenow", String(state.modelProgress));
    elements.modelProgressLabel.textContent = `${state.modelProgress}%`;
    elements.importModelButton.disabled = busy;

    if (busy) {
      elements.modelTitle.textContent = "Đang tải và xác minh model…";
      elements.modelCopy.textContent = "Bạn có thể đóng cửa sổ này; tiến trình vẫn tiếp tục.";
      elements.downloadModelButton.textContent = "Huỷ tải";
    } else if (ready) {
      elements.modelTitle.textContent = "Model tiếng Việt đã sẵn sàng";
      elements.modelCopy.textContent = "Đã xác minh và lưu trên máy.";
      elements.downloadModelButton.textContent = "Xoá model";
    } else {
      elements.modelTitle.textContent = "Chưa cài model AI";
      elements.modelCopy.textContent = "Ứng dụng vẫn chạy bằng tầng luật.";
      elements.downloadModelButton.textContent = "Tải model về máy";
    }

    elements.modelInlineTitle.textContent = ready ? "Model AI đã sẵn sàng" : "Đang dùng tầng luật";
    elements.modelInlineCopy.textContent = ready
      ? "Quy tắc riêng sẽ được dùng trong bước phân loại ngữ cảnh."
      : "Model AI chưa cài; quy tắc cơ bản vẫn hoạt động.";
  }

  function startModelInstall(importing = false) {
    window.clearInterval(state.modelTimer);
    state.modelState = "loading";
    state.modelProgress = importing ? 48 : 0;
    renderModel();

    state.modelTimer = window.setInterval(() => {
      state.modelProgress = Math.min(100, state.modelProgress + 5);
      if (state.modelProgress >= 100) {
        window.clearInterval(state.modelTimer);
        state.modelState = "ready";
        renderModel();
        return;
      }
      renderModel();
    }, 120);
  }

  function handleModelAction() {
    if (state.modelState === "loading") {
      window.clearInterval(state.modelTimer);
      state.modelState = "empty";
      state.modelProgress = 0;
    } else if (state.modelState === "ready") {
      state.modelState = "empty";
      state.modelProgress = 0;
    } else {
      startModelInstall(false);
      return;
    }
    renderModel();
  }

  function bindEvents() {
    $("#choose-file-button").addEventListener("click", () => elements.fileInput.click());
    $("#sample-file-button").addEventListener("click", () => selectFile({ name: "Bao-cao-tong-ket-2026.docx", size: 0 }, true));
    elements.fileInput.addEventListener("change", () => {
      selectFile(elements.fileInput.files?.[0]);
      elements.fileInput.value = "";
    });

    ["dragenter", "dragover"].forEach((eventName) => elements.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      elements.dropZone.dataset.dragging = "true";
    }));
    ["dragleave", "drop"].forEach((eventName) => elements.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      elements.dropZone.dataset.dragging = "false";
    }));
    elements.dropZone.addEventListener("drop", (event) => selectFile(event.dataTransfer?.files?.[0]));

    $("#back-to-file").addEventListener("click", resetWorkflow);
    elements.promptTemplate.addEventListener("change", applyTemplate);
    elements.customPrompt.addEventListener("input", () => {
      if (elements.customPrompt.value !== templates[elements.promptTemplate.value]) elements.promptTemplate.value = "custom";
      updatePrompt();
    });
    $("#start-button").addEventListener("click", startProcessing);
    $("#cancel-button").addEventListener("click", cancelProcessing);
    $("#restart-button").addEventListener("click", resetWorkflow);
    $("#open-output-button").addEventListener("click", () => showSnackbar("Bản thật sẽ mở file kết quả bằng Microsoft Word."));
    $("#open-folder-button").addEventListener("click", () => showSnackbar("Bản thật sẽ mở thư mục chứa file kết quả."));

    $("#settings-button").addEventListener("click", () => elements.settingsDialog.showModal());
    elements.settingsDialog.addEventListener("click", (event) => {
      if (event.target === elements.settingsDialog) elements.settingsDialog.close("cancel");
    });
    elements.downloadModelButton.addEventListener("click", handleModelAction);
    elements.importModelButton.addEventListener("click", () => elements.modelInput.click());
    elements.modelInput.addEventListener("change", () => {
      if (elements.modelInput.files?.[0]) startModelInstall(true);
      elements.modelInput.value = "";
    });

    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase("vi") === "o" && !elements.settingsDialog.open) {
        event.preventDefault();
        elements.fileInput.click();
      }
    });
  }

  function initialize() {
    bindEvents();
    elements.customPrompt.value = templates.standard;
    updatePrompt();
    renderModel();

    const previewStep = new URLSearchParams(window.location.search).get("step");
    if (["rules", "processing", "result"].includes(previewStep)) {
      selectFile({ name: "Bao-cao-tong-ket-2026.docx", size: 0 }, true);
      if (previewStep === "processing") startProcessing();
      if (previewStep === "result") showStep("result", false);
    } else {
      showStep("file", false);
    }
  }

  initialize();
})();

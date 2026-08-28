import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CustomRule, DocumentInfo, JobResult, ModelStatus, ProgressEvent } from "../src/contracts";

const documentInfo: DocumentInfo = {
  path: "C:\\Tài liệu\\nguồn.docx",
  name: "nguồn.docx",
  size: 1024,
  paragraph_count: 4,
  table_cell_count: 1,
  character_count: 120,
  word_count: 24,
  page_count: 2,
};

const signedReadyModel: ModelStatus = {
  state: "ready",
  model_id: "gemma-4-e4b",
  version: "1.0.0",
  trust: "release_signed",
  release_approved: true,
  capabilities: { candidate_filter: true, full_review: true },
};

function customRule(id: string, prompt: string, createdAt = "2026-08-25T01:00:00.000000Z"): CustomRule {
  return { id, prompt, created_at: createdAt, updated_at: createdAt };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

async function loadApp(options?: {
  choose?: () => Promise<DocumentInfo | null>;
  inspectDropped?: (path: string) => Promise<DocumentInfo>;
  start?: () => Promise<JobResult>;
  cancel?: () => Promise<boolean>;
  progressListener?: (handler: (event: ProgressEvent) => void) => Promise<() => void>;
  openOutput?: (path: string, reveal: boolean) => Promise<void>;
  customRuleList?: () => Promise<CustomRule[]>;
  customRuleUpsert?: (id: string | null, prompt: string) => Promise<CustomRule>;
  customRuleDelete?: (id: string) => Promise<boolean>;
  modelStatus?: () => Promise<ModelStatus>;
  modelImport?: () => Promise<ModelStatus | null>;
  modelCancel?: () => Promise<boolean>;
}) {
  let progressHandler: ((event: ProgressEvent) => void) | undefined;
  let fileDropHandler: ((path: string) => void) | undefined;
  let modelProgressHandler: ((event: { received: number; total: number; percent: number }) => void) | undefined;
  const api = {
    chooseDocument: vi.fn(options?.choose ?? (() => Promise.resolve(documentInfo))),
    inspectDropped: vi.fn(options?.inspectDropped ?? (() => Promise.resolve(documentInfo))),
    startJob: vi.fn(
      options?.start ??
        (() =>
          Promise.resolve({
            job_id: "job",
            status: "completed" as const,
            output_path: "C:\\Tài liệu\\nguồn-soat.docx",
            finding_count: 2,
            counts: { category: { spelling: 2 }, origin: { rule: 2 } },
          })),
    ),
    cancelJob: vi.fn(options?.cancel ?? (() => Promise.resolve(true))),
    onProgress: vi.fn(async (handler: (event: ProgressEvent) => void) => {
      progressHandler = handler;
      if (options?.progressListener) return options.progressListener(handler);
      return () => undefined;
    }),
    onFileDrop: vi.fn(async (handler: (path: string) => void) => {
      fileDropHandler = handler;
      return () => undefined;
    }),
    appVersion: vi.fn(() => Promise.resolve("0.1.5")),
    openOutput: vi.fn(options?.openOutput ?? (() => Promise.resolve())),
    customRuleList: vi.fn(options?.customRuleList ?? (() => Promise.resolve([]))),
    customRuleUpsert: vi.fn(options?.customRuleUpsert ?? ((id: string | null, prompt: string) => Promise.resolve(customRule(id ?? "rule-created", prompt)))),
    customRuleDelete: vi.fn(options?.customRuleDelete ?? (() => Promise.resolve(true))),
    modelStatus: vi.fn(options?.modelStatus ?? (() => Promise.resolve({ state: "not_installed" as const }))),
    modelDeactivate: vi.fn(() => Promise.resolve({ state: "installed" as const, model_id: "gemma-4-e4b", version: "1.0.0", trust: "release_signed" as const, release_approved: true, capabilities: { candidate_filter: true, full_review: true } })),
    modelImport: vi.fn(options?.modelImport ?? (() => Promise.resolve(null))),
    modelDownload: vi.fn(() => Promise.resolve(signedReadyModel)),
    modelCancel: vi.fn(options?.modelCancel ?? (() => Promise.resolve(true))),
    modelRemove: vi.fn(() => Promise.resolve({ state: "not_installed" as const })),
    onModelProgress: vi.fn(async (handler: (event: { received: number; total: number; percent: number }) => void) => {
      modelProgressHandler = handler;
      return () => undefined;
    }),
  };
  vi.doMock("../src/api", () => ({ api }));
  await import("../src/main");
  await vi.waitFor(() => expect(document.querySelector("#choose")).not.toBeNull());
  return Object.assign(api, {
    emitProgress(jobId: string, percent = 35, stage = "rules") {
      progressHandler?.({
        job_id: jobId,
        stage,
        percent,
        message_code: "job.applying_rules",
      });
    },
    emitFileDrop(path: string) {
      fileDropHandler?.(path);
    },
    emitModelProgress(received: number, total: number, percent = 0) {
      modelProgressHandler?.({ received, total, percent });
    },
  });
}

async function chooseDocument(): Promise<void> {
  document.querySelector<HTMLButtonElement>("#choose")!.click();
  await vi.waitFor(() => expect(document.querySelector("#start")).not.toBeNull());
}

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  document.body.innerHTML = '<div id="app"></div>';
});

describe("four-step desktop workflow", () => {
  it("moves from file selection to the fixed preparation screen", async () => {
    await loadApp();
    const progressStrip = document.querySelector<HTMLElement>('nav.progress-strip[aria-label="Tiến trình kiểm tra"]');
    expect(progressStrip?.nextElementSibling?.matches("main.workflow-shell")).toBe(true);
    expect(document.querySelectorAll(".stepper li")).toHaveLength(4);
    expect(document.querySelector('.stepper li[aria-current="step"]')?.textContent).toContain("Chọn tệp");
    expect(document.querySelector(".step-label")).toBeNull();
    await chooseDocument();
    expect(document.querySelector('.stepper li[aria-current="step"]')?.textContent).toContain("Chuẩn bị");
    expect(
      [...document.querySelectorAll<HTMLElement>(".stepper > li")].map(item => ({
        current: item.getAttribute("aria-current"),
        complete: item.dataset.complete,
      })),
    ).toEqual([
      { current: null, complete: "true" },
      { current: "step", complete: "false" },
      { current: null, complete: "false" },
      { current: null, complete: "false" },
    ]);
    expect(document.querySelector('input[name="preset"]')).toBeNull();
    expect(document.querySelector("#ignored-words")).toBeNull();
    expect(document.querySelector("#prompt")).toBeNull();
    expect(document.querySelector("#full-review")).toBeNull();
    expect(document.body.textContent).toContain("nguồn.docx");
    expect(document.body.textContent).toContain("2 trang");
    expect(document.body.textContent).toContain("24 từ");
    expect(document.body.textContent).toContain("0 quy tắc riêng");
  });

  it("shows progress then only an output path, without document preview", async () => {
    const job = deferred<JobResult>();
    const api = await loadApp({ start: () => job.promise });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.querySelector("#cancel")).not.toBeNull());
    expect(document.querySelector<HTMLButtonElement>("#settings")!.disabled).toBe(true);
    api.emitProgress(api.startJob.mock.calls[0][0]);
    expect(document.body.textContent).toContain("35%");
    const progress = document.querySelector<HTMLElement>('[role="progressbar"]')!;
    expect(progress.getAttribute("aria-valuenow")).toBe("35");
    expect(progress.getAttribute("aria-valuemin")).toBe("0");
    expect(progress.getAttribute("aria-valuemax")).toBe("100");
    expect(api.startJob.mock.calls[0][2]).toBe("standard");
    expect(api.startJob.mock.calls[0][3]).toBe("");
    expect(api.startJob.mock.calls[0][4]).toBe(false);
    expect(api.startJob.mock.calls[0][7]).toBe(false);
    expect(api.startJob.mock.calls[0][8]).toBe(false);
    expect(api.startJob.mock.calls[0][5]).toEqual({
      technical: true,
      repeated_words: true,
      confusions: true,
      syllables: true,
      administrative_capitalization: false,
    });
    expect(api.startJob.mock.calls[0][6]).toEqual([]);
    job.resolve({
      job_id: "job",
      status: "completed",
      output_path: "C:\\Tài liệu\\nguồn-soat.docx",
      finding_count: 3,
      counts: { category: { spelling: 3 }, origin: { rule: 3 } },
    });
    await vi.waitFor(() => expect(document.querySelector("#open")).not.toBeNull());
    expect(document.body.textContent).toContain("nguồn-soat.docx");
    expect(document.querySelector("iframe, object, embed")).toBeNull();
    document.querySelector<HTMLButtonElement>("#open")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#open")!.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>("#reveal")!.click();
    await vi.waitFor(() => expect(api.openOutput).toHaveBeenCalledTimes(2));
    expect(api.openOutput).toHaveBeenNthCalledWith(1, "C:\\Tài liệu\\nguồn-soat.docx", false);
    expect(api.openOutput).toHaveBeenNthCalledWith(
      2,
      "C:\\Tài liệu\\nguồn-soat.docx",
      true,
    );
  });

  it("maps backend stages, normalizes monotonic progress, and preserves cancel focus", async () => {
    const job = deferred<JobResult>();
    const unlisten = vi.fn();
    const api = await loadApp({ start: () => job.promise, progressListener: () => Promise.resolve(unlisten) });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    const jobId = api.startJob.mock.calls[0][0];
    const cancel = document.querySelector<HTMLButtonElement>("#cancel")!;
    cancel.focus();

    api.emitProgress("another-job", 80, "exporting");
    expect(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("0");
    api.emitProgress(jobId, -10, "reading");
    expect(document.body.textContent).toContain("Đang đọc cấu trúc tệp Word");
    api.emitProgress(jobId, 72, "validating");
    expect(document.body.textContent).toContain("Đang kiểm tra vị trí cảnh báo");
    api.emitProgress(jobId, 88, "exporting");
    expect(document.body.textContent).toContain("Đang tạo tệp kết quả");
    api.emitProgress(jobId, 99, "rules");
    expect(document.body.textContent).toContain("Đang tạo tệp kết quả");
    api.emitProgress(jobId, 140, "complete");
    expect(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("100");
    api.emitProgress(jobId, Number.NaN, "complete");
    expect(document.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("100");
    expect(document.activeElement).toBe(cancel);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "o", ctrlKey: true }));
    api.emitFileDrop("C:\\Tài liệu\\khác.docx");
    expect(api.chooseDocument).toHaveBeenCalledOnce();
    expect(api.inspectDropped).not.toHaveBeenCalled();

    job.resolve({ job_id: jobId, status: "no_findings", output_path: null, finding_count: 0, counts: {} });
    await vi.waitFor(() => expect(document.querySelector("#restart")).not.toBeNull());
    expect(unlisten).toHaveBeenCalledOnce();
  });

  it("recovers when the progress listener cannot be registered", async () => {
    const api = await loadApp({
      progressListener: () => Promise.reject(new Error("listener unavailable")),
    });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.body.textContent).toContain("Không thể theo dõi tiến độ xử lý"));
    expect(document.querySelector("#start")).not.toBeNull();
    expect(document.querySelector("#cancel")).toBeNull();
    expect(api.startJob).not.toHaveBeenCalled();
  });

  it("shows an actionable error when custom rules exceed model context", async () => {
    await loadApp({
      start: () => Promise.reject("CUSTOM_PROMPT_CONTEXT_EXCEEDED"),
    });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();

    await vi.waitFor(() =>
      expect(document.body.textContent).toContain(
        "Quy tắc riêng quá dài so với dung lượng ngữ cảnh đang dùng",
      ),
    );
    expect(document.body.textContent).toContain("Tệp gốc chưa bị thay đổi");
    expect(document.querySelector("#start")).not.toBeNull();
  });

  it("keeps the generic processing message for unknown backend errors", async () => {
    await loadApp({ start: () => Promise.reject(new Error("UNKNOWN_ENGINE_ERROR")) });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();

    await vi.waitFor(() =>
      expect(document.body.textContent).toContain("Không xử lý được tệp"),
    );
    expect(document.body.textContent).not.toContain("UNKNOWN_ENGINE_ERROR");
  });

  it("keeps running and re-enables cancel when the backend declines cancellation", async () => {
    const job = deferred<JobResult>();
    const cancelAttempt = deferred<boolean>();
    const api = await loadApp({ start: () => job.promise, cancel: () => cancelAttempt.promise });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    const cancel = document.querySelector<HTMLButtonElement>("#cancel")!;
    cancel.click();
    expect(cancel.disabled).toBe(true);
    expect(cancel.textContent).toContain("Đang dừng");
    cancel.click();
    expect(api.cancelJob).toHaveBeenCalledOnce();
    cancelAttempt.resolve(false);
    await vi.waitFor(() => expect(document.body.textContent).toContain("tiếp tục chờ kết quả"));
    expect(document.querySelector<HTMLButtonElement>("#cancel")!.disabled).toBe(false);
    expect(document.querySelector("#start")).toBeNull();

    job.resolve({ job_id: "job", status: "no_findings", output_path: null, finding_count: 0, counts: {} });
    await vi.waitFor(() => expect(document.querySelector("#restart")).not.toBeNull());
    expect(document.querySelector(".error")).toBeNull();
  });

  it("keeps running and offers retry when cancellation fails", async () => {
    const job = deferred<JobResult>();
    const api = await loadApp({ start: () => job.promise, cancel: () => Promise.reject(new Error("cancel failed")) });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    document.querySelector<HTMLButtonElement>("#cancel")!.click();
    await vi.waitFor(() => expect(document.body.textContent).toContain("Tác vụ vẫn đang chạy"));
    expect(document.querySelector<HTMLButtonElement>("#cancel")!.disabled).toBe(false);

    job.resolve({ job_id: "job", status: "no_findings", output_path: null, finding_count: 0, counts: {} });
    await vi.waitFor(() => expect(document.querySelector("#restart")).not.toBeNull());
  });

  it("keeps a completed result when a cancellation response arrives late", async () => {
    const job = deferred<JobResult>();
    const cancelAttempt = deferred<boolean>();
    const api = await loadApp({ start: () => job.promise, cancel: () => cancelAttempt.promise });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    document.querySelector<HTMLButtonElement>("#cancel")!.click();
    job.resolve({
      job_id: "job",
      status: "completed",
      output_path: "C:\\Tài liệu\\nguồn-soat.docx",
      finding_count: 1,
      counts: { category: { spelling: 1 }, origin: { rule: 1 } },
    });
    await vi.waitFor(() => expect(document.querySelector("#open")).not.toBeNull());
    cancelAttempt.resolve(true);
    await Promise.resolve();
    expect(document.querySelector("#open")).not.toBeNull();
    expect(document.querySelector("#start")).toBeNull();
  });

  it("guards output actions and clears an old error after a successful retry", async () => {
    const api = await loadApp();
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.querySelector("#open")).not.toBeNull());
    api.openOutput.mockRejectedValueOnce(new Error("missing"));
    document.querySelector<HTMLButtonElement>("#open")!.click();
    await vi.waitFor(() => expect(document.body.textContent).toContain("Không mở được tệp kết quả"));

    const retry = deferred<void>();
    api.openOutput.mockReturnValueOnce(retry.promise);
    const open = document.querySelector<HTMLButtonElement>("#open")!;
    open.click();
    expect(document.querySelector(".error")).toBeNull();
    expect(open.disabled).toBe(true);
    expect(document.querySelector<HTMLButtonElement>("#reveal")!.disabled).toBe(true);
    open.click();
    expect(api.openOutput).toHaveBeenCalledTimes(2);
    retry.resolve();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#open")!.disabled).toBe(false));
    expect(document.querySelector(".error")).toBeNull();
    expect(document.activeElement?.id).toBe("open");
  });

  it("ignores a late output error after leaving the result screen", async () => {
    const opening = deferred<void>();
    await loadApp({ openOutput: () => opening.promise });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.querySelector("#reveal")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("#reveal")!.click();
    expect(document.querySelector<HTMLButtonElement>("#reveal")!.disabled).toBe(true);
    document.querySelector<HTMLButtonElement>("#restart")!.click();
    opening.reject(new Error("late opener error"));
    await Promise.resolve();
    expect(document.querySelector("#choose")).not.toBeNull();
    expect(document.querySelector("[role=alert]")).toBeNull();
  });

  it("shows no-findings state without creating an output affordance", async () => {
    await loadApp({
      start: () =>
        Promise.resolve({
          job_id: "job",
          status: "no_findings",
          output_path: null,
          finding_count: 0,
          counts: {},
        }),
    });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() =>
      expect(document.body.textContent).toContain("Không phát hiện cảnh báo"),
    );
    expect(document.querySelector("#open")).toBeNull();
  });

  it("warns about partial review with no findings instead of claiming no warnings", async () => {
    await loadApp({
      start: () => Promise.resolve({
        job_id: "job",
        status: "partial",
        output_path: null,
        finding_count: 0,
        counts: {},
        review: {
          status: "partial",
          total_chunks: 3,
          reviewed_chunks: 2,
          failed_chunks: 1,
          total_blocks: 12,
          reviewed_blocks: 8,
          failed_blocks: 4,
          timeout_chunks: 1,
          invalid_output_chunks: 0,
          inference_error_chunks: 0,
          retried_chunks: 1,
          recovered_chunks: 0,
        },
      }),
    });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.body.textContent).toContain("Rà soát chỉ hoàn tất một phần"));
    expect(document.body.textContent).not.toContain("Không phát hiện cảnh báo");
    expect(document.querySelector<HTMLElement>(".review-warning")?.textContent).toContain("2/3");
    expect(document.querySelector<HTMLElement>(".review-warning")?.textContent).toContain("1 quá thời gian");
    expect(document.querySelector<HTMLElement>(".review-warning")?.textContent).toContain("Đã tự chia nhỏ và thử lại 1 phần");
    expect(document.querySelector<HTMLElement>(".review-warning")?.textContent).toContain("Không thể kết luận toàn bộ tài liệu không có lỗi");
    expect(document.querySelector("#open")).toBeNull();
  });

  it("keeps partial coverage visible when a result file was created", async () => {
    await loadApp({
      start: () => Promise.resolve({
        job_id: "job",
        status: "partial",
        output_path: "C:\\Tài liệu\\nguồn-soat.docx",
        finding_count: 2,
        counts: { category: { spelling: 2 }, origin: { llm: 2 } },
        review: {
          status: "partial",
          total_chunks: 5,
          reviewed_chunks: 4,
          failed_chunks: 1,
          total_blocks: 20,
          reviewed_blocks: 16,
          failed_blocks: 4,
          timeout_chunks: 0,
          invalid_output_chunks: 1,
          inference_error_chunks: 0,
          retried_chunks: 1,
          recovered_chunks: 0,
        },
      }),
    });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.querySelector("#open")).not.toBeNull());
    expect(document.body.textContent).toContain("Đã tạo tệp kết quả khi AI rà chưa hết");
    expect(document.body.textContent).toContain("Tệp gồm các cảnh báo AI đã ghi nhận");
    expect(document.body.textContent).toContain("1 trả kết quả không hợp lệ");
    expect(document.querySelector<HTMLElement>(".review-warning")?.textContent).toContain("4/5");
  });

  it("keeps invalid files on step one with a safe error", async () => {
    await loadApp({ choose: () => Promise.reject(new Error("invalid")) });
    document.querySelector<HTMLButtonElement>("#choose")!.click();
    await vi.waitFor(() => expect(document.querySelector('[role="alert"]')).not.toBeNull());
    expect(document.body.textContent).toContain("Không mở được tệp DOCX");
    expect(document.querySelector("#choose")).not.toBeNull();
  });

  it("supports Ctrl+O and Tauri file drop", async () => {
    const api = await loadApp();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "o", ctrlKey: true }));
    await vi.waitFor(() => expect(api.chooseDocument).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(document.querySelector("#start")).not.toBeNull());

    document.querySelector<HTMLButtonElement>("#back")!.click();
    api.emitFileDrop("C:\\Tài liệu\\thả.docx");
    await vi.waitFor(() => expect(api.inspectDropped).toHaveBeenCalledWith("C:\\Tài liệu\\thả.docx"));
    await vi.waitFor(() => expect(document.querySelector("#start")).not.toBeNull());
  });

  it("ignores an old file-picker result after a newer file drop", async () => {
    const picked = deferred<DocumentInfo | null>();
    const api = await loadApp({ choose: () => picked.promise });
    document.querySelector<HTMLButtonElement>("#choose")!.click();
    await vi.waitFor(() => expect(api.chooseDocument).toHaveBeenCalledOnce());

    api.emitFileDrop("C:\\Tài liệu\\thả-mới.docx");
    await vi.waitFor(() => expect(document.querySelector("#start")).not.toBeNull());
    expect(document.body.textContent).toContain("nguồn.docx");

    picked.resolve({ ...documentInfo, path: "C:\\Tài liệu\\chọn-cũ.docx", name: "chọn-cũ.docx" });
    await Promise.resolve();
    expect(document.body.textContent).toContain("nguồn.docx");
    expect(document.body.textContent).not.toContain("chọn-cũ.docx");
  });

  it("does not replace an active job when an earlier file-drop inspection finishes late", async () => {
    const inspected = deferred<DocumentInfo>();
    const job = deferred<JobResult>();
    const api = await loadApp({ inspectDropped: () => inspected.promise, start: () => job.promise });
    await chooseDocument();
    api.emitFileDrop("C:\\Tài liệu\\mới.docx");
    await vi.waitFor(() => expect(api.inspectDropped).toHaveBeenCalledOnce());
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());

    inspected.resolve({ ...documentInfo, path: "C:\\Tài liệu\\mới.docx", name: "mới.docx" });
    await Promise.resolve();
    expect(document.querySelector("#cancel")).not.toBeNull();
    expect(document.querySelector("#start")).toBeNull();

    job.resolve({ job_id: "job", status: "no_findings", output_path: null, finding_count: 0, counts: {} });
    await vi.waitFor(() => expect(document.querySelector("#restart")).not.toBeNull());
  });

  it("does not replace a completed result when file-drop inspection returns after the job", async () => {
    const inspected = deferred<DocumentInfo>();
    const job = deferred<JobResult>();
    const api = await loadApp({ inspectDropped: () => inspected.promise, start: () => job.promise });
    await chooseDocument();
    api.emitFileDrop("C:\\Tài liệu\\muộn.docx");
    await vi.waitFor(() => expect(api.inspectDropped).toHaveBeenCalledOnce());
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalledOnce());
    job.resolve({
      job_id: "job",
      status: "completed",
      output_path: "C:\\Tài liệu\\nguồn-soat.docx",
      finding_count: 1,
      counts: { category: { spelling: 1 }, origin: { rule: 1 } },
    });
    await vi.waitFor(() => expect(document.querySelector("#open")).not.toBeNull());

    inspected.resolve({ ...documentInfo, path: "C:\\Tài liệu\\muộn.docx", name: "muộn.docx" });
    await Promise.resolve();
    expect(document.querySelector("#open")).not.toBeNull();
    expect(document.querySelector("#start")).toBeNull();
  });

  it("applies only the latest of concurrent file-drop inspections", async () => {
    const first = deferred<DocumentInfo>();
    const second = deferred<DocumentInfo>();
    const api = await loadApp({
      inspectDropped: path => path.endsWith("hai.docx") ? second.promise : first.promise,
    });
    await chooseDocument();
    api.emitFileDrop("C:\\Tài liệu\\một.docx");
    api.emitFileDrop("C:\\Tài liệu\\hai.docx");
    await vi.waitFor(() => expect(api.inspectDropped).toHaveBeenCalledTimes(2));

    second.resolve({ ...documentInfo, path: "C:\\Tài liệu\\hai.docx", name: "hai.docx" });
    await vi.waitFor(() => expect(document.body.textContent).toContain("hai.docx"));
    first.resolve({ ...documentInfo, path: "C:\\Tài liệu\\một.docx", name: "một.docx" });
    await Promise.resolve();
    expect(document.body.textContent).toContain("hai.docx");
    expect(document.body.textContent).not.toContain("một.docx");
  });

  it("ignores a late result after cancellation", async () => {
    const job = deferred<JobResult>();
    const api = await loadApp({ start: () => job.promise });
    await chooseDocument();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.querySelector("#cancel")).not.toBeNull());
    document.querySelector<HTMLButtonElement>("#cancel")!.click();
    await vi.waitFor(() => expect(api.cancelJob).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(document.querySelector("#start")).not.toBeNull());
    job.resolve({
      job_id: "late",
      status: "completed",
      output_path: "C:\\late.docx",
      finding_count: 1,
      counts: { category: { spelling: 1 }, origin: { rule: 1 } },
    });
    await Promise.resolve();
    expect(document.querySelector("#open")).toBeNull();
    expect(document.querySelector("#start")).not.toBeNull();
  });

  it("provides master-detail prompt create, edit, delete, undo, and aggregate-limit feedback", async () => {
    const firstCreatedAt = "2026-08-25T01:00:00.000000Z";
    const first = customRule("rule-1", "Giữ nguyên tên SoátVăn.", firstCreatedAt);
    const api = await loadApp({
      customRuleList: () => Promise.resolve([first]),
      customRuleUpsert: (id, prompt) => Promise.resolve({
        ...customRule(id ?? "rule-2", prompt, id === "rule-1" ? firstCreatedAt : "2026-08-25T02:00:00.000000Z"),
        updated_at: "2026-08-25T03:00:00.000000Z",
      }),
    });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector("#custom-rule-form")).not.toBeNull());
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#new-custom-rule")?.disabled).toBe(false));
    const master = document.querySelector<HTMLElement>('nav.prompt-list[aria-label="Danh sách prompt"]')!;
    expect(master).not.toBeNull();
    expect(master.querySelectorAll("[data-prompt-id]")).toHaveLength(1);
    const firstRow = master.querySelector<HTMLButtonElement>('[data-prompt-id="rule-1"][data-edit-rule="rule-1"]')!;
    expect(firstRow.textContent).toContain("Giữ nguyên tên SoátVăn.");
    expect(firstRow.getAttribute("aria-label")).toBe("Sửa prompt 1");
    expect(document.querySelector('.prompt-manager__detail[aria-labelledby="custom-rule-editor-title"]')).not.toBeNull();
    expect(document.querySelector('input[name="title"], #custom-rule-title')).toBeNull();
    expect(document.querySelector("#custom-rule-help")?.textContent).toContain("4.000 ký tự");

    firstRow.click();
    await vi.waitFor(() => expect(document.querySelector('[data-prompt-id="rule-1"]')?.getAttribute("aria-current")).toBe("true"));
    let prompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
    expect(prompt.value).toBe("Giữ nguyên tên SoátVăn.");
    expect(prompt.maxLength).toBe(4000);
    expect(document.querySelector("#custom-rule-count")?.textContent).toContain("/4.000 ký tự");

    document.querySelector<HTMLButtonElement>("#new-custom-rule")!.click();
    prompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
    expect(prompt.value).toBe("");
    expect(document.querySelector('[data-prompt-id][aria-current="true"]')).toBeNull();

    prompt.value = "Dùng thuật ngữ “khách hàng”.";
    prompt.dispatchEvent(new InputEvent("input", { bubbles: true }));
    const externalSubmit = document.querySelector<HTMLButtonElement>('button[type="submit"][form="custom-rule-form"]')!;
    expect(externalSubmit.form?.id).toBe("custom-rule-form");
    externalSubmit.click();
    await vi.waitFor(() =>
      expect(api.customRuleUpsert).toHaveBeenCalledWith(null, "Dùng thuật ngữ “khách hàng”."),
    );
    await vi.waitFor(() => expect(document.body.textContent).toContain("Dùng thuật ngữ “khách hàng”."));

    document.querySelector<HTMLButtonElement>('[data-prompt-id="rule-1"][data-edit-rule="rule-1"]')!.click();
    prompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
    expect(prompt.value).toBe("Giữ nguyên tên SoátVăn.");
    expect(document.querySelector('[data-prompt-id="rule-1"]')?.getAttribute("aria-current")).toBe("true");
    prompt.value = "Giữ nguyên tên riêng SoátVăn.";
    prompt.dispatchEvent(new InputEvent("input", { bubbles: true }));
    document.querySelector<HTMLFormElement>("#custom-rule-form")!.requestSubmit();
    await vi.waitFor(() => expect(api.customRuleUpsert).toHaveBeenCalledWith("rule-1", "Giữ nguyên tên riêng SoátVăn."));

    document.querySelector<HTMLButtonElement>('[data-delete-rule="rule-1"]')!.click();
    await vi.waitFor(() => expect(api.customRuleDelete).toHaveBeenCalledWith("rule-1"));
    expect(document.querySelector("#undo-delete-rule")).not.toBeNull();
    document.querySelector<HTMLButtonElement>("#undo-delete-rule")!.click();
    await vi.waitFor(() => expect(api.customRuleUpsert).toHaveBeenCalledWith("rule-1", "Giữ nguyên tên riêng SoátVăn."));
  });

  it("guards every prompt draft exit and supports cancelling or confirming discard", async () => {
    await loadApp({
      customRuleList: () => Promise.resolve([
        customRule("rule-1", "Prompt one."),
        customRule("rule-2", "Prompt two.", "2026-08-25T02:00:00.000000Z"),
      ]),
    });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-prompt-id="rule-2"]')).not.toBeNull());
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));

    const changeDraft = (value: string) => {
      const textarea = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
      textarea.value = value;
      textarea.dispatchEvent(new InputEvent("input", { bubbles: true }));
    };
    const expectDiscardPrompt = async () => {
      await vi.waitFor(() => expect(document.querySelector("#prompt-discard-confirmation")).not.toBeNull());
      await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("cancel-prompt-discard"));
    };

    changeDraft("Unsaved new prompt.");
    document.querySelector<HTMLButtonElement>("#settings-back")!.click();
    await expectDiscardPrompt();
    expect(document.querySelector("#settings-page")).not.toBeNull();
    document.querySelector<HTMLButtonElement>("#cancel-prompt-discard")!.click();
    await vi.waitFor(() => expect(document.querySelector("#prompt-discard-confirmation")).toBeNull());
    expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!.value).toBe("Unsaved new prompt.");

    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();
    await expectDiscardPrompt();
    document.querySelector<HTMLButtonElement>("#cancel-prompt-discard")!.click();
    expect(document.querySelector('[data-settings-section="prompts"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!.value).toBe("Unsaved new prompt.");
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();
    await expectDiscardPrompt();
    document.querySelector<HTMLButtonElement>("#confirm-prompt-discard")!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-models-title"));

    document.querySelector<HTMLButtonElement>('[data-settings-section="prompts"]')!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-prompts-title"));
    document.querySelector<HTMLButtonElement>('[data-prompt-id="rule-1"]')!.click();
    changeDraft("Edited prompt one.");
    document.querySelector<HTMLButtonElement>("#new-custom-rule")!.click();
    await expectDiscardPrompt();
    document.querySelector<HTMLButtonElement>("#cancel-prompt-discard")!.click();
    expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!.value).toBe("Edited prompt one.");
    expect(document.querySelector('[data-prompt-id="rule-1"]')?.getAttribute("aria-current")).toBe("true");
    document.querySelector<HTMLButtonElement>("#new-custom-rule")!.click();
    await expectDiscardPrompt();
    document.querySelector<HTMLButtonElement>("#confirm-prompt-discard")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")?.value).toBe(""));
    expect(document.querySelector('[data-prompt-id][aria-current="true"]')).toBeNull();

    document.querySelector<HTMLButtonElement>('[data-prompt-id="rule-1"]')!.click();
    changeDraft("Another edit to prompt one.");
    document.querySelector<HTMLButtonElement>('[data-prompt-id="rule-2"]')!.click();
    await expectDiscardPrompt();
    document.querySelector<HTMLButtonElement>("#cancel-prompt-discard")!.click();
    expect(document.querySelector('[data-prompt-id="rule-1"]')?.getAttribute("aria-current")).toBe("true");
    expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!.value).toBe("Another edit to prompt one.");
    document.querySelector<HTMLButtonElement>('[data-prompt-id="rule-2"]')!.click();
    await expectDiscardPrompt();
    document.querySelector<HTMLButtonElement>("#confirm-prompt-discard")!.click();
    await vi.waitFor(() => expect(document.querySelector('[data-prompt-id="rule-2"]')?.getAttribute("aria-current")).toBe("true"));
    expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!.value).toBe("Prompt two.");

    changeDraft("Edited prompt two.");
    document.querySelector<HTMLButtonElement>("#settings-back")!.click();
    await expectDiscardPrompt();
    document.querySelector<HTMLButtonElement>("#confirm-prompt-discard")!.click();
    await vi.waitFor(() => expect(document.querySelector("#settings-page")).toBeNull());
    expect((document.activeElement as HTMLElement | null)?.id).toBe("settings");
  });

  it("marks the prompt editor invalid and describes it with the save error", async () => {
    const api = await loadApp({
      customRuleUpsert: () => Promise.reject(new Error("aggregate prompt limit")),
    });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#new-custom-rule")?.disabled).toBe(false));
    const prompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
    prompt.value = "This prompt will be rejected.";
    prompt.dispatchEvent(new InputEvent("input", { bubbles: true }));
    document.querySelector<HTMLFormElement>("#custom-rule-form")!.requestSubmit();

    await vi.waitFor(() => expect(api.customRuleUpsert).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")?.getAttribute("aria-invalid")).toBe("true"));
    const invalidPrompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
    expect(invalidPrompt.getAttribute("aria-describedby")?.split(/\s+/)).toEqual(
      expect.arrayContaining(["custom-rule-help", "custom-rule-count", "settings-message"]),
    );
    expect(document.querySelector("#settings-message")?.getAttribute("role")).toBe("alert");
    expect(document.activeElement).toBe(invalidPrompt);
  });

  it("keeps busy prompt navigation focusable and sends blocked actions to the status message", async () => {
    const save = deferred<CustomRule>();
    const api = await loadApp({ customRuleUpsert: () => save.promise });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#new-custom-rule")?.disabled).toBe(false));
    const prompt = document.querySelector<HTMLTextAreaElement>("#custom-rule-prompt")!;
    prompt.value = "Pending prompt.";
    prompt.dispatchEvent(new InputEvent("input", { bubbles: true }));
    document.querySelector<HTMLFormElement>("#custom-rule-form")!.requestSubmit();
    await vi.waitFor(() => expect(api.customRuleUpsert).toHaveBeenCalledOnce());

    let back = document.querySelector<HTMLButtonElement>("#settings-back")!;
    expect(back.disabled).toBe(false);
    expect(back.getAttribute("aria-disabled")).toBe("true");
    expect(back.tabIndex).toBe(0);
    back.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-message"));
    expect(document.querySelector('[data-settings-section="prompts"]')?.getAttribute("aria-current")).toBe("page");

    const models = document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!;
    expect(models.disabled).toBe(false);
    expect(models.getAttribute("aria-disabled")).toBe("true");
    models.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-message"));
    expect(document.querySelector('[data-settings-section="prompts"]')?.getAttribute("aria-current")).toBe("page");

    save.resolve(customRule("rule-created", "Pending prompt."));
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#settings-back")?.hasAttribute("aria-disabled")).toBe(false));
  });

  it("renders exactly one Settings main with labelled local-section navigation and page keyboard semantics", async () => {
    await loadApp();
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector("#settings-page")).not.toBeNull());
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-title"));
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="prompts"]')?.disabled).toBe(false));
    const page = document.querySelector<HTMLElement>("#settings-page")!;
    expect(page.tagName).toBe("MAIN");
    expect(page.getAttribute("aria-labelledby")).toBe("settings-title");
    expect(document.querySelectorAll("main")).toHaveLength(1);
    expect(document.querySelector("dialog, [role=tablist], [role=tab], [role=tabpanel], [inert]")).toBeNull();
    expect(document.querySelector(".progress-strip, .workflow-shell")).toBeNull();

    const localNav = page.querySelector<HTMLElement>('nav.settings-nav[aria-label="Mục cài đặt"]')!;
    const sectionButtons = [...localNav.querySelectorAll<HTMLButtonElement>("[data-settings-section]")];
    expect(sectionButtons.map(button => button.dataset.settingsSection)).toEqual(["prompts", "review-rules", "models"]);
    expect(localNav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(localNav.querySelector('[aria-current="page"]')?.getAttribute("data-settings-section")).toBe("prompts");
    expect(page.querySelectorAll(".settings-section")).toHaveLength(1);
    expect(page.querySelector("#settings-prompts")?.getAttribute("aria-labelledby")).toBe("settings-prompts-title");
    const footer = page.querySelector<HTMLElement>(".settings-footer")!;
    expect(footer.querySelector('button[type="submit"][form="custom-rule-form"]')).not.toBeNull();

    document.querySelector<HTMLButtonElement>('[data-settings-section="review-rules"]')!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-review-rules-title"));
    expect(document.querySelector('[data-settings-section="review-rules"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelectorAll('[aria-current="page"][data-settings-section]')).toHaveLength(1);
    expect(document.querySelector("#settings-review-rules")?.getAttribute("aria-labelledby")).toBe("settings-review-rules-title");
    expect(document.querySelectorAll("[data-review-rule]")).toHaveLength(5);

    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-models-title"));
    expect(document.querySelector('[data-settings-section="models"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelectorAll('[aria-current="page"][data-settings-section]')).toHaveLength(1);
    expect(document.querySelector("#settings-models")?.getAttribute("aria-labelledby")).toBe("settings-models-title");
    expect(
      [...document.querySelectorAll<HTMLButtonElement>(".settings-footer button")].map(button => button.id),
    ).toEqual(["model-import", "model-action"]);

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    expect(document.querySelector("#settings-page")).not.toBeNull();
    const currentPage = document.querySelector<HTMLElement>("#settings-page")!;
    const focusable = [...currentPage.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')];
    const lastFocusable = focusable.at(-1)!;
    lastFocusable.focus();
    const tabEvent = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
    lastFocusable.dispatchEvent(tabEvent);
    expect(tabEvent.defaultPrevented).toBe(false);
    expect(document.querySelector("#settings-page")).not.toBeNull();

    document.querySelector<HTMLButtonElement>("#settings-back")!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings"));
  });

  it("returns focus to each workflow Settings opener", async () => {
    const api = await loadApp({
      customRuleList: () => Promise.resolve([customRule("rule-1", "Giữ nguyên tên SoátVăn.")]),
    });
    await vi.waitFor(() => expect(api.customRuleList).toHaveBeenCalled());
    await chooseDocument();
    await vi.waitFor(() => expect(document.body.textContent).toContain("1 quy tắc riêng"));

    const cases = [
      ["manage-custom-rules", "settings-prompts-title"],
      ["manage-review-rules", "settings-review-rules-title"],
      ["open-model-settings", "settings-models-title"],
    ] as const;

    for (const [openerId, headingId] of cases) {
      const opener = document.querySelector<HTMLButtonElement>(`#${openerId}`)!;
      expect(opener).not.toBeNull();
      opener.focus();
      opener.click();
      await vi.waitFor(() => expect(document.querySelector("#settings-page")).not.toBeNull());
      await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe(headingId));
      await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#settings-back")?.disabled).toBe(false));
      document.querySelector<HTMLButtonElement>("#settings-back")!.click();
      await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe(openerId));
      expect(document.querySelector("#start")).not.toBeNull();
      expect(document.body.textContent).toContain("nguồn.docx");
    }
  });

  it("keeps the fixed review-rule inventory read-only and preserves workflow review state", async () => {
    const api = await loadApp({ modelStatus: () => Promise.resolve(signedReadyModel) });
    await chooseDocument();
    await vi.waitFor(() => expect(document.querySelector("#include-rule-findings")).not.toBeNull());
    document.querySelector<HTMLInputElement>("#include-rule-findings")!.click();
    expect(document.querySelector<HTMLInputElement>("#include-rule-findings")!.checked).toBe(true);

    document.querySelector<HTMLButtonElement>("#manage-review-rules")!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-review-rules-title"));
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#settings-back")?.disabled).toBe(false));
    const inventory = document.querySelector<HTMLElement>("#settings-review-rules")!;
    expect(inventory.getAttribute("aria-labelledby")).toBe("settings-review-rules-title");
    const ruleList = inventory.querySelector<HTMLUListElement>("ul.review-rule-inventory")!;
    expect(ruleList).not.toBeNull();
    expect(ruleList.querySelectorAll(":scope > li.review-rule-row[data-review-rule]")).toHaveLength(5);
    expect([...ruleList.children].every(item => item.tagName === "LI")).toBe(true);
    expect(inventory.querySelector("article.review-rule-row")).toBeNull();
    expect(inventory.querySelector("button, input, select, textarea")).toBeNull();
    expect(inventory.querySelector('[data-rule-toggle], input[name="preset"], #dictionary-form, #ignored-words')).toBeNull();
    expect(document.querySelector("#full-review, #include-rule-findings")).toBeNull();

    document.querySelector<HTMLButtonElement>("#settings-back")!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("manage-review-rules"));
    expect(document.querySelector('.stepper li[aria-current="step"]')?.textContent).toContain("Chuẩn bị");
    expect(document.body.textContent).toContain("nguồn.docx");
    expect(document.querySelector<HTMLInputElement>("#full-review")?.checked).toBe(true);
    expect(document.querySelector<HTMLInputElement>("#include-rule-findings")?.checked).toBe(true);
    expect(api.chooseDocument).toHaveBeenCalledOnce();
    expect(api.inspectDropped).not.toHaveBeenCalled();
  });

  it("ignores Ctrl+O plus HTML and Tauri file drops while Settings is active", async () => {
    const api = await loadApp();
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector("#settings-page")).not.toBeNull());

    const shortcut = new KeyboardEvent("keydown", { key: "o", ctrlKey: true, bubbles: true, cancelable: true });
    window.dispatchEvent(shortcut);
    expect(shortcut.defaultPrevented).toBe(true);

    const file = new File(["docx"], "khác.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" }) as File & { path?: string };
    Object.defineProperty(file, "path", { value: "C:\\Tài liệu\\khác.docx" });
    const htmlDrop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(htmlDrop, "dataTransfer", { value: { files: [file] } });
    window.dispatchEvent(htmlDrop);
    api.emitFileDrop("C:\\Tài liệu\\khác-tauri.docx");
    await Promise.resolve();

    expect(htmlDrop.defaultPrevented).toBe(true);
    expect(api.chooseDocument).not.toHaveBeenCalled();
    expect(api.inspectDropped).not.toHaveBeenCalled();
    expect(document.querySelector("#settings-page")).not.toBeNull();
  });

  it("falls back to the workflow heading when a conditional Settings opener disappears", async () => {
    const api = await loadApp({
      customRuleList: () => Promise.resolve([customRule("rule-1", "Keep this prompt.")]),
    });
    await vi.waitFor(() => expect(api.customRuleList).toHaveBeenCalled());
    await chooseDocument();
    await vi.waitFor(() => expect(document.querySelector("#open-model-settings")).not.toBeNull());

    document.querySelector<HTMLButtonElement>("#open-model-settings")!.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-models-title"));
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#model-action")?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>("#model-action")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLInputElement>("#use-model")?.checked).toBe(true));
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#settings-back")?.hasAttribute("aria-disabled")).toBe(false));
    document.querySelector<HTMLButtonElement>("#settings-back")!.click();

    await vi.waitFor(() => expect(document.querySelector("#settings-page")).toBeNull());
    expect(document.querySelector("#open-model-settings")).toBeNull();
    const workflowHeading = document.querySelector<HTMLElement>(".workflow-card h1")!;
    await vi.waitFor(() => expect(document.activeElement).toBe(workflowHeading));
    expect(workflowHeading.getAttribute("tabindex")).toBe("-1");
    expect(document.querySelector("#start")).not.toBeNull();
  });

  it("shows the Settings shell immediately and does not reopen after Back wins deferred loads", async () => {
    const modelLoad = deferred<ModelStatus>();
    const ruleLoad = deferred<CustomRule[]>();
    let modelStatusCalls = 0;
    let customRuleListCalls = 0;
    const api = await loadApp({
      modelStatus: () => {
        modelStatusCalls += 1;
        return modelStatusCalls === 1 ? Promise.resolve({ state: "not_installed" }) : modelLoad.promise;
      },
      customRuleList: () => {
        customRuleListCalls += 1;
        return customRuleListCalls === 1 ? Promise.resolve([]) : ruleLoad.promise;
      },
    });
    await vi.waitFor(() => expect(api.modelStatus).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(api.customRuleList).toHaveBeenCalledOnce());

    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(api.modelStatus).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(api.customRuleList).toHaveBeenCalledTimes(2));
    const page = document.querySelector<HTMLElement>("#settings-page")!;
    expect(page).not.toBeNull();
    expect(page.getAttribute("aria-busy")).toBe("true");
    const back = document.querySelector<HTMLButtonElement>("#settings-back")!;
    expect(back.disabled).toBe(false);
    back.click();
    await vi.waitFor(() => expect(document.querySelector("#choose")).not.toBeNull());

    modelLoad.resolve({ state: "not_installed" });
    ruleLoad.resolve([]);
    await Promise.resolve();
    await Promise.resolve();
    expect(document.querySelector("#settings-page")).toBeNull();
    expect(document.querySelector("#choose")).not.toBeNull();
  });

  it("compiles saved prompts and offers full review for an approved capable model", async () => {
    const api = await loadApp({
      modelStatus: () => Promise.resolve(signedReadyModel),
      customRuleList: () => Promise.resolve([
        customRule("rule-1", "Giữ nguyên tên SoátVăn."),
        customRule("rule-2", "Dùng thuật ngữ khách hàng.", "2026-08-25T02:00:00.000000Z"),
      ]),
    });
    await vi.waitFor(() => expect(api.customRuleList).toHaveBeenCalled());
    await chooseDocument();
    await vi.waitFor(() => expect(document.body.textContent).toContain("2 quy tắc riêng"));
    const fullReview = document.querySelector<HTMLInputElement>("#full-review")!;
    expect(fullReview).not.toBeNull();
    expect(fullReview.checked).toBe(true);
    expect(document.body.textContent).toContain("Chỉ dùng AI để rà soát");
    const includeRuleFindings = document.querySelector<HTMLInputElement>("#include-rule-findings")!;
    expect(includeRuleFindings).not.toBeNull();
    expect(includeRuleFindings.checked).toBe(false);
    expect(document.body.textContent).toContain("Bổ sung cảnh báo từ bộ quy tắc code");
    expect(document.body.textContent).toContain("AI vẫn rà toàn văn");
    includeRuleFindings.click();
    expect(document.querySelector<HTMLInputElement>("#include-rule-findings")!.checked).toBe(true);
    expect(document.body.textContent).toContain("AI rà soát toàn văn");
    expect(document.body.textContent).not.toContain("Chỉ dùng AI để rà soát");
    expect(document.body.textContent).toContain("AI là lớp rà soát chính");
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalled());
    expect(api.startJob.mock.calls[0][3]).toBe("Giữ nguyên tên SoátVăn.\n\nDùng thuật ngữ khách hàng.");
    expect(api.startJob.mock.calls[0][4]).toBe(true);
    expect(api.startJob.mock.calls[0][7]).toBe(true);
    expect(api.startJob.mock.calls[0][8]).toBe(true);
  });

  it("resets the optional code-rule findings when full review is switched off", async () => {
    await loadApp({ modelStatus: () => Promise.resolve(signedReadyModel) });
    await chooseDocument();
    await vi.waitFor(() => expect(document.querySelector("#include-rule-findings")).not.toBeNull());
    document.querySelector<HTMLInputElement>("#include-rule-findings")!.click();
    expect(document.querySelector<HTMLInputElement>("#include-rule-findings")!.checked).toBe(true);

    document.querySelector<HTMLInputElement>("#full-review")!.click();
    expect(document.querySelector("#include-rule-findings")).toBeNull();
    expect(document.body.textContent).toContain("Bộ kiểm tra cơ bản sẽ tạo candidate");

    document.querySelector<HTMLInputElement>("#full-review")!.click();
    expect(document.querySelector<HTMLInputElement>("#include-rule-findings")!.checked).toBe(false);
  });

  it("deactivates the model runtime when AI is switched off", async () => {
    const api = await loadApp({
      modelStatus: () => Promise.resolve(signedReadyModel),
      customRuleList: () => Promise.resolve([customRule("rule-1", "Giữ nguyên SoátVăn.")]),
    });
    await vi.waitFor(() => expect(api.customRuleList).toHaveBeenCalled());
    await chooseDocument();
    await vi.waitFor(() => expect(document.querySelector("#include-rule-findings")).not.toBeNull());
    document.querySelector<HTMLInputElement>("#include-rule-findings")!.click();
    expect(document.querySelector<HTMLInputElement>("#include-rule-findings")!.checked).toBe(true);
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLInputElement>("#use-model")?.disabled).toBe(false));
    const useModel = document.querySelector<HTMLInputElement>("#use-model")!;
    expect(useModel.disabled).toBe(false);
    expect(useModel.checked).toBe(true);
    useModel.click();
    await vi.waitFor(() => expect(api.modelDeactivate).toHaveBeenCalledOnce());
    expect(document.querySelector<HTMLInputElement>("#use-model")!.checked).toBe(false);
    document.querySelector<HTMLButtonElement>("#settings-back")!.click();
    expect(document.querySelector("#full-review")).toBeNull();
    expect(document.querySelector("#include-rule-findings")).toBeNull();
    expect(document.body.textContent).toContain("Chưa được áp dụng vì AI cục bộ đang tắt");
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalled());
    expect(api.startJob.mock.calls[0][3]).toBe("");
    expect(api.startJob.mock.calls[0][4]).toBe(false);
    expect(api.startJob.mock.calls[0][7]).toBe(false);
    expect(api.startJob.mock.calls[0][8]).toBe(false);
  });

  it("offers experimental full review for local-unverified AI without release approval", async () => {
    const localUnverified: ModelStatus = {
      state: "ready",
      model_id: "gemma-4-e4b",
      version: "local",
      trust: "local_unverified",
      release_approved: false,
      capabilities: { candidate_filter: true, full_review: true },
    };
    const api = await loadApp({
      modelStatus: () => Promise.resolve(localUnverified),
      customRuleList: () => Promise.resolve([customRule("rule-1", "Không đổi tên đơn vị.")]),
    });
    await vi.waitFor(() => expect(api.customRuleList).toHaveBeenCalled());
    await chooseDocument();
    const fullReview = document.querySelector<HTMLInputElement>("#full-review")!;
    expect(fullReview).not.toBeNull();
    expect(fullReview.checked).toBe(true);
    expect(document.body.textContent).toContain("chế độ thử nghiệm");
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();
    expect(document.body.textContent).toContain("kết quả chưa được phê duyệt cho phát hành");
    document.querySelector<HTMLButtonElement>("#settings-back")!.click();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(api.startJob).toHaveBeenCalled());
    expect(api.startJob.mock.calls[0][3]).toBe("Không đổi tên đơn vị.");
    expect(api.startJob.mock.calls[0][4]).toBe(true);
    expect(api.startJob.mock.calls[0][7]).toBe(true);
    expect(api.startJob.mock.calls[0][8]).toBe(false);
  });

  it("cancels an import and ignores its late success", async () => {
    const imported = deferred<ModelStatus | null>();
    const api = await loadApp({ modelImport: () => imported.promise });
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#model-import")?.disabled).toBe(false));
    const modelImport = document.querySelector<HTMLButtonElement>("#model-import")!;
    expect(modelImport.disabled).toBe(false);
    modelImport.click();
    await vi.waitFor(() => expect(api.modelImport).toHaveBeenCalledOnce());
    expect(document.body.textContent).toContain("Đang nhập gói model");
    document.querySelector<HTMLButtonElement>("#model-action")!.click();
    await vi.waitFor(() => expect(api.modelCancel).toHaveBeenCalledOnce());
    expect(document.body.textContent).toContain("Chưa cài AI cục bộ");

    imported.resolve(signedReadyModel);
    await Promise.resolve();
    await Promise.resolve();
    expect(document.body.textContent).toContain("Chưa cài AI cục bộ");
    expect(document.body.textContent).not.toContain("AI cục bộ đã sẵn sàng");
  });

  it("shows model download progress and can cancel the active operation", async () => {
    const api = await loadApp();
    const download = deferred<ModelStatus>();
    api.modelDownload.mockReturnValue(download.promise);
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')?.disabled).toBe(false));
    document.querySelector<HTMLButtonElement>('[data-settings-section="models"]')!.click();
    await vi.waitFor(() => expect(document.querySelector<HTMLButtonElement>("#model-action")?.disabled).toBe(false));
    const modelRadios = [...document.querySelectorAll<HTMLInputElement>('input[name="gemma-select"]')];
    expect(modelRadios).toHaveLength(3);
    expect(modelRadios.every(radio => radio.labels.length === 1)).toBe(true);
    expect(document.querySelectorAll("[data-model-download]")).toHaveLength(0);
    const modelAction = document.querySelector<HTMLButtonElement>("#model-action")!;
    expect(modelAction.disabled).toBe(false);
    modelAction.click();
    await vi.waitFor(() => expect(api.modelDownload).toHaveBeenCalled());
    expect(document.body.textContent).toContain("Đang tải model");
    const lockedBack = document.querySelector<HTMLButtonElement>("#settings-back")!;
    const lockedReviewNav = document.querySelector<HTMLButtonElement>("#review-nav")!;
    const lockedSections = [...document.querySelectorAll<HTMLButtonElement>("[data-settings-section]")];
    expect(lockedBack.disabled).toBe(false);
    expect(lockedBack.getAttribute("aria-disabled")).toBe("true");
    expect(lockedBack.getAttribute("aria-describedby")).toBe("settings-message");
    expect(lockedBack.tabIndex).toBe(0);
    expect(lockedReviewNav.disabled).toBe(false);
    expect(lockedReviewNav.getAttribute("aria-disabled")).toBe("true");
    expect(lockedSections.every(button => !button.disabled && button.getAttribute("aria-disabled") === "true")).toBe(true);

    lockedBack.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-message"));
    expect(document.querySelector('[data-settings-section="models"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector("#settings-page")).not.toBeNull();

    const lockedPromptNav = document.querySelector<HTMLButtonElement>('[data-settings-section="prompts"]')!;
    lockedPromptNav.click();
    await vi.waitFor(() => expect((document.activeElement as HTMLElement | null)?.id).toBe("settings-message"));
    expect(document.querySelector('[data-settings-section="models"]')?.getAttribute("aria-current")).toBe("page");
    expect(document.querySelector("#settings-page")).not.toBeNull();
    api.emitModelProgress(512, 1024);
    expect(document.querySelector("#model-download-progress")?.getAttribute("aria-valuenow")).toBe("50");
    document.querySelector<HTMLButtonElement>("#model-action")!.click();
    await vi.waitFor(() => expect(api.modelCancel).toHaveBeenCalled());
    expect(document.body.textContent).toContain("Chưa cài AI cục bộ");
    expect(document.querySelector<HTMLButtonElement>("#settings-back")?.disabled).toBe(false);
    expect(document.querySelector<HTMLButtonElement>("#settings-back")?.hasAttribute("aria-disabled")).toBe(false);
    expect(
      [...document.querySelectorAll<HTMLButtonElement>("[data-settings-section]")].every(button => !button.disabled && !button.hasAttribute("aria-disabled")),
    ).toBe(true);
    download.resolve(signedReadyModel);
    await Promise.resolve();
    expect(document.body.textContent).toContain("Chưa cài AI cục bộ");
  });
});

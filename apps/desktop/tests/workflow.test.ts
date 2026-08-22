import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentInfo, JobResult, ProgressEvent } from "../src/contracts";

const documentInfo: DocumentInfo = {
  path: "C:\\Tài liệu\\nguồn.docx",
  name: "nguồn.docx",
  size: 1024,
  paragraph_count: 4,
  table_cell_count: 1,
  character_count: 120,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => {
    resolve = done;
  });
  return { promise, resolve };
}

async function loadApp(options?: {
  choose?: () => Promise<DocumentInfo | null>;
  start?: () => Promise<JobResult>;
}) {
  let progressHandler: ((event: ProgressEvent) => void) | undefined;
  let fileDropHandler: ((path: string) => void) | undefined;
  const api = {
    chooseDocument: vi.fn(options?.choose ?? (() => Promise.resolve(documentInfo))),
    inspectDropped: vi.fn(() => Promise.resolve(documentInfo)),
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
    cancelJob: vi.fn(() => Promise.resolve(true)),
    onProgress: vi.fn(async (handler: (event: ProgressEvent) => void) => {
      progressHandler = handler;
      return () => undefined;
    }),
    onFileDrop: vi.fn(async (handler: (path: string) => void) => {
      fileDropHandler = handler;
      return () => undefined;
    }),
    openOutput: vi.fn(() => Promise.resolve()),
    dictionaryList: vi.fn(() => Promise.resolve([])),
    dictionaryUpsert: vi.fn(),
    dictionaryDelete: vi.fn(),
    dictionaryImport: vi.fn(),
    dictionaryExport: vi.fn(),
    modelStatus: vi.fn(() => Promise.resolve({ state: "not_installed" as const })),
    modelImport: vi.fn(),
    modelDownload: vi.fn(),
    modelCancel: vi.fn(),
    modelRemove: vi.fn(),
  };
  vi.doMock("../src/api", () => ({ api }));
  await import("../src/main");
  await vi.waitFor(() => expect(document.querySelector("#choose")).not.toBeNull());
  return Object.assign(api, {
    emitProgress(jobId: string, percent = 35) {
      progressHandler?.({
        job_id: jobId,
        stage: "rules",
        percent,
        message_code: "job.applying_rules",
      });
    },
    emitFileDrop(path: string) {
      fileDropHandler?.(path);
    },
  });
}

async function chooseDocument(): Promise<void> {
  document.querySelector<HTMLButtonElement>("#choose")!.click();
  await vi.waitFor(() => expect(document.querySelector("#start")).not.toBeNull());
}

beforeEach(() => {
  vi.resetModules();
  document.body.innerHTML = '<div id="app"></div>';
});

describe("four-step desktop workflow", () => {
  it("moves from file selection to three presets with AI prompt locked", async () => {
    await loadApp();
    expect(document.querySelectorAll(".stepper li")).toHaveLength(4);
    await chooseDocument();
    expect(document.querySelectorAll<HTMLInputElement>('input[name="preset"]')).toHaveLength(3);
    const prompt = document.querySelector<HTMLTextAreaElement>("#prompt")!;
    expect(prompt.disabled).toBe(true);
    expect(prompt.maxLength).toBe(1000);
    expect(document.body.textContent).toContain("nguồn.docx");
  });

  it("shows progress then only an output path, without document preview", async () => {
    const job = deferred<JobResult>();
    const api = await loadApp({ start: () => job.promise });
    await chooseDocument();
    document.querySelector<HTMLInputElement>('input[value="spelling"]')!.click();
    document.querySelector<HTMLButtonElement>("#start")!.click();
    await vi.waitFor(() => expect(document.querySelector("#cancel")).not.toBeNull());
    api.emitProgress(api.startJob.mock.calls[0][0]);
    expect(document.body.textContent).toContain("35%");
    expect(api.startJob.mock.calls[0][2]).toBe("spelling");
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
    document.querySelector<HTMLButtonElement>("#reveal")!.click();
    expect(api.openOutput).toHaveBeenNthCalledWith(1, "C:\\Tài liệu\\nguồn-soat.docx");
    expect(api.openOutput).toHaveBeenNthCalledWith(
      2,
      "C:\\Tài liệu\\nguồn-soat.docx",
      true,
    );
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

  it("keeps invalid files on step one with a safe error", async () => {
    await loadApp({ choose: () => Promise.reject(new Error("invalid")) });
    document.querySelector<HTMLButtonElement>("#choose")!.click();
    await vi.waitFor(() => expect(document.querySelector('[role="alert"]')).not.toBeNull());
    expect(document.body.textContent).toContain("Không thể mở tệp DOCX này");
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

  it("provides dictionary CRUD and CSV controls in Settings", async () => {
    const api = await loadApp();
    api.dictionaryList.mockResolvedValue([{ word: "SoátVăn", note: "Tên sản phẩm" }]);
    document.querySelector<HTMLButtonElement>("#settings")!.click();
    await vi.waitFor(() => expect(document.querySelector("#dictionary-form")).not.toBeNull());
    expect(document.querySelectorAll(".tabs button")).toHaveLength(3);
    expect(document.body.textContent).toContain("SoátVăn");

    const word = document.querySelector<HTMLInputElement>('input[name="word"]')!;
    const note = document.querySelector<HTMLInputElement>('input[name="note"]')!;
    word.value = "Nội bộ";
    note.value = "Được chấp nhận";
    document.querySelector<HTMLFormElement>("#dictionary-form")!.requestSubmit();
    await vi.waitFor(() =>
      expect(api.dictionaryUpsert).toHaveBeenCalledWith("Nội bộ", "Được chấp nhận"),
    );

    document.querySelector<HTMLButtonElement>("[data-delete]")!.click();
    await vi.waitFor(() => expect(api.dictionaryDelete).toHaveBeenCalledWith("SoátVăn"));
    document.querySelector<HTMLButtonElement>("#import-csv")!.click();
    await vi.waitFor(() => expect(api.dictionaryImport).toHaveBeenCalledOnce());
    document.querySelector<HTMLButtonElement>("#export-csv")!.click();
    await vi.waitFor(() => expect(api.dictionaryExport).toHaveBeenCalledOnce());
  });
});

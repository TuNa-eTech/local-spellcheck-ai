import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import type { DictionaryEntry, DocumentInfo, JobResult, ModelStatus, Preset, ProgressEvent, RuleOptions } from "./contracts";

const isTauri = () => "__TAURI_INTERNALS__" in window;

export const api = {
  async chooseDocument(): Promise<DocumentInfo | null> {
    if (!isTauri()) return { path: "C:\\Tai lieu\\van-ban.docx", name: "van-ban.docx", size: 184320, paragraph_count: 42, table_cell_count: 8, character_count: 6250, word_count: 1210, page_count: 5 };
    return invoke("choose_document");
  },
  async inspectDropped(path: string): Promise<DocumentInfo> { return invoke("inspect_document", { path }); },
  async startJob(jobId: string, sourcePath: string, preset: Preset, customPrompt: string, useModel: boolean, ruleOptions: RuleOptions, ignoredWords: string[]): Promise<JobResult> {
    if (!isTauri()) {
      await new Promise(resolve => setTimeout(resolve, 1400));
      return { job_id: jobId, status: "completed", output_path: sourcePath.replace(/\.docx$/i, "-soat.docx"), finding_count: 5, counts: { category: { spelling: 2, technical: 3 }, origin: { rule: 5 } } };
    }
    return invoke("start_job", { request: { jobId, sourcePath, preset, customPrompt, useModel, ruleOptions, ignoredWords } });
  },
  cancelJob(jobId: string) { return isTauri() ? invoke("cancel_job", { jobId }) : Promise.resolve(false); },
  onProgress(handler: (event: ProgressEvent) => void): Promise<UnlistenFn> {
    if (!isTauri()) {
      let percent = 0;
      const timer = window.setInterval(() => { percent = Math.min(96, percent + 8); handler({ job_id: "demo", stage: "rules", percent, message_code: "job.applying_rules" }); }, 110);
      return Promise.resolve(() => clearInterval(timer));
    }
    return listen<ProgressEvent>("job.progress", event => handler(event.payload));
  },
  onFileDrop(handler: (path: string) => void): Promise<UnlistenFn> {
    if (!isTauri()) return Promise.resolve(() => undefined);
    return getCurrentWebview().onDragDropEvent(event => {
      if (event.payload.type !== "drop") return;
      const path = event.payload.paths[0];
      if (path) handler(path);
    });
  },
  openOutput(path: string, reveal = false) { return invoke("open_output", { path, reveal }); },
  dictionaryList(query = ""): Promise<DictionaryEntry[]> { return invoke("dictionary_list", { query }); },
  dictionaryUpsert(word: string, note: string): Promise<DictionaryEntry> { return invoke("dictionary_upsert", { word, note }); },
  dictionaryDelete(word: string): Promise<boolean> { return invoke("dictionary_delete", { word }); },
  dictionaryImport(): Promise<number | null> { return invoke("dictionary_import"); },
  dictionaryExport(): Promise<number | null> { return invoke("dictionary_export"); },
  modelStatus(activate = true): Promise<ModelStatus> { return isTauri() ? invoke("model_status", { activate }) : Promise.resolve({ state: "not_installed" }); },
  modelDeactivate(): Promise<ModelStatus> { return isTauri() ? invoke("model_deactivate") : Promise.resolve({ state: "not_installed" }); },
  modelImport(): Promise<ModelStatus | null> { return invoke("model_import"); },
  modelDownload(): Promise<ModelStatus> { return invoke("model_download"); },
  modelCancel(): Promise<boolean> { return invoke("model_cancel"); },
  modelRemove(): Promise<ModelStatus> { return invoke("model_remove"); },
  onModelProgress(handler: (event: { received: number; total: number; percent: number }) => void): Promise<UnlistenFn> {
    if (!isTauri()) return Promise.resolve(() => undefined);
    return listen("model.progress", event => handler(event.payload as { received: number; total: number; percent: number }));
  },
};

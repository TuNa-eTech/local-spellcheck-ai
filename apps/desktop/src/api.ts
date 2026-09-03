import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { getVersion } from "@tauri-apps/api/app";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import type {
  AiConfigEntry,
  AiConfigState,
  AiTestConnectionResult,
  CustomRule,
  DocumentInfo,
  JobResult,
  ModelStatus,
  Preset,
  ProgressEvent,
  RuleOptions,
  Seq2SeqConfig,
} from "./contracts";

const isTauri = () => "__TAURI_INTERNALS__" in window;

export const api = {
  async chooseDocument(): Promise<DocumentInfo | null> {
    if (!isTauri()) return { path: "C:\\Tai lieu\\van-ban.docx", name: "van-ban.docx", size: 184320, paragraph_count: 42, table_cell_count: 8, character_count: 6250, word_count: 1210, page_count: 5 };
    return invoke("choose_document");
  },
  async inspectDropped(path: string): Promise<DocumentInfo> { return invoke("inspect_document", { path }); },
  async startJob(jobId: string, sourcePath: string, preset: Preset, customPrompt: string, useModel: boolean, ruleOptions: RuleOptions, ignoredWords: string[], fullReview: boolean, includeRuleFindings = false): Promise<JobResult> {
    if (!isTauri()) {
      await new Promise(resolve => setTimeout(resolve, 1400));
      return { job_id: jobId, status: "completed", output_path: sourcePath.replace(/\.docx$/i, "-soat.docx"), finding_count: 5, counts: { category: { spelling: 2, technical: 3 }, origin: { rule: 5 } } };
    }
    return invoke("start_job", { request: { jobId, sourcePath, preset, customPrompt, useModel, fullReview, includeRuleFindings, ruleOptions, ignoredWords } });
  },
  cancelJob(jobId: string) { return isTauri() ? invoke("cancel_job", { jobId }) : Promise.resolve(false); },
  onProgress(handler: (event: ProgressEvent) => void, jobId?: string): Promise<UnlistenFn> {
    if (!isTauri()) {
      let percent = 0;
      const timer = window.setInterval(() => { percent = Math.min(96, percent + 8); handler({ job_id: jobId ?? "demo", stage: "rules", percent, message_code: "job.applying_rules" }); }, 110);
      return Promise.resolve(() => clearInterval(timer));
    }
    return listen<ProgressEvent>("job-progress", event => handler(event.payload));
  },
  onFileDrop(handler: (path: string) => void): Promise<UnlistenFn> {
    if (!isTauri()) return Promise.resolve(() => undefined);
    return getCurrentWebview().onDragDropEvent(event => {
        if (event.payload.type !== "drop") return;
        const path = event.payload.paths[0];
        if (path) handler(path);
      }).catch(() => () => undefined);
  },
  appVersion(): Promise<string> { return isTauri() ? getVersion() : Promise.resolve("0.1.5"); },
  openOutput(path: string, reveal = false) { return invoke("open_output", { path, reveal }); },
  customRuleList(): Promise<CustomRule[]> { return invoke("custom_rule_list"); },
  customRuleUpsert(id: string | null, title: string, prompt: string, isDefault: boolean): Promise<CustomRule> { return invoke("custom_rule_upsert", { id, title, prompt, isDefault }); },
  customRuleDelete(id: string): Promise<boolean> { return invoke("custom_rule_delete", { id }); },
  modelStatus(activate = true): Promise<ModelStatus> { return isTauri() ? invoke("model_status", { activate }) : Promise.resolve({ state: "not_installed" }); },
  modelDeactivate(): Promise<ModelStatus> { return isTauri() ? invoke("model_deactivate") : Promise.resolve({ state: "not_installed" }); },
  modelImport(): Promise<ModelStatus | null> { return invoke("model_import"); },
  modelCancel(): Promise<boolean> { return invoke("model_cancel"); },
  modelRemove(modelId?: string): Promise<ModelStatus> { return invoke("model_remove", { modelId }); },
  aiConfigGet(): Promise<AiConfigState> {
    if (!isTauri()) {
      return Promise.resolve({
        active_provider: "local",
        configs: [
          {
            provider: "openai",
            masked_key: "sk-proj...1234",
            base_url: "https://api.openai.com/v1",
            model_name: "gpt-4o-mini",
            temperature: 0.0,
            timeout_seconds: 180,
            is_active: false,
          },
          {
            provider: "gemini",
            masked_key: "AIza...5678",
            base_url: "https://generativelanguage.googleapis.com/v1beta",
            model_name: "gemini-2.5-flash",
            temperature: 0.0,
            timeout_seconds: 180,
            is_active: false,
          },
        ],
      });
    }
    return invoke("ai_config_get");
  },
  aiConfigUpdate(params: {
    provider: string;
    apiKey?: string;
    api_key?: string;
    baseUrl?: string;
    base_url?: string;
    modelName?: string;
    model_name?: string;
    temperature?: number;
    timeoutSeconds?: number;
    timeout_seconds?: number;
    isActive?: boolean;
    is_active?: boolean;
  }): Promise<{ updated: boolean; config?: AiConfigEntry }> {
    if (!isTauri()) return Promise.resolve({ updated: true });
    return invoke("ai_config_update", {
      request: {
        provider: params.provider,
        apiKey: params.apiKey ?? params.api_key,
        baseUrl: params.baseUrl ?? params.base_url,
        modelName: params.modelName ?? params.model_name,
        temperature: params.temperature ?? 0.0,
        timeoutSeconds: params.timeoutSeconds ?? params.timeout_seconds ?? 180,
        isActive: params.isActive ?? params.is_active ?? false,
      },
    });
  },
  aiConfigSetActive(provider: string): Promise<{ active_provider: string }> {
    if (!isTauri()) return Promise.resolve({ active_provider: provider });
    return invoke("ai_config_set_active", { provider });
  },
  aiConfigTestConnection(params: {
    provider: string;
    apiKey?: string;
    api_key?: string;
    baseUrl?: string;
    base_url?: string;
    modelName?: string;
    model_name?: string;
  }): Promise<AiTestConnectionResult> {
    if (!isTauri()) {
      return Promise.resolve({
        ok: true,
        provider: params.provider,
        model: params.modelName ?? params.model_name ?? "mock-model",
      });
    }
    return invoke("ai_config_test_connection", {
      provider: params.provider,
      apiKey: params.apiKey ?? params.api_key,
      baseUrl: params.baseUrl ?? params.base_url,
      modelName: params.modelName ?? params.model_name,
    });
  },
  async seq2seqConfigGet(): Promise<Seq2SeqConfig> {
    if (!isTauri()) return { model_dir: "", is_configured: false, is_valid: false, is_enabled: true, runtime_available: true, is_ready: false };
    return invoke("seq2seq_config_get");
  },
  async seq2seqConfigUpdate(modelDir?: string, isEnabled?: boolean): Promise<Seq2SeqConfig> {
    if (!isTauri()) {
      return {
        model_dir: modelDir ?? "",
        is_configured: Boolean(modelDir),
        is_valid: Boolean(modelDir),
        is_enabled: isEnabled ?? true,
        runtime_available: true,
        is_ready: Boolean(modelDir) && (isEnabled ?? true),
      };
    }
    return invoke("seq2seq_config_update", {
      modelDir: modelDir ?? null,
      isEnabled: isEnabled ?? null,
    });
  },
  async chooseSeq2SeqModelDir(): Promise<string | null> {
    if (!isTauri()) return null;
    return invoke("choose_seq2seq_model_dir");
  },
};

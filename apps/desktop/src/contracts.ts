export type Preset = "standard" | "administrative" | "spelling";
export type Step = "file" | "rules" | "processing" | "result" | "no-findings";

export interface DocumentInfo {
  path: string;
  name: string;
  size: number;
  paragraph_count: number;
  table_cell_count: number;
  character_count: number;
}

export interface JobResult {
  job_id: string;
  status: "completed" | "no_findings";
  output_path: string | null;
  finding_count: number;
  counts: { category: Record<string, number>; origin: Record<string, number> } | Record<string, never>;
}

export interface ProgressEvent {
  job_id: string;
  stage: string;
  percent: number;
  message_code: string;
}

export interface DictionaryEntry { word: string; note: string }
export interface ModelStatus { state: "not_installed" | "installed" | "ready" | "invalid"; model_id?: string; version?: string; code?: string }

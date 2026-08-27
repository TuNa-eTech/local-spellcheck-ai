export type Preset = "standard" | "administrative" | "spelling";
export type Step = "file" | "rules" | "processing" | "result" | "no-findings";

export interface RuleOptions {
  technical: boolean;
  repeated_words: boolean;
  confusions: boolean;
  syllables: boolean;
  administrative_capitalization: boolean;
}

export interface DocumentInfo {
  path: string;
  name: string;
  size: number;
  paragraph_count: number;
  table_cell_count: number;
  character_count: number;
  word_count: number;
  page_count?: number;
}

export interface ReviewCoverage {
  status: "complete" | "partial";
  total_chunks: number;
  reviewed_chunks: number;
  failed_chunks: number;
  total_blocks: number;
  reviewed_blocks: number;
  failed_blocks: number;
  timeout_chunks?: number;
  invalid_output_chunks?: number;
  inference_error_chunks?: number;
  retried_chunks?: number;
  recovered_chunks?: number;
}

export interface JobResult {
  job_id: string;
  status: "completed" | "no_findings" | "partial";
  output_path: string | null;
  finding_count: number;
  counts: { category: Record<string, number>; origin: Record<string, number> } | Record<string, never>;
  review?: ReviewCoverage | null;
}

export interface ProgressEvent {
  job_id: string;
  stage: string;
  percent: number;
  message_code: string;
}

export interface CustomRule {
  id: string;
  prompt: string;
  created_at: string;
  updated_at: string;
}

export interface ModelCapabilities {
  candidate_filter: boolean;
  full_review: boolean;
}

export interface ModelStatus {
  state: "not_installed" | "downloading" | "importing" | "verifying" | "installed" | "ready" | "unverified" | "invalid" | "cancelled" | "error" | "incompatible";
  model_id?: string;
  version?: string;
  code?: string;
  trust?: "release_signed" | "local_unverified";
  release_approved?: boolean;
  capabilities?: ModelCapabilities;
}

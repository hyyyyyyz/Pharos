// Mirrors the backend Pydantic schemas (xuanzang/api/schemas.py).

export type JobStatus = "queued" | "running" | "done" | "error";

export interface Job {
  id: string;
  paper_id: string;
  status: JobStatus;
  stage: string;
  progress: number; // 0–100
  translator_type: string;
  target_lang: string;
  error: string | null;
  tokens: number | null;
  total_seconds: number | null;
  has_mono: boolean;
  has_dual: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Paper {
  id: string;
  title: string;
  orig_filename: string;
  page_count: number | null;
  source: string;
  source_lang: string;
  added_at: string;
  latest_job: Job | null;
}

export type PdfKind = "original" | "mono" | "dual";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/**
 * View model: maps the backend's `Paper`/`Job` onto the fields the UI renders.
 *
 * The design prototype assumed rich bibliographic metadata (authors, venue,
 * DOI, abstract, tags). The backend does not extract that yet, so those fields
 * come back as `null` and the UI renders a “—” placeholder — no invented data.
 */
import type { Job, Paper } from "../api/types";

export type PaperStatus = "untranslated" | "translating" | "translated" | "failed";

export interface PaperVM {
  id: string;
  /** Display title (the backend's title, usually derived from the PDF). */
  title: string;
  file: string;
  pages: number | null;
  status: PaperStatus;
  /** 0–100 while translating. */
  progress: number;
  addedAt: string;
  isZotero: boolean;
  /** Metadata the backend cannot supply yet. */
  authors: string | null;
  year: number | null;
  venue: string | null;
  doi: string | null;
  abstract: string | null;
  tags: string[];
  job: Job | null;
}

export function statusOf(job: Job | null | undefined): PaperStatus {
  if (!job) return "untranslated";
  if (job.status === "queued" || job.status === "running") return "translating";
  if (job.status === "done") return job.has_mono ? "translated" : "untranslated";
  return "failed";
}

export function isJobActive(job: Job | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

export function toVM(p: Paper): PaperVM {
  const job = p.latest_job ?? null;
  return {
    id: p.id,
    title: p.title || p.orig_filename,
    file: p.orig_filename,
    pages: p.page_count,
    status: statusOf(job),
    progress: job?.progress ?? 0,
    addedAt: p.added_at,
    isZotero: p.source === "zotero",
    authors: null,
    year: null,
    venue: null,
    doi: null,
    abstract: null,
    tags: [],
    job,
  };
}

/** “—” for anything the backend hasn’t given us. */
export const dash = (v: string | number | null | undefined): string =>
  v === null || v === undefined || v === "" ? "—" : String(v);

/** Badge text + colour for a status pill, matching the design prototype. */
export function statusMeta(st: PaperStatus): { label: string; cls: string } {
  switch (st) {
    case "translating":
      return { label: "翻译中", cls: "is-translating" };
    case "translated":
      return { label: "已译", cls: "is-translated" };
    case "failed":
      return { label: "失败", cls: "is-failed" };
    default:
      return { label: "未译", cls: "is-untranslated" };
  }
}

/** Stage labels shown in the translating screen (design: 解析版面/翻译正文/重排版面). */
export const TRANSLATE_STAGES = ["解析版面", "翻译正文", "重排版面"];

/** Map a backend job stage onto the 3-step visual stepper. */
export function stageIndex(job: Job | null): number {
  if (!job) return 0;
  const s = job.stage?.toLowerCase() ?? "";
  if (s.includes("typeset") || s.includes("排版")) return 2;
  if (s.includes("translat") || s.includes("翻译")) return 1;
  if (s.includes("pars") || s.includes("解析") || s.includes("queue")) return 0;
  // fall back to progress
  return Math.min(2, Math.floor((job.progress / 100) * 3));
}

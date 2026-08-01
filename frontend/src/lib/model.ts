/**
 * View model: maps the backend's `Paper`/`Job` onto the fields the UI renders.
 *
 * The backend now extracts bibliographic metadata, but only ever reports what it
 * could determine with confidence — a field it is unsure about comes back
 * null/empty rather than guessed. The UI mirrors that contract: it renders a
 * “—” placeholder for anything missing and never fabricates a stand-in, because
 * a wrong author list is worse than a visibly absent one. `tags` stays empty
 * because tagging genuinely has no backend yet.
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
  pdfAvailable: boolean;
  /** Empty when the backend could not extract authors confidently. */
  authors: string[];
  year: number | null;
  venue: string | null;
  /** Bare DOI; callers build the https://doi.org/ URL themselves. */
  doi: string | null;
  abstract: string | null;
  /** Always empty — tagging has no backend yet. */
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
    pdfAvailable: p.orig_filename !== "",
    authors: p.authors ?? [],
    year: p.year,
    venue: p.venue,
    doi: p.doi,
    abstract: p.abstract,
    tags: [],
    job,
  };
}

/** “—” for anything the backend hasn’t given us. An author list is accepted
 *  directly so callers that only need a plain inline rendering don’t each
 *  reimplement the empty-array case. */
export const dash = (v: string | number | readonly string[] | null | undefined): string => {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length === 0 ? "—" : v.join(", ");
  return String(v);
};

/**
 * Compact author label for the narrow 作者 column.
 *
 * The backend stores authors in “A. Vaswani” form, so the last whitespace-
 * separated token is the surname — the only part that fits in 80px. Returns
 * null (not a guess) when there is nothing to show.
 */
export function compactAuthors(authors: readonly string[]): string | null {
  const surname = (name: string): string => {
    const parts = name.trim().split(/\s+/);
    return parts[parts.length - 1] ?? "";
  };
  const named = authors.map(surname).filter((s) => s !== "");
  if (named.length === 0) return null;
  if (named.length === 1) return named[0]!;
  if (named.length === 2) return `${named[0]} 和 ${named[1]}`;
  return `${named[0]} 等`;
}

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

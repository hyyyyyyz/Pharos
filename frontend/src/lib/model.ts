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
import type { LocalZoteroPaper } from "./localZotero";
import type { ZoteroItemDetail, ZoteroItemSummary } from "../types/zotero";
import { zoteroItemId } from "./zotero";

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
  isLocalZotero: boolean;
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
    isLocalZotero: false,
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

export function localToVM(p: LocalZoteroPaper): PaperVM {
  return {
    id: p.id,
    title: p.title,
    file: p.pdfFilename ?? "本地 PDF 未下载",
    pages: null,
    status: "untranslated",
    progress: 0,
    addedAt: p.dateAdded ?? "",
    isZotero: true,
    isLocalZotero: true,
    pdfAvailable: p.pdfAvailable,
    authors: p.authors,
    year: p.year,
    venue: p.venue,
    doi: p.doi,
    abstract: p.abstractText,
    tags: [],
    job: null,
  };
}

const zoteroCreatorName = (creator: ZoteroItemSummary["creators"][number]): string | null => {
  const direct = creator.name?.trim();
  if (direct) return direct;
  const joined = `${creator.firstName ?? ""} ${creator.lastName ?? ""}`.trim();
  return joined || null;
};

export function zoteroToVM(item: ZoteroItemSummary): PaperVM {
  return {
    id: zoteroItemId({
      sourceId: item.sourceId,
      libraryId: item.libraryId,
      itemKey: item.key,
    }),
    title: item.title?.trim() || "未命名 Zotero 条目",
    file:
      item.attachmentCount > 0
        ? `${item.availableAttachmentCount}/${item.attachmentCount} 份本地附件`
        : "没有 PDF 附件",
    pages: null,
    status: "untranslated",
    progress: 0,
    addedAt: item.dateAdded ?? item.dateModified ?? "",
    isZotero: true,
    isLocalZotero: true,
    pdfAvailable: item.availableAttachmentCount > 0,
    authors: item.creators.map(zoteroCreatorName).filter((value): value is string => value !== null),
    year: item.year,
    venue: item.venue,
    doi: item.doi,
    abstract: item.abstractNote,
    tags: item.tags.map((tag) => tag.tag),
    job: null,
  };
}

const rawData = (raw: unknown): Record<string, unknown> => {
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return {};
  const record = raw as Record<string, unknown>;
  const data = record.data;
  return data !== null && typeof data === "object" && !Array.isArray(data)
    ? (data as Record<string, unknown>)
    : record;
};

const rawString = (record: Record<string, unknown>, key: string): string | null =>
  typeof record[key] === "string" ? (record[key] as string) : null;

export function zoteroDetailToVM(detail: ZoteroItemDetail): PaperVM {
  const data = rawData(detail.item.raw);
  const date = rawString(data, "date") ?? "";
  const yearMatch = date.match(/(?:^|\D)(\d{4})(?:\D|$)/);
  const venue = [
    "publicationTitle",
    "proceedingsTitle",
    "conferenceName",
    "repository",
    "university",
  ]
    .map((key) => rawString(data, key)?.trim() ?? "")
    .find(Boolean);
  const doi = rawString(data, "DOI")
    ?.trim()
    .replace(/^https?:\/\/doi\.org\//i, "")
    .replace(/^doi:/i, "");
  return {
    id: zoteroItemId({
      sourceId: detail.item.sourceId,
      libraryId: detail.item.libraryId,
      itemKey: detail.item.key,
    }),
    title: detail.item.title?.trim() || "未命名 Zotero 条目",
    file: detail.attachments[0]?.filename ?? "没有 PDF 附件",
    pages: null,
    status: "untranslated",
    progress: 0,
    addedAt: detail.item.dateAdded ?? detail.item.dateModified ?? "",
    isZotero: true,
    isLocalZotero: true,
    pdfAvailable: detail.attachments.some((attachment) => attachment.available),
    authors: detail.item.creators
      .map(zoteroCreatorName)
      .filter((value): value is string => value !== null),
    year: yearMatch ? Number(yearMatch[1]) : null,
    venue: venue || null,
    doi: doi || null,
    abstract: detail.item.abstractNote,
    tags: detail.item.tags.map((tag) => tag.tag),
    job: null,
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

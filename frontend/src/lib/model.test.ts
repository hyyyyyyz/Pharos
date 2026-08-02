/**
 * The view model is where the backend's honesty about missing metadata either
 * survives into the UI or gets quietly replaced by a guess. These tests pin the
 * cases where the obvious implementation is wrong in a way nothing else catches:
 * a finished job that produced no PDF, a zero that must not read as "missing",
 * and a stage index that must stay inside a three-step stepper.
 */
import { describe, expect, it } from "vitest";

import type { Job, Paper } from "../api/types";
import {
  compactAuthors,
  dash,
  isJobActive,
  stageIndex,
  statusOf,
  toVM,
} from "./model";

const job = (over: Partial<Job> = {}): Job => ({
  id: "j1",
  paper_id: "p1",
  status: "done",
  stage: "",
  progress: 100,
  translator_type: "openai",
  target_lang: "zh",
  error: null,
  tokens: null,
  total_seconds: null,
  has_mono: true,
  has_dual: false,
  created_at: "2026-01-01T00:00:00Z",
  started_at: null,
  finished_at: null,
  ...over,
});

const paper = (over: Partial<Paper> = {}): Paper => ({
  id: "p1",
  title: "Attention Is All You Need",
  orig_filename: "attention.pdf",
  page_count: 15,
  source: "upload",
  source_lang: "en",
  added_at: "2026-01-01T00:00:00Z",
  authors: ["A. Vaswani", "N. Shazeer"],
  year: 2017,
  venue: "NeurIPS",
  doi: "10.5555/3295222",
  abstract: "…",
  meta_source: "pdf",
  latest_job: null,
  ...over,
});

describe("statusOf", () => {
  it("reports no job as untranslated", () => {
    expect(statusOf(null)).toBe("untranslated");
    expect(statusOf(undefined)).toBe("untranslated");
  });

  it("folds queued and running into one in-flight state", () => {
    expect(statusOf(job({ status: "queued" }))).toBe("translating");
    expect(statusOf(job({ status: "running" }))).toBe("translating");
  });

  it("calls a finished job translated only once a PDF exists", () => {
    // A job can reach "done" without producing a mono PDF. Trusting the status
    // alone would offer the reader a translation that cannot be opened, so the
    // artefact — not the job row — is what decides.
    expect(statusOf(job({ status: "done", has_mono: true }))).toBe("translated");
    expect(statusOf(job({ status: "done", has_mono: false }))).toBe("untranslated");
  });

  it("reports an errored job as failed", () => {
    expect(statusOf(job({ status: "error" }))).toBe("failed");
  });
});

describe("isJobActive", () => {
  it("is true exactly while the backend still owns the job", () => {
    expect(isJobActive(job({ status: "queued" }))).toBe(true);
    expect(isJobActive(job({ status: "running" }))).toBe(true);
    expect(isJobActive(job({ status: "done" }))).toBe(false);
    expect(isJobActive(job({ status: "error" }))).toBe(false);
    expect(isJobActive(null)).toBe(false);
    expect(isJobActive(undefined)).toBe(false);
  });
});

describe("dash", () => {
  it("shows the placeholder only for genuinely absent values", () => {
    expect(dash(null)).toBe("—");
    expect(dash(undefined)).toBe("—");
    expect(dash("")).toBe("—");
    expect(dash([])).toBe("—");
  });

  it("keeps zero, which is a value and not an absence", () => {
    // A falsy test here would print "—" for a paper with 0 pages or year 0.
    expect(dash(0)).toBe("0");
  });

  it("renders an author list inline", () => {
    expect(dash(["A. Vaswani", "N. Shazeer"])).toBe("A. Vaswani, N. Shazeer");
  });

  it("stringifies anything else", () => {
    expect(dash(2017)).toBe("2017");
    expect(dash("NeurIPS")).toBe("NeurIPS");
  });
});

describe("compactAuthors", () => {
  it("returns null rather than a guess when there is nothing to show", () => {
    expect(compactAuthors([])).toBeNull();
    expect(compactAuthors(["   "])).toBeNull();
  });

  it("keeps the surname, which is the part that fits the column", () => {
    expect(compactAuthors(["A. Vaswani"])).toBe("Vaswani");
    expect(compactAuthors(["Jean-Baptiste  Alayrac"])).toBe("Alayrac");
  });

  it("names both authors of a pair and elides any longer list", () => {
    expect(compactAuthors(["A. Vaswani", "N. Shazeer"])).toBe("Vaswani 和 Shazeer");
    expect(compactAuthors(["A. Vaswani", "N. Shazeer", "N. Parmar"])).toBe("Vaswani 等");
  });
});

describe("stageIndex", () => {
  it("maps the backend's stage names onto the three-step stepper", () => {
    expect(stageIndex(job({ stage: "parsing" }))).toBe(0);
    expect(stageIndex(job({ stage: "queued" }))).toBe(0);
    expect(stageIndex(job({ stage: "translating" }))).toBe(1);
    expect(stageIndex(job({ stage: "typesetting" }))).toBe(2);
  });

  it("recognises the Chinese stage names the engine also emits", () => {
    expect(stageIndex(job({ stage: "解析版面" }))).toBe(0);
    expect(stageIndex(job({ stage: "翻译正文" }))).toBe(1);
    expect(stageIndex(job({ stage: "重排版面" }))).toBe(2);
  });

  it("matches stage names case-insensitively", () => {
    expect(stageIndex(job({ stage: "Translating body text" }))).toBe(1);
  });

  it("falls back to progress for a stage it does not recognise", () => {
    expect(stageIndex(job({ stage: "warming up", progress: 0 }))).toBe(0);
    expect(stageIndex(job({ stage: "warming up", progress: 50 }))).toBe(1);
    // Clamped: an unclamped (progress / 100) * 3 yields 3 at completion, which
    // is one past the last step and renders nothing as active.
    expect(stageIndex(job({ stage: "warming up", progress: 100 }))).toBe(2);
  });

  it("starts at the first step with no job at all", () => {
    expect(stageIndex(null)).toBe(0);
  });
});

describe("toVM", () => {
  it("falls back to the filename when the backend extracted no title", () => {
    expect(toVM(paper({ title: "" })).title).toBe("attention.pdf");
  });

  it("passes absent metadata through instead of inventing a stand-in", () => {
    const vm = toVM(paper({ authors: [], year: null, venue: null, doi: null, abstract: null }));
    expect(vm.authors).toEqual([]);
    expect(vm.year).toBeNull();
    expect(vm.venue).toBeNull();
    expect(vm.doi).toBeNull();
    expect(vm.abstract).toBeNull();
  });

  it("reports no PDF when the record carries no filename", () => {
    expect(toVM(paper()).pdfAvailable).toBe(true);
    expect(toVM(paper({ orig_filename: "" })).pdfAvailable).toBe(false);
  });

  it("derives status and progress from the latest job", () => {
    const vm = toVM(paper({ latest_job: job({ status: "running", progress: 42 }) }));
    expect(vm.status).toBe("translating");
    expect(vm.progress).toBe(42);
  });

  it("leaves tags empty, because tagging has no backend", () => {
    expect(toVM(paper()).tags).toEqual([]);
  });
});

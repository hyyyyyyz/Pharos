import { describe, expect, it } from "vitest";

import type { Job } from "../api/types";
import { compactAuthors, dash, stageIndex, statusOf } from "./model";

const job = (patch: Partial<Job> = {}): Job => ({
  id: "j1",
  paper_id: "p1",
  status: "running",
  stage: "",
  progress: 0,
  translator_type: "openai_compatible",
  target_lang: "zh",
  error: null,
  tokens: null,
  total_seconds: null,
  has_mono: false,
  has_dual: false,
  created_at: "2026-08-01T00:00:00Z",
  started_at: null,
  finished_at: null,
  ...patch,
});

/**
 * The desktop client ports this function term for term
 * (`client/chrome/content/zotero/xpcom/pharos/translate.js::stageIndex`). The
 * two drifting apart means a user with both surfaces open sees one job
 * described as being at two different steps, so the cases below are the
 * contract between the two products and not just this one's behaviour.
 */
describe("stageIndex", () => {
  it("reads the engine's own label, in either language", () => {
    expect(stageIndex(job({ stage: "parsing layout" }))).toBe(0);
    expect(stageIndex(job({ stage: "解析版面" }))).toBe(0);
    expect(stageIndex(job({ stage: "translating" }))).toBe(1);
    expect(stageIndex(job({ stage: "翻译正文" }))).toBe(1);
    expect(stageIndex(job({ stage: "typesetting" }))).toBe(2);
    expect(stageIndex(job({ stage: "重排版面" }))).toBe(2);
  });

  it("treats a queued job as the first step rather than as unknown", () => {
    expect(stageIndex(job({ status: "queued", stage: "queued" }))).toBe(0);
  });

  it("matches the label case-insensitively and as a substring", () => {
    // BabelDOC's labels are whole sentences and their case is not ours to
    // rely on; an exact-match rewrite would send every real job down the
    // progress fallback below.
    expect(stageIndex(job({ stage: "Typesetting page 3/12" }))).toBe(2);
    expect(stageIndex(job({ stage: "Translating paragraphs" }))).toBe(1);
  });

  it("prefers the label over the percentage when they disagree", () => {
    // A job 90% of the way through parsing is still parsing. The stage is the
    // engine's own statement; progress is only a guess about it.
    expect(stageIndex(job({ stage: "parsing", progress: 90 }))).toBe(0);
  });

  it("falls back to progress for a label it does not recognise", () => {
    // A stepper frozen on step one for a job that is 80% done is a worse lie
    // than a rough guess — the desktop's comment for the same branch.
    expect(stageIndex(job({ stage: "cleaning up", progress: 0 }))).toBe(0);
    expect(stageIndex(job({ stage: "cleaning up", progress: 50 }))).toBe(1);
    expect(stageIndex(job({ stage: "cleaning up", progress: 80 }))).toBe(2);
    // 100% must clamp to the last step, never to a fourth one that has no label.
    expect(stageIndex(job({ stage: "cleaning up", progress: 100 }))).toBe(2);
  });

  it("is the first step when there is no job at all", () => {
    expect(stageIndex(null)).toBe(0);
  });
});

describe("statusOf", () => {
  it("reports a paper with no job as untranslated", () => {
    expect(statusOf(null)).toBe("untranslated");
    expect(statusOf(undefined)).toBe("untranslated");
  });

  it("collapses queued and running into one 翻译中 state", () => {
    expect(statusOf(job({ status: "queued" }))).toBe("translating");
    expect(statusOf(job({ status: "running" }))).toBe("translating");
  });

  it("only calls a finished job translated when a mono PDF actually exists", () => {
    // `done` is the job's status, not the artefact's existence. A run that
    // ended without producing the translated PDF has nothing for the reader to
    // open, so 已译 would be a badge pointing at a file that is not there.
    expect(statusOf(job({ status: "done", has_mono: true }))).toBe("translated");
    expect(statusOf(job({ status: "done", has_mono: false }))).toBe("untranslated");
  });

  it("treats anything else as a failure", () => {
    // The backend's terminal failure is `error`; the view model's is `failed`.
    // The two vocabularies are deliberately not the same word, so this is the
    // one place the mapping between them is written down.
    expect(statusOf(job({ status: "error" }))).toBe("failed");
  });
});

describe("compactAuthors", () => {
  it("takes the surname, which is the last token of the backend's 'A. Vaswani' form", () => {
    expect(compactAuthors(["A. Vaswani"])).toBe("Vaswani");
  });

  it("names both authors of a pair and abbreviates beyond that", () => {
    expect(compactAuthors(["A. Vaswani", "N. Shazeer"])).toBe("Vaswani 和 Shazeer");
    expect(compactAuthors(["A. Vaswani", "N. Shazeer", "N. Parmar"])).toBe("Vaswani 等");
  });

  it("returns null rather than a stand-in when there is nothing to show", () => {
    // Null is the signal for "the backend could not extract authors", which the
    // callers render as “—”. An empty string would render as a blank cell and
    // read as an author list that is genuinely empty.
    expect(compactAuthors([])).toBeNull();
    expect(compactAuthors(["", "   "])).toBeNull();
  });
});

describe("dash", () => {
  it("renders a placeholder for everything the backend did not give us", () => {
    expect(dash(null)).toBe("—");
    expect(dash(undefined)).toBe("—");
    expect(dash("")).toBe("—");
    // An empty array is the same absence, not an empty join.
    expect(dash([])).toBe("—");
  });

  it("passes real values through, including a falsy zero", () => {
    expect(dash(2017)).toBe("2017");
    // 0 is a page count, a score or a year — a value, and never a gap.
    expect(dash(0)).toBe("0");
    expect(dash(["Vaswani", "Shazeer"])).toBe("Vaswani, Shazeer");
  });
});

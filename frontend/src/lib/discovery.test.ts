import { describe, expect, it } from "vitest";

import { isRulesAnalysis, sourceList, sourceName } from "./discovery";

describe("sourceName", () => {
  it("gives each project the name it writes for itself", () => {
    // The wire ids are lowercase and neither project spells its own name that
    // way. This helper exists precisely so no view prints one.
    expect(sourceName("arxiv")).toBe("arXiv");
    expect(sourceName("openalex")).toBe("OpenAlex");
  });

  it("matches the id however the backend cased it", () => {
    expect(sourceName("arXiv")).toBe("arXiv");
    expect(sourceName("OPENALEX")).toBe("OpenAlex");
  });

  it("passes an unknown source through rather than blanking it", () => {
    // A provider added on the server before this build knows about it still has
    // to be nameable; dropping it would understate where a result came from.
    expect(sourceName("semanticscholar")).toBe("semanticscholar");
  });
});

describe("sourceList", () => {
  it("says so when a result carries no source at all", () => {
    expect(sourceList([])).toBe("来源未知");
  });

  it("joins display names, never wire ids", () => {
    expect(sourceList(["arxiv", "openalex"])).toBe("arXiv、OpenAlex");
  });
});

describe("isRulesAnalysis", () => {
  it("is false for an llm reading, in whatever case it arrives", () => {
    expect(isRulesAnalysis({ analysis_mode: "llm" })).toBe(false);
    expect(isRulesAnalysis({ analysis_mode: "LLM" })).toBe(false);
  });

  it("is true for rules", () => {
    expect(isRulesAnalysis({ analysis_mode: "rules" })).toBe(true);
  });

  it("falls back to rules for anything it does not recognise", () => {
    // The direction of this failure is the whole point. A mode this build has
    // not heard of, a field the backend forgot, or a half-built object all have
    // to read as rules: the cost of that mistake is a cautious label on a real
    // AI reading, while the cost of the opposite is an English sentence cut out
    // of an abstract presented as a model's Chinese analysis.
    expect(isRulesAnalysis({ analysis_mode: "" })).toBe(true);
    expect(isRulesAnalysis({ analysis_mode: "deep" })).toBe(true);
    expect(isRulesAnalysis({} as { analysis_mode: string })).toBe(true);
  });
});

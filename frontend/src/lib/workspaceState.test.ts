import { describe, expect, it } from "vitest";

import { DEFAULT_WORKSPACE, parseWorkspace } from "./workspaceState";

describe("parseWorkspace", () => {
  it("returns the default for nothing, garbage, or a non-object", () => {
    expect(parseWorkspace(null)).toEqual(DEFAULT_WORKSPACE);
    expect(parseWorkspace("{not json")).toEqual(DEFAULT_WORKSPACE);
    expect(parseWorkspace("42")).toEqual(DEFAULT_WORKSPACE);
  });

  it("round-trips module, paper tabs and the active tab", () => {
    const snap = {
      activeModule: "daily",
      tabs: [
        { id: "library", kind: "library" },
        { id: "t-a", kind: "paper", paperId: "a" },
        { id: "t-b", kind: "paper", paperId: "b" },
      ],
      activeTabId: "t-b",
    };
    expect(parseWorkspace(JSON.stringify(snap))).toEqual(snap);
  });

  it("drops malformed and duplicate tabs, unknown modules, and a dangling active tab", () => {
    const raw = JSON.stringify({
      activeModule: "writing",
      tabs: [
        { id: "t-a", kind: "paper", paperId: "a" },
        { id: "t-a", kind: "paper", paperId: "a" },
        { id: "t-x", kind: "paper", paperId: "y" },
        { id: "t-", kind: "paper", paperId: "" },
        null,
      ],
      activeTabId: "t-gone",
    });
    expect(parseWorkspace(raw)).toEqual({
      activeModule: "library",
      tabs: [
        { id: "library", kind: "library" },
        { id: "t-a", kind: "paper", paperId: "a" },
      ],
      activeTabId: "library",
    });
  });
});

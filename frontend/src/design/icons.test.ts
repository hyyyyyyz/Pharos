import { describe, expect, it } from "vitest";

import { Icons } from "./icons";

describe("navigation icons", () => {
  // One icon per destination, never shared: two rail rows with the same glyph
  // were the original complaint.
  it("gives every navigation destination its own icon", () => {
    const nav = [Icons.library, Icons.runs, Icons.daily, Icons.compass, Icons.project, Icons.sliders, Icons.shield, Icons.gear];
    expect(new Set(nav).size).toBe(nav.length);
  });

  it("keeps the magnifier for search boxes only", () => {
    expect(Icons.compass).not.toBe(Icons.search);
  });
});

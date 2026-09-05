import { describe, expect, it } from "vitest";

import { isDrawingPointer } from "./pointer";

describe("isDrawingPointer (防手指)", () => {
  it("lets a pen mark the page", () => {
    expect(isDrawingPointer({ pointerType: "pen" })).toBe(true);
  });

  it("turns a finger away by default", () => {
    expect(isDrawingPointer({ pointerType: "touch" })).toBe(false);
  });

  it("still lets a mouse draw — the desktop reader has no stylus at all", () => {
    expect(isDrawingPointer({ pointerType: "mouse" })).toBe(true);
  });

  it("lets a finger through when 手指书写 is explicitly on", () => {
    expect(isDrawingPointer({ pointerType: "touch" }, true)).toBe(true);
  });

  it("does not change what a pen may do when the override is on", () => {
    expect(isDrawingPointer({ pointerType: "pen" }, true)).toBe(true);
  });
});

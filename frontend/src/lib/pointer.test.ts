import { describe, expect, it } from "vitest";

import { isDrawingPointer, isStylus } from "./pointer";

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

  it("lets an eraser-type pointer mark the page — it is still a stylus", () => {
    expect(isDrawingPointer({ pointerType: "eraser" })).toBe(true);
  });
});

describe("isStylus", () => {
  it("recognises a plain pen", () => {
    expect(isStylus({ pointerType: "pen" })).toBe(true);
  });

  /* The one that mattered. Android reports a stylus with its barrel button
     held as TOOL_TYPE_ERASER, and Chromium forwards that as pointerType
     "eraser" — not as a "pen" with a button bit. Every `=== "pen"` gate in
     InkLayer therefore rejected the exact events the S Pen feature needed,
     and the press fell through to the mouse branch and drew a line. */
  it("recognises an eraser-type pointer as a stylus", () => {
    expect(isStylus({ pointerType: "eraser" })).toBe(true);
  });

  it("is not a finger and not a mouse", () => {
    expect(isStylus({ pointerType: "touch" })).toBe(false);
    expect(isStylus({ pointerType: "mouse" })).toBe(false);
  });
});

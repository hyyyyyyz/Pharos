import { describe, expect, it } from "vitest";

import { MIN_DRAG_LENGTH, MIN_TAPE_SIZE, tapeFromPath, tapeFromDrag } from "./tape";

describe("tapeFromDrag", () => {
  it("builds a horizontal strip from a horizontal drag", () => {
    const r = tapeFromDrag(0, 10, 40, 10, 6);
    expect(r).toEqual({ x: 20, y: 10, w: 40, h: 6, angle: 0 });
  });

  it("computes the segment's own angle for a diagonal drag", () => {
    const r = tapeFromDrag(0, 0, 10, 10, 4);
    expect(r.angle).toBeCloseTo(45);
    expect(r.w).toBeCloseTo(Math.hypot(10, 10));
  });

  it("floors a near-zero-length drag so the strip stays selectable", () => {
    const r = tapeFromDrag(5, 5, 5.0001, 5, 6);
    expect(r.w).toBeGreaterThanOrEqual(MIN_DRAG_LENGTH);
  });

  it("centres the strip at the drag's midpoint regardless of direction", () => {
    const r = tapeFromDrag(10, 20, -10, 0, 6);
    expect(r.x).toBeCloseTo(0);
    expect(r.y).toBeCloseTo(10);
  });

  /* The server refuses anything under MIN_SIZE, and a drag qualifies as a
     drag after 4 CSS pixels — which at any zoom past 1 is fewer than 4 PDF
     points. Unclamped, those strips were POSTed and 422'd, so the strip the
     reader watched appear simply never existed. */
  it("clamps a short drag up into the size range the server accepts", () => {
    const r = tapeFromDrag(5, 5, 7, 5, 6);
    expect(r.w).toBeGreaterThanOrEqual(MIN_TAPE_SIZE);
  });

  it("clamps a thickness measured off a small text line at high zoom", () => {
    const r = tapeFromDrag(0, 0, 40, 0, 1.5);
    expect(r.h).toBeGreaterThanOrEqual(MIN_TAPE_SIZE);
  });
});

describe("tapeFromPath (freehand strips)", () => {
  /* The regression this file exists for: `h` is the strip's THICKNESS, and
     `TapePaths` strokes the path at exactly that. When `h` held the path's
     bounding-box height instead, a squiggle 100pt tall was stroked 100pt
     wide — a blob, not a ribbon ("随意涂功能有问题"). */
  it("puts the THICKNESS in h, not the path's bounding height", () => {
    const r = tapeFromPath(
      [
        { x: 0, y: 0 },
        { x: 10, y: 60 },
      ],
      6,
    );
    expect(r.h).toBe(6);
  });

  it("measures w along the path — how much tape was laid down", () => {
    const r = tapeFromPath(
      [
        { x: 0, y: 0 },
        { x: 30, y: 0 },
        { x: 30, y: 40 },
      ],
      6,
    );
    expect(r.w).toBeCloseTo(70);
  });

  it("centres on the path's bounding box", () => {
    const r = tapeFromPath(
      [
        { x: 0, y: 0 },
        { x: 10, y: 4 },
      ],
      6,
    );
    expect(r.x).toBeCloseTo(5);
    expect(r.y).toBeCloseTo(2);
  });

  it("is never rotated — the path carries its own direction", () => {
    const r = tapeFromPath(
      [
        { x: 0, y: 0 },
        { x: 10, y: 10 },
      ],
      2,
    );
    expect(r.angle).toBe(0);
  });

  it("clamps a very long squiggle into the size range the server accepts", () => {
    const path = Array.from({ length: 400 }, (_, i) => ({ x: i * 20, y: 0 }));
    const r = tapeFromPath(path, 6);
    expect(r.w).toBeLessThanOrEqual(2000);
  });

  it("survives an empty path rather than producing NaN bounds", () => {
    const r = tapeFromPath([], 6);
    expect(Number.isFinite(r.x)).toBe(true);
    expect(Number.isFinite(r.w)).toBe(true);
    expect(r.w).toBeGreaterThanOrEqual(MIN_DRAG_LENGTH);
  });
});

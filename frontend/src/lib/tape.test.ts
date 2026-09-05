import { describe, expect, it } from "vitest";

import { MIN_DRAG_LENGTH, tapeBoundsOfPath, tapeFromDrag } from "./tape";

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
});

describe("tapeBoundsOfPath (freehand strips)", () => {
  it("centres the box on the path and pads it by the stroke's own thickness", () => {
    const r = tapeBoundsOfPath(
      [
        { x: 0, y: 0 },
        { x: 10, y: 4 },
      ],
      6,
    );
    expect(r.x).toBeCloseTo(5);
    expect(r.y).toBeCloseTo(2);
    expect(r.w).toBeCloseTo(16); // 10 spanned + 6 of thickness
    expect(r.h).toBeCloseTo(10); // 4 spanned + 6 of thickness
  });

  it("is never rotated — the path carries its own direction", () => {
    const r = tapeBoundsOfPath(
      [
        { x: 0, y: 0 },
        { x: 10, y: 10 },
      ],
      2,
    );
    expect(r.angle).toBe(0);
  });

  it("survives an empty path rather than producing NaN bounds", () => {
    const r = tapeBoundsOfPath([], 6);
    expect(Number.isFinite(r.x)).toBe(true);
    expect(Number.isFinite(r.w)).toBe(true);
    expect(r.w).toBeGreaterThanOrEqual(MIN_DRAG_LENGTH);
  });
});

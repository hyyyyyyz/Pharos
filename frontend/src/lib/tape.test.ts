import { describe, expect, it } from "vitest";

import { MIN_DRAG_LENGTH, tapeFromDrag } from "./tape";

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

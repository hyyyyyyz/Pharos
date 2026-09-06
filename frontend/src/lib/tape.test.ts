import { describe, expect, it } from "vitest";

import { MIN_DRAG_LENGTH, MIN_TAPE_SIZE, tapeAlongAxis, tapeFromDrag } from "./tape";

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

describe("tapeAlongAxis (直胶条: snap to the line, then hold it)", () => {
  /* A hand dragging along a paragraph is never level to the degree. Without
     the snap, every strip inherited that tremor and needed 拉直 afterwards. */
  it("snaps a near-horizontal drag to exactly level", () => {
    const { rect, axis } = tapeAlongAxis(0, 100, 80, 106, 14, null);
    expect(axis).toBe("x");
    expect(rect.angle).toBe(0);
    expect(rect.w).toBeCloseTo(80);
    expect(rect.y).toBeCloseTo(100); // the 6pt of wobble is discarded
  });

  it("snaps a near-vertical drag to exactly upright", () => {
    const { rect, axis } = tapeAlongAxis(50, 0, 55, 90, 14, null);
    expect(axis).toBe("y");
    expect(Math.abs(rect.angle)).toBeCloseTo(90);
    expect(rect.x).toBeCloseTo(50);
  });

  it("leaves a deliberately diagonal drag at its own angle", () => {
    const { rect, axis } = tapeAlongAxis(0, 0, 60, 60, 14, null);
    expect(axis).toBe(null);
    expect(rect.angle).toBeCloseTo(45);
  });

  it("has no direction at all until the drag has actually travelled", () => {
    const { axis } = tapeAlongAxis(0, 0, 2, 1, 14, null);
    expect(axis).toBe(null);
  });

  /* The hysteresis. Without it the axis flips the moment |dy| creeps past
     |dx| midway through a long sweep, which is the twitchiness the lock
     exists to remove. */
  it("holds a locked axis through wobble inside the strip's own width", () => {
    const { rect, axis } = tapeAlongAxis(0, 100, 200, 106, 14, "x");
    expect(axis).toBe("x"); // 6pt off-axis, strip is 14 wide: still inside
    expect(rect.y).toBeCloseTo(100);
  });

  /* "除非笔尖超出胶条宽边界，后可变方向" — leaving the strip releases it. */
  it("releases the lock once the pen leaves the strip's width", () => {
    // 80pt off the axis of a 14pt strip: the lock is gone, and the travel is
    // now overwhelmingly vertical, so that is the new direction.
    const { axis } = tapeAlongAxis(0, 100, 10, 180, 14, "x");
    expect(axis).toBe("y");
  });

  it("releases to a FREE angle when the escape is diagonal", () => {
    // Equal travel both ways is neither horizontal nor vertical, and the rule
    // only pins a direction that IS one of those.
    const { rect, axis } = tapeAlongAxis(0, 100, 40, 140, 14, "x");
    expect(axis).toBe(null);
    expect(rect.angle).toBeCloseTo(45);
  });

  it("re-locks to the same axis when the movement still says so", () => {
    // Far off-axis, but still overwhelmingly horizontal travel.
    const { axis } = tapeAlongAxis(0, 100, 400, 112, 14, "x");
    expect(axis).toBe("x");
  });

  it("clamps into the size range the server accepts", () => {
    const { rect } = tapeAlongAxis(0, 0, 2, 0, 1.5, "x");
    expect(rect.w).toBeGreaterThanOrEqual(MIN_TAPE_SIZE);
    expect(rect.h).toBeGreaterThanOrEqual(MIN_TAPE_SIZE);
  });
});

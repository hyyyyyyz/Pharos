import { describe, expect, it } from "vitest";

import {
  distToSegment,
  pointToCss,
  pointToPdf,
  sampleWidth,
  strokeNear,
  strokeSegments,
} from "./ink";

const PAGE_H = 792; // US Letter, in points
const SCALE = 1.6;

describe("coordinate conversion", () => {
  // The node test environment has no DOMRect; `pointToPdf` only reads
  // left/top, so a literal with the right shape is enough.
  const origin = { left: 100, top: 50 } as DOMRect;

  it("round-trips client pixels through PDF space exactly", () => {
    const client = { x: 100 + 61.4 * SCALE, y: 50 + 300 * SCALE };
    const pdf = pointToPdf(client.x, client.y, origin, SCALE, PAGE_H);
    const css = pointToCss(pdf, SCALE, PAGE_H);
    expect(css.left).toBeCloseTo(61.4 * SCALE, 6);
    expect(css.top).toBeCloseTo(300 * SCALE, 6);
  });

  it("flips the y origin: PDF bottom-left vs CSS top-left", () => {
    // A point at the very top of the page has PDF y == pageHeight.
    const pdf = pointToPdf(100, 50, origin, SCALE, PAGE_H);
    expect(pdf.x).toBeCloseTo(0, 6);
    expect(pdf.y).toBeCloseTo(PAGE_H, 6);
    // …and a point 792*scale px below the top is PDF y == 0.
    const bottom = pointToPdf(100, 50 + PAGE_H * SCALE, origin, SCALE, PAGE_H);
    expect(bottom.y).toBeCloseTo(0, 6);
  });

  it("divides by the live zoom, so marks survive a zoom change", () => {
    const at1 = pointToPdf(origin.left + 160, origin.top + 160, origin, 1, PAGE_H);
    const at2 = pointToPdf(origin.left + 320, origin.top + 320, origin, 2, PAGE_H);
    expect(at1.x).toBeCloseTo(at2.x, 6);
    expect(at1.y).toBeCloseTo(at2.y, 6);
  });
});

describe("sampleWidth", () => {
  it("makes the pressureless case exactly the chosen width", () => {
    expect(sampleWidth(4, 0.5)).toBe(4); // mouse / trackpad -> the promised width
    expect(sampleWidth(4, 0)).toBeCloseTo(2, 6); // feather-light: half, still legible
    expect(sampleWidth(4, 1)).toBeCloseTo(6, 6); // full press: 1.5x
    expect(sampleWidth(4, 0)).toBeLessThan(sampleWidth(4, 0.5));
    expect(sampleWidth(4, 1)).toBeGreaterThan(sampleWidth(4, 0.5));
  });

  it("clamps out-of-range pressure instead of thinning to zero", () => {
    expect(sampleWidth(4, -3)).toBe(sampleWidth(4, 0));
    expect(sampleWidth(4, 9)).toBe(sampleWidth(4, 1));
  });
});

describe("strokeSegments", () => {
  it("produces lead-in, interior curves, and a lead-out", () => {
    const pts = [
      { x: 0, y: 0, p: 0.5 },
      { x: 10, y: 10, p: 0.5 },
      { x: 20, y: 0, p: 0.5 },
    ];
    const segs = strokeSegments(pts, 2);
    // n samples -> n segments: one lead-in, n-2 interior, one lead-out.
    expect(segs.length).toBe(3);
    expect(segs[0]!.x0).toBe(0);
    expect(segs[0]!.y0).toBe(0);
    expect(segs[2]!.x1).toBe(20);
    expect(segs[2]!.y1).toBe(0);
  });

  it("curves through the samples, not the midpoints, so ink stays under the pen", () => {
    const pts = [
      { x: 0, y: 0, p: 0.5 },
      { x: 10, y: 20, p: 0.5 },
      { x: 20, y: 0, p: 0.5 },
    ];
    const segs = strokeSegments(pts, 2);
    expect(segs[1]!.cx).toBe(10);
    expect(segs[1]!.cy).toBe(20);
  });

  it("keeps the endpoints fixed (the lead pieces are straight)", () => {
    const pts = [
      { x: 1, y: 2, p: 0.5 },
      { x: 3, y: 4, p: 0.5 },
    ];
    const segs = strokeSegments(pts, 2);
    expect(segs[0]!.x0).toBe(1);
    expect(segs[segs.length - 1]!.x1).toBe(3);
  });

  it("returns nothing for a single-sample stroke — dots are filled circles", () => {
    expect(strokeSegments([{ x: 5, y: 5, p: 0.5 }], 2)).toEqual([]);
  });
});

describe("strokeNear (the eraser's hit test)", () => {
  const line = [
    { x: 0, y: 0, p: 0.5 },
    { x: 100, y: 0, p: 0.5 },
  ];

  it("hits the centreline", () => {
    expect(strokeNear(line, 2, 50, 1, 4)).toBe(true);
  });

  it("misses clear of the stroke", () => {
    expect(strokeNear(line, 2, 50, 20, 4)).toBe(false);
  });

  it("folds the stroke's own half-width into the tolerance", () => {
    // A 4pt stroke's edge sits 2pt from its centreline, so with tolerance 0 a
    // point 1.5pt away is a hit, one 3pt away is a miss, 8pt a clear miss.
    expect(strokeNear(line, 4, 50, 1.5, 0)).toBe(true);
    expect(strokeNear(line, 4, 50, 3, 0)).toBe(false);
    expect(strokeNear(line, 4, 50, 8, 0)).toBe(false);
  });

  it("respects the segment ends — beyond the pen lift is a miss", () => {
    expect(strokeNear(line, 2, 130, 0, 4)).toBe(false);
    expect(strokeNear(line, 2, 103, 0, 4)).toBe(true);
  });
});

describe("distToSegment", () => {
  it("measures to the nearest point, not the infinite line", () => {
    expect(distToSegment(5, 5, 0, 0, 10, 0)).toBe(5);
    expect(distToSegment(-5, 0, 0, 0, 10, 0)).toBe(5); // clamped to the end
    expect(distToSegment(15, 0, 0, 0, 10, 0)).toBe(5);
  });
});

import { describe, expect, it } from "vitest";

import {
  INK_COLORS,
  distToSegment,
  pointInPolygon,
  pointToCss,
  pointToPdf,
  rankInkColors,
  sampleWidth,
  splitStroke,
  strokeBounds,
  strokeCaughtBy,
  strokeNear,
  strokeSegments,
  translatePoints,
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

/* ----------------------------------------------------------- lasso select */

describe("pointInPolygon", () => {
  const square = [
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 10, y: 10 },
    { x: 0, y: 10 },
  ];

  it("accepts points inside and rejects points outside", () => {
    expect(pointInPolygon(5, 5, square)).toBe(true);
    expect(pointInPolygon(15, 5, square)).toBe(false);
    expect(pointInPolygon(5, -5, square)).toBe(false);
  });

  it("closes the loop implicitly (last vertex back to first)", () => {
    // Left edge runs from (0,10) to (0,0): a point just left of it is outside
    // even though the pair (i,j) that straddles it is the implicit closing one.
    expect(pointInPolygon(-1, 5, square)).toBe(false);
    expect(pointInPolygon(0.5, 5, square)).toBe(true);
  });

  it("is sane on a concave loop", () => {
    // A square whose top edge dips down to (5,4): the wedge between the two
    // slanted edges is outside, the strip beside it is inside.
    const notch = [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
      { x: 5, y: 4 },
      { x: 0, y: 10 },
    ];
    expect(pointInPolygon(0.5, 8, notch)).toBe(true); // beside the wedge
    expect(pointInPolygon(5, 8, notch)).toBe(false); // inside the wedge cut
    expect(pointInPolygon(5, 2, notch)).toBe(true); // below the notch tip
  });

  it("degenerate loops contain nothing", () => {
    expect(pointInPolygon(1, 1, [])).toBe(false);
    expect(pointInPolygon(1, 1, [{ x: 0, y: 0 }, { x: 2, y: 2 }])).toBe(false);
  });
});

describe("strokeCaughtBy (the lasso's stroke test)", () => {
  const line = [
    { x: 0, y: 50, p: 0.5 },
    { x: 100, y: 50, p: 0.5 },
  ];
  // A loop that crosses the line's middle but holds no endpoint.
  const crossing = [
    { x: 40, y: 40 },
    { x: 60, y: 40 },
    { x: 60, y: 60 },
    { x: 40, y: 60 },
  ];

  it("catches a stroke the loop merely crosses", () => {
    expect(strokeCaughtBy(line, crossing)).toBe(true);
  });

  it("misses strokes clear of the loop", () => {
    const far = line.map((p) => ({ ...p, y: 200 }));
    expect(strokeCaughtBy(far, crossing)).toBe(false);
  });

  it("requires an actual loop", () => {
    expect(strokeCaughtBy(line, crossing.slice(0, 2))).toBe(false);
  });
});

describe("translatePoints", () => {
  it("shifts geometry and preserves pressure", () => {
    const out = translatePoints(
      [
        { x: 1, y: 2, p: 0.3 },
        { x: 3, y: 4, p: 0.9 },
      ],
      10,
      -5,
    );
    expect(out[0]).toEqual({ x: 11, y: -3, p: 0.3 });
    expect(out[1]).toEqual({ x: 13, y: -1, p: 0.9 });
  });
});

describe("splitStroke (the partial eraser)", () => {
  const line = (n: number): { x: number; y: number; p: number }[] =>
    Array.from({ length: n }, (_, i) => ({ x: i * 10, y: 0, p: 0.5 }));

  it("cuts the middle out and keeps two parts", () => {
    const parts = splitStroke(line(11), 2, 50, 0, 5);
    expect(parts.length).toBe(2);
    expect(parts[0]!.length).toBe(5); // samples 0..4 (x=0..40)
    expect(parts[0]![4]!.x).toBe(40);
    expect(parts[1]![0]!.x).toBe(60);
    expect(parts[1]!.length).toBe(5);
  });

  it("clears the stroke entirely when the reach covers it", () => {
    expect(splitStroke(line(3), 2, 10, 0, 100)).toEqual([]);
  });

  it("leaves an untouched stroke as a single part with identical samples", () => {
    const pts = line(5);
    const parts = splitStroke(pts, 2, 500, 500, 4);
    expect(parts.length).toBe(1);
    expect(parts[0]).toEqual(pts);
  });

  it("cuts at the very end of a stroke", () => {
    const parts = splitStroke(line(5), 2, 42, 0, 4); // hits only the last sample
    expect(parts.length).toBe(1);
    expect(parts[0]!.length).toBe(4);
  });

  it("keeps pressure through the cut", () => {
    const pts = [
      { x: 0, y: 0, p: 0.7 },
      { x: 100, y: 0, p: 0.2 },
    ];
    const parts = splitStroke(pts, 2, 200, 0, 4);
    expect(parts[0]![1]!.p).toBe(0.2);
  });
});

describe("strokeBounds", () => {
  it("wraps exactly the samples", () => {
    const b = strokeBounds([
      { x: 5, y: 10, p: 0.5 },
      { x: 15, y: 2, p: 0.5 },
      { x: 8, y: 6, p: 0.5 },
    ]);
    expect(b).toEqual({ x0: 5, y0: 2, x1: 15, y1: 10 });
  });

  it("returns a zero box, not infinities, for an empty stroke", () => {
    expect(strokeBounds([])).toEqual({ x0: 0, y0: 0, x1: 0, y1: 0 });
  });
});

describe("rankInkColors (the quick-bar ranking)", () => {
  const NOW = 1_700_000_000_000;

  it("pins the palette's first colour (ink/black) in front with no history", () => {
    expect(rankInkColors(INK_COLORS, {}, NOW)[0]).toBe("ink");
  });

  it("falls back to the palette's own order with no usage history — day one looks like today", () => {
    expect(rankInkColors(INK_COLORS, {}, NOW)).toEqual(["ink", "red", "amber", "brown"]);
  });

  it("ranks the most-used colour first, ahead of the palette order", () => {
    const usage = {
      pink: { count: 9, last: NOW },
      teal: { count: 1, last: NOW },
    };
    const ranked = rankInkColors(INK_COLORS, usage, NOW);
    expect(ranked[1]).toBe("pink");
  });

  it("decays an old count enough to fall behind a smaller but recent one", () => {
    const twoDaysAgo = NOW - 2 * 24 * 3_600_000;
    const usage = {
      // Used 3 times, but two half-lives ago: score decays to 3 * 0.25 = 0.75.
      purple: { count: 3, last: twoDaysAgo },
      // Used once, just now: score stays 1 — no decay yet.
      teal: { count: 1, last: NOW },
    };
    const ranked = rankInkColors(INK_COLORS, usage, NOW);
    // Raw counts alone would put purple ahead (3 > 1); decay flips it.
    expect(ranked.indexOf("teal")).toBeLessThan(ranked.indexOf("purple"));
  });

  it("never lets more than one colour past the first occupy the same slot", () => {
    const usage = { red: { count: 5, last: NOW }, amber: { count: 5, last: NOW } };
    const ranked = rankInkColors(INK_COLORS, usage, NOW);
    expect(new Set(ranked).size).toBe(ranked.length);
  });
});

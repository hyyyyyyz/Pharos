import { describe, expect, it } from "vitest";

import {
  INK_COLORS,
  WATER_COLORS,
  distToSegment,
  isWaterColor,
  outlineArea,
  paintInk,
  pathLength,
  pointInPolygon,
  pointToCss,
  pointToPdf,
  rankInkColors,
  rotatePoints,
  sampleWidth,
  scalePoints,
  splitStroke,
  strokeBounds,
  strokeCaughtBy,
  strokeNear,
  strokeOutline,
  strokeSegments,
  translatePoints,
  unionBounds,
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

describe("scalePoints (the lasso resize handle)", () => {
  it("doubles distance from the pivot in both axes, pressure untouched", () => {
    const out = scalePoints([{ x: 12, y: 14, p: 0.4 }], 10, 10, 2);
    expect(out[0]).toEqual({ x: 14, y: 18, p: 0.4 });
  });

  it("leaves the pivot itself fixed under any factor", () => {
    const out = scalePoints([{ x: 10, y: 10, p: 0.5 }], 10, 10, 3);
    expect(out[0]).toEqual({ x: 10, y: 10, p: 0.5 });
  });

  it("floors a collapsing or inverting factor rather than trust a live drag", () => {
    const out = scalePoints([{ x: 12, y: 10, p: 0.5 }], 10, 10, -4);
    // factor floored to 0.05: 10 + (12-10)*0.05 = 10.1, never negative/flipped.
    expect(out[0]!.x).toBeCloseTo(10.1);
  });
});

describe("rotatePoints (the lasso rotate handle)", () => {
  it("rotates a point a quarter turn about the pivot", () => {
    const out = rotatePoints([{ x: 11, y: 10, p: 0.5 }], 10, 10, Math.PI / 2);
    expect(out[0]!.x).toBeCloseTo(10);
    expect(out[0]!.y).toBeCloseTo(11);
  });

  it("leaves the pivot itself fixed", () => {
    const out = rotatePoints([{ x: 5, y: 5, p: 0.5 }], 5, 5, 1.234);
    expect(out[0]!.x).toBeCloseTo(5);
    expect(out[0]!.y).toBeCloseTo(5);
  });

  it("a full turn returns to the start", () => {
    const out = rotatePoints([{ x: 3, y: 7, p: 0.5 }], 1, 1, Math.PI * 2);
    expect(out[0]!.x).toBeCloseTo(3);
    expect(out[0]!.y).toBeCloseTo(7);
  });
});

describe("isWaterColor (the pen/watercolour rendering split)", () => {
  it("recognises every WATER_COLORS token", () => {
    for (const c of WATER_COLORS) expect(isWaterColor(c.key)).toBe(true);
  });

  it("rejects every regular INK_COLORS token", () => {
    for (const c of INK_COLORS) expect(isWaterColor(c.key)).toBe(false);
  });
});

describe("unionBounds (the multi-stroke selection box)", () => {
  it("wraps several boxes in the smallest box containing them all", () => {
    const b = unionBounds([
      { x0: 0, y0: 0, x1: 5, y1: 5 },
      { x0: 3, y0: -2, x1: 8, y1: 4 },
    ]);
    expect(b).toEqual({ x0: 0, y0: -2, x1: 8, y1: 5 });
  });

  it("returns a zero box for an empty selection rather than +/-Infinity", () => {
    expect(unionBounds([])).toEqual({ x0: 0, y0: 0, x1: 0, y1: 0 });
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

/* ---------------------------------------------------------------- dots */
/*
   "画点的时候容易识别不出来" — a dot drawn with the pen very often did not
   appear. Not a capture bug: the tap WAS recorded and stored. It was a
   rendering bug, and a precisely measurable one.

   `perfect-freehand`'s start/end tapers are lengths along the stroke. A dot
   is a stroke of length ~0, the round-3 tapers were 2 and 4 points, and so
   the whole mark was tapered away — leaving a polygon with three vertices
   and an area of exactly zero. Three vertices sails past a `length >= 3`
   guard, so the renderer dutifully filled nothing at all.

   The two fixes are tested separately below because they fail separately:
   the tapers are now capped against the stroke's own length, and the
   fallback to a dab triggers on AREA rather than on vertex count.
*/

/** A minimal 2d context recorder — enough to see which primitive was used
 *  and how big it was. jsdom has no canvas, and this needs no pixels.
 *  Counters live on an object, not as destructured numbers: a number is
 *  copied out and would report whatever it was before the call under test. */
function recordingCtx(): {
  ctx: CanvasRenderingContext2D;
  arcs: { x: number; y: number; r: number }[];
  n: { fills: number; lineTos: number };
} {
  const arcs: { x: number; y: number; r: number }[] = [];
  const n = { fills: 0, lineTos: 0 };
  const ctx = {
    fillStyle: "",
    beginPath: () => undefined,
    closePath: () => undefined,
    moveTo: () => undefined,
    lineTo: () => {
      n.lineTos++;
    },
    arc: (x: number, y: number, r: number) => {
      arcs.push({ x, y, r });
    },
    fill: () => {
      n.fills++;
    },
  } as unknown as CanvasRenderingContext2D;
  return { ctx, arcs, n };
}

describe("pathLength", () => {
  it("is zero for a single sample", () => {
    expect(pathLength([{ x: 5, y: 5, p: 0.5 }])).toBe(0);
  });

  it("sums the polyline, not the straight-line distance end to end", () => {
    const l = pathLength([
      { x: 0, y: 0, p: 0.5 },
      { x: 3, y: 0, p: 0.5 },
      { x: 3, y: 4, p: 0.5 },
    ]);
    expect(l).toBeCloseTo(7); // 3 + 4, not the 5 of the hypotenuse
  });
});

describe("outlineArea", () => {
  it("is zero for a degenerate outline, whatever its vertex count", () => {
    // The exact shape a tap produced: three vertices, no area. This is the
    // value the old `length >= 3` guard could not see.
    expect(outlineArea([[0, 0], [1, 1], [2, 2]])).toBeCloseTo(0);
  });

  it("measures a real polygon regardless of winding direction", () => {
    const square: [number, number][] = [[0, 0], [10, 0], [10, 10], [0, 10]];
    const reversed = [...square].reverse();
    expect(outlineArea(square)).toBeCloseTo(100);
    // The canvas transform flips y, which reverses winding; a fill does not
    // care, so neither does this.
    expect(outlineArea(reversed)).toBeCloseTo(100);
  });
});

describe("strokeOutline taper capping", () => {
  it("leaves a tap with a real, fillable outline", () => {
    // Before: 3 vertices, area 0.000. The taper could consume the whole
    // stroke because nothing bounded it by the stroke's own length.
    const outline = strokeOutline([{ x: 100, y: 100, p: 0.5 }], 2);
    expect(outlineArea(outline)).toBeGreaterThan(0);
  });

  it("still tapers a long stroke — the ends are what make it read as ink", () => {
    const long = Array.from({ length: 20 }, (_, i) => ({ x: 100 + i * 3, y: 100, p: 0.5 }));
    const outline = strokeOutline(long, 2);
    // A 57pt run at width 2 would be ~114 if it were a plain bar; the tapers
    // take a visible bite out of it without erasing it.
    const area = outlineArea(outline);
    expect(area).toBeGreaterThan(100);
    expect(area).toBeLessThan(200);
  });
});

describe("paintInk (the one place a stroke's appearance is decided)", () => {
  it("draws a tap as a dab of the nib's own width", () => {
    const { ctx, arcs, n } = recordingCtx();
    paintInk(ctx, [{ x: 30, y: 40, p: 0.5 }], 2, "#000");
    expect(n.fills).toBe(1);
    expect(arcs).toHaveLength(1);
    expect(arcs[0]!.x).toBeCloseTo(30);
    expect(arcs[0]!.y).toBeCloseTo(40);
    // sampleWidth(2, 0.5) / 2 = 1. NOT perfect-freehand's own single-point
    // circle, which measures radius 2.5 for the same 2pt nib — a dot has to
    // be exactly as wide as the line the same pen draws.
    expect(arcs[0]!.r).toBeCloseTo(1);
  });

  it("draws a wash dab at the wash's own width", () => {
    const { ctx, arcs } = recordingCtx();
    paintInk(ctx, [{ x: 0, y: 0, p: 0.5 }], 14, "#fc0");
    expect(arcs[0]!.r).toBeCloseTo(7);
  });

  it("centres the dab on a jittered tap rather than on its first sample", () => {
    const { ctx, arcs } = recordingCtx();
    paintInk(
      ctx,
      [
        { x: 10, y: 10, p: 0.5 },
        { x: 10.4, y: 10.2, p: 0.5 },
        { x: 10.6, y: 10.1, p: 0.5 },
      ],
      2,
      "#000",
    );
    expect(arcs).toHaveLength(1);
    expect(arcs[0]!.x).toBeCloseTo(10.3);
  });

  it("draws a real stroke as an outline, not a dab", () => {
    const { ctx, arcs, n } = recordingCtx();
    const long = Array.from({ length: 20 }, (_, i) => ({ x: 100 + i * 3, y: 100, p: 0.5 }));
    paintInk(ctx, long, 2, "#000");
    expect(arcs).toHaveLength(0);
    expect(n.lineTos).toBeGreaterThan(10);
  });

  it("draws nothing at all for no samples", () => {
    const { ctx, n } = recordingCtx();
    paintInk(ctx, [], 2, "#000");
    expect(n.fills).toBe(0);
  });

  it("honours pressure in the dab's size", () => {
    const light = recordingCtx();
    const heavy = recordingCtx();
    paintInk(light.ctx, [{ x: 0, y: 0, p: 0.1 }], 4, "#000");
    paintInk(heavy.ctx, [{ x: 0, y: 0, p: 1 }], 4, "#000");
    expect(heavy.arcs[0]!.r).toBeGreaterThan(light.arcs[0]!.r);
  });
});

/**
 * Ink geometry — the pure half of the handwriting feature.
 *
 * Everything here works in **PDF user space: points at scale 1, origin at the
 * page's bottom-left** — the exact convention `HighlightLayer.tsx` uses for
 * highlight rects, and for the same reason: the reader zooms, devices disagree
 * about pixel density, and anything captured in screen coordinates lands in
 * the right place exactly once. The canvas-side renderer receives these points
 * and a transform; the transport stores them verbatim.
 *
 * Pressure rides on every sample (0..1). Devices without a digitiser report
 * 0.5, which the width mapping turns into the plain stroke width — so a mouse
 * draws the line the pen's medium pressure would, never a hairline.
 *
 * Stroke *rendering* is outline-based (via `perfect-freehand`, the library
 * tldraw and Excalidraw draw with): a stroke becomes a filled polygon whose
 * width breathes with pressure, with round caps and input streamlining. That
 * is what makes ink look like ink — per-segment `lineWidth` stamping, the
 * cheap alternative, leaves bulges wherever pressure changes.
 */
import { getStroke } from "perfect-freehand";
import type { InkPoint, InkStrokeRow } from "../api/types";

/** Client-space point -> PDF user space. The exact inverse of `pointToCss`. */
export function pointToPdf(
  clientX: number,
  clientY: number,
  origin: DOMRect,
  scale: number,
  pageHeight: number,
  pressure = 0.5,
): InkPoint {
  return {
    x: (clientX - origin.left) / scale,
    y: pageHeight - (clientY - origin.top) / scale,
    // PointerEvent.pressure is 0.5 for every pointer backend that cannot
    // measure it; 0 would be "pen hovering", which never starts a stroke.
    p: pressure === 0 ? 0.5 : pressure,
  };
}

/** PDF user space -> CSS pixels within the page box. The inverse of `pointToPdf`. */
export function pointToCss(
  p: InkPoint,
  scale: number,
  pageHeight: number,
): { left: number; top: number } {
  return {
    left: p.x * scale,
    top: (pageHeight - p.y) * scale,
  };
}

/**
 * Stroke width for one sample, in PDF points.
 *
 * Mapped so the **pressureless case (p = 0.5) is exactly the chosen width** —
 * a mouse or trackpad draws the line the width picker promised, and pressure
 * modulates around it: a feather-light touch halves it (still legible), a full
 * press makes it 1.5× (clearly heavier, never a blob).
 */
export function sampleWidth(baseWidth: number, pressure: number): number {
  const clamped = Math.max(0, Math.min(1, pressure));
  return baseWidth * (0.5 + clamped);
}

/* ------------------------------------------------------------------ outline */

/** Tuning of the outline generator. Values are perfect-freehand defaults-ish,
 *  chosen so pressure dominates the width and input jitter is absorbed. */
const OUTLINE_OPTIONS = {
  size: 2, // overridden per call
  thinning: 0.6,
  smoothing: 0.5,
  streamline: 0.45,
  easing: (t: number): number => t,
  simulatePressure: false, // real digitiser pressure only
  cap: true,
};

/**
 * The filled outline of a stroke, in PDF space — `getStroke` over the samples.
 *
 * `live` keeps the outline open-ended (no end taper) for the in-progress
 * gesture; a finished stroke tapers its tail so the pen lift looks like a
 * pen lift.
 */
export function strokeOutline(
  points: InkPoint[],
  width: number,
  live = false,
): [number, number][] {
  if (points.length === 0) return [];
  return getStroke(
    points.map((p) => [p.x, p.y, p.p]),
    { ...OUTLINE_OPTIONS, size: width, last: !live },
  );
}

/** Fill one outline. The transform owns scale and the y-flip; winding order
 *  is irrelevant to the fill, so the flip is safe. */
export function paintOutline(
  ctx: CanvasRenderingContext2D,
  outline: [number, number][],
  color: string,
): void {
  if (outline.length < 3) return;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(outline[0]![0], outline[0]![1]);
  for (let i = 1; i < outline.length; i++) ctx.lineTo(outline[i]![0], outline[i]![1]);
  ctx.closePath();
  ctx.fill();
}

/**
 * Paint one stored stroke onto a 2d context already transformed into PDF
 * space (see `InkLayer`'s transform — scale + y-flip live there, not here).
 */
export function paintStroke(
  ctx: CanvasRenderingContext2D,
  stroke: Pick<InkStrokeRow, "points" | "width" | "color">,
  colorResolve: (token: string) => string,
): void {
  if (stroke.points.length === 0) return;
  const color = colorResolve(stroke.color);
  const outline = strokeOutline(stroke.points, stroke.width);
  if (outline.length >= 3) {
    paintOutline(ctx, outline, color);
    return;
  }
  // Degenerate outline (a tap, or samples too close): a dot.
  const p = stroke.points[0]!;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.arc(p.x, p.y, Math.max(0.3, stroke.width / 2), 0, Math.PI * 2);
  ctx.fill();
}

/* --------------------------------------------------------------- eraser hit */

/**
 * Does the eraser at (x, y) touch this stroke?
 *
 * Point-to-segment distance against the stroke's centreline, with the stroke's
 * own half-width folded into the tolerance — an eraser must be able to grab a
 * thin stroke's edge, not only its centreline. Whole-stroke erasing (OneNote's
 * default, and GoodNotes' stroke eraser): a partial erase would have to
 * re-serialise one row as several, and a gesture that deletes half a written
 * word reads as a bug.
 */
export function strokeNear(
  points: InkPoint[],
  baseWidth: number,
  x: number,
  y: number,
  tolerance: number,
): boolean {
  if (points.length === 1) {
    const p = points[0]!;
    return Math.hypot(x - p.x, y - p.y) <= tolerance + sampleWidth(baseWidth, p.p) / 2;
  }
  const segments = strokeSegments(points, baseWidth);
  const reach = tolerance;
  for (const s of segments) {
    const halfW = s.w / 2;
    if (distToSegment(x, y, s.x0, s.y0, s.cx, s.cy) <= reach + halfW) return true;
    if (distToSegment(x, y, s.cx, s.cy, s.x1, s.y1) <= reach + halfW) return true;
  }
  return false;
}

/** Distance from a point to a finite segment (not the infinite line). */
export function distToSegment(
  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number,
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/* ------------------------------------------------------------- lasso select */

/**
 * Ray-casting point-in-polygon. `poly` is a closed loop's vertex list in PDF
 * space; the implicit closing edge (last vertex back to the first) counts.
 */
export function pointInPolygon(x: number, y: number, poly: { x: number; y: number }[]): boolean {
  if (poly.length < 3) return false;
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i]!;
    const b = poly[j]!;
    // The `> 0`/`<= 0` split puts a vertex exactly on the ray inside exactly
    // one of the two edges, so crossings are never double-counted.
    if (a.y > y !== b.y > y) {
      const cx = ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x;
      if (x < cx) inside = !inside;
    }
  }
  return inside;
}

/**
 * Does the lasso loop catch this stroke?
 *
 * "Any part inside" semantics, like every note app's lasso: a loop that merely
 * crosses a stroke selects the whole of it. Each *sample* and the midpoint of
 * each consecutive sample pair are tested — the midpoint matters because a
 * fast pen move can put two samples either side of a thin loop with both
 * endpoints outside it.
 */
export function strokeCaughtBy(
  points: InkPoint[],
  poly: { x: number; y: number }[],
): boolean {
  if (poly.length < 3) return false;
  for (let i = 0; i < points.length; i++) {
    const p = points[i]!;
    if (pointInPolygon(p.x, p.y, poly)) return true;
    if (i > 0) {
      const q = points[i - 1]!;
      if (pointInPolygon((p.x + q.x) / 2, (p.y + q.y) / 2, poly)) return true;
    }
  }
  return false;
}

/** Translate every sample by (dx, dy) in PDF space, pressures untouched. */
export function translatePoints(
  points: InkPoint[],
  dx: number,
  dy: number,
): InkPoint[] {
  return points.map((p) => ({ x: p.x + dx, y: p.y + dy, p: p.p }));
}

/** Scale every sample toward/away from a pivot (typically the selection's own
 *  centre), uniformly on both axes — a stroke keeps its shape, only its size
 *  changes. `factor` <= 0 would collapse or invert the stroke, so it is
 *  floored well above zero rather than trusted from a live drag. */
export function scalePoints(
  points: InkPoint[],
  cx: number,
  cy: number,
  factor: number,
): InkPoint[] {
  const f = Math.max(0.05, factor);
  return points.map((p) => ({ x: cx + (p.x - cx) * f, y: cy + (p.y - cy) * f, p: p.p }));
}

/** Rotate every sample about a pivot by `radians` (positive = counter-
 *  clockwise, matching PDF space's own bottom-left-origin, right-handed
 *  axes — the same convention `pointToPdf`'s y-flip sets up). */
export function rotatePoints(
  points: InkPoint[],
  cx: number,
  cy: number,
  radians: number,
): InkPoint[] {
  const sin = Math.sin(radians);
  const cos = Math.cos(radians);
  return points.map((p) => {
    const dx = p.x - cx;
    const dy = p.y - cy;
    return { x: cx + dx * cos - dy * sin, y: cy + dx * sin + dy * cos, p: p.p };
  });
}

/** The combined bounds of several strokes — the lasso selection's own box,
 *  which a single stroke's `strokeBounds` cannot answer once more than one
 *  stroke is caught. */
export function unionBounds(
  boxes: { x0: number; y0: number; x1: number; y1: number }[],
): { x0: number; y0: number; x1: number; y1: number } {
  if (boxes.length === 0) return { x0: 0, y0: 0, x1: 0, y1: 0 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const b of boxes) {
    if (b.x0 < x0) x0 = b.x0;
    if (b.y0 < y0) y0 = b.y0;
    if (b.x1 > x1) x1 = b.x1;
    if (b.y1 > y1) y1 = b.y1;
  }
  return { x0, y0, x1, y1 };
}

/**
 * Cut a stroke where the partial eraser passes, keeping the surviving pieces.
 *
 * A sample is erased when it sits inside the eraser's reach (its own half
 * width folded in). A *segment* whose interior crosses the reach without an
 * endpoint inside cuts the polyline there too — the two endpoint samples
 * survive on opposite sides, which is exactly what a cut through the middle
 * of a sparsely-sampled span should do. Surviving samples regroup into
 * maximal consecutive runs.
 *
 * This is the geometry half of the 局部 eraser — the caller decides what to
 * do with the parts (persist them, drop single-sample specks, …).
 */
export function splitStroke(
  points: InkPoint[],
  baseWidth: number,
  x: number,
  y: number,
  reach: number,
): InkPoint[][] {
  if (points.length === 0) return [];
  const isHit = (i: number): boolean => {
    const p = points[i]!;
    return Math.hypot(x - p.x, y - p.y) <= reach + sampleWidth(baseWidth, p.p) / 2;
  };
  const parts: InkPoint[][] = [];
  let run: InkPoint[] = [];
  for (let i = 0; i < points.length; i++) {
    const p = points[i]!;
    if (isHit(i)) {
      if (run.length > 0) {
        parts.push(run);
        run = [];
      }
      continue;
    }
    run.push(p);
    // Does the eraser cross the span to the next sample without touching
    // either endpoint? Then the cut falls between them.
    const q = points[i + 1];
    if (
      q &&
      !isHit(i + 1) &&
      distToSegment(x, y, p.x, p.y, q.x, q.y) <= reach + sampleWidth(baseWidth, q.p) / 2
    ) {
      if (run.length > 0) {
        parts.push(run);
        run = [];
      }
    }
  }
  if (run.length > 0) parts.push(run);
  return parts;
}

/** Axis-aligned bounds of a stroke's samples, in PDF space. */
export function strokeBounds(points: InkPoint[]): {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
} {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const p of points) {
    if (p.x < x0) x0 = p.x;
    if (p.y < y0) y0 = p.y;
    if (p.x > x1) x1 = p.x;
    if (p.y > y1) y1 = p.y;
  }
  if (!Number.isFinite(x0)) return { x0: 0, y0: 0, x1: 0, y1: 0 };
  return { x0, y0, x1, y1 };
}

/* ------------------------------------------------------- centreline segments */

/**
 * One drawable piece of a stroke centreline, in PDF space, with its own width.
 * Used only for hit-testing now that rendering is outline-based.
 */
export interface Segment {
  x0: number;
  y0: number;
  /** Control point — the raw sample the curve bends through. */
  cx: number;
  cy: number;
  x1: number;
  y1: number;
  w: number;
}

export function strokeSegments(points: InkPoint[], baseWidth: number): Segment[] {
  if (points.length < 2) return [];
  const out: Segment[] = [];
  const widthAt = (i: number): number => sampleWidth(baseWidth, points[i]!.p);

  const firstMid = midpoint(points[0]!, points[1]!);
  out.push({
    x0: points[0]!.x,
    y0: points[0]!.y,
    cx: points[0]!.x,
    cy: points[0]!.y,
    x1: firstMid.x,
    y1: firstMid.y,
    w: widthAt(0),
  });

  for (let i = 1; i < points.length - 1; i++) {
    const from = midpoint(points[i - 1]!, points[i]!);
    const to = midpoint(points[i]!, points[i + 1]!);
    out.push({
      x0: from.x,
      y0: from.y,
      cx: points[i]!.x,
      cy: points[i]!.y,
      x1: to.x,
      y1: to.y,
      w: widthAt(i),
    });
  }

  const last = points.length - 1;
  const lastMid = midpoint(points[last - 1]!, points[last]!);
  out.push({
    x0: lastMid.x,
    y0: lastMid.y,
    cx: points[last]!.x,
    cy: points[last]!.y,
    x1: points[last]!.x,
    y1: points[last]!.y,
    w: widthAt(last),
  });
  return out;
}

function midpoint(a: InkPoint, b: InkPoint): InkPoint {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, p: a.p };
}

/**
 * The full colour palette the backend accepts, in a fixed reference order —
 * also the tie-break, and the fallback order before any colour has usage
 * history (a fresh install's quick bar should look exactly like today's
 * fixed toolbar, not an arbitrary shuffle).
 */
export const INK_COLORS = [
  { key: "ink", label: "墨黑" },
  { key: "red", label: "朱红" },
  { key: "amber", label: "琥珀" },
  { key: "brown", label: "赭石" },
  { key: "green", label: "青绿" },
  { key: "teal", label: "青碧" },
  { key: "blue", label: "湖蓝" },
  { key: "purple", label: "紫罗兰" },
  { key: "pink", label: "绯红" },
  { key: "gray", label: "灰石" },
] as const;

/**
 * 水彩笔 tones — a separate, smaller palette from `INK_COLORS`: these are
 * light washes meant to sit behind text (`mix-blend-mode: multiply`, see
 * `InkLayer.css`'s `.ph-ink-water`), not opaque lines, so mixing them into
 * the pen's own colour picker would offer a pen colour that barely shows up
 * and a wash so dark it hides what it is meant to sit behind. The "wc-"
 * prefix is how the renderer (`InkLayer`) tells which strokes are washes.
 */
export const WATER_COLORS = [
  { key: "wc-amber", label: "琥珀" },
  { key: "wc-green", label: "青绿" },
  { key: "wc-blue", label: "湖蓝" },
  { key: "wc-purple", label: "紫罗兰" },
  { key: "wc-pink", label: "绯红" },
] as const;

/** Every watercolour stroke is tagged this way — the one place that
 *  convention is spelled out, so nothing hardcodes the "wc-" prefix itself. */
export function isWaterColor(token: string): boolean {
  return token.startsWith("wc-");
}

/** How recently, and how often, a colour has been picked — the quick-bar
 *  ranking's raw material. `last` is a `Date.now()` epoch millisecond. */
export interface InkColorUsage {
  count: number;
  last: number;
}

const RECENCY_HALFLIFE_HOURS = 24;

/**
 * The quick-bar order: the palette's first colour (墨黑, "ink") pinned in
 * front always — handwriting is overwhelmingly black or near-black, and
 * ranking it against how often pink got picked would answer the wrong
 * question — then `quickSlots` more from the rest of the palette, by a
 * recency-weighted usage score: a colour picked often but long ago decays
 * toward one picked a little less but just now, on a
 * `RECENCY_HALFLIFE_HOURS` half-life. Everything else stays off the quick
 * bar, in the full palette panel. Ties (most commonly: everything at 0, a
 * fresh install with no history yet) keep the palette's own order.
 */
export function rankInkColors(
  colors: readonly { key: string }[],
  usage: Record<string, InkColorUsage>,
  now: number,
  quickSlots = 3,
): string[] {
  const [pinned, ...rest] = colors;
  if (!pinned) return [];
  const scored = rest.map((c, i) => {
    const u = usage[c.key];
    const hours = u ? Math.max(0, (now - u.last) / 3_600_000) : 0;
    const score = u ? u.count * Math.pow(0.5, hours / RECENCY_HALFLIFE_HOURS) : 0;
    return { key: c.key, score, i };
  });
  scored.sort((a, b) => b.score - a.score || a.i - b.i);
  return [pinned.key, ...scored.slice(0, quickSlots).map((s) => s.key)];
}

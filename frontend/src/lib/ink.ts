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

/** The color tokens the backend accepts, in toolbar order. */
export const INK_COLORS = [
  { key: "ink", label: "墨黑" },
  { key: "amber", label: "琥珀" },
  { key: "green", label: "青绿" },
  { key: "blue", label: "湖蓝" },
  { key: "pink", label: "绯红" },
  { key: "purple", label: "紫罗兰" },
] as const;

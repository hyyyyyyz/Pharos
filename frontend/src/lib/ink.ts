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
 */
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

/**
 * One drawable piece of a stroke, in PDF space, with its own width.
 *
 * A stroke is rendered as several quadratic pieces rather than one path
 * because the width varies with pressure, and a path carries a single
 * `lineWidth`. Splitting at the midpoints and curving through the sample
 * between them is the standard pen-smoothing trick: it rounds the corners
 * without moving the line away from where the pen actually was.
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

  // Lead-in: a straight half-segment from the pen-down point to the first
  // midpoint. Without it the stroke would visually start one midpoint late.
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

  // Lead-out: mirror of the lead-in, so the stroke ends where the pen lifted.
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
 * Does the eraser at (x, y) touch this stroke?
 *
 * Point-to-segment distance against every drawable piece, with the stroke's
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

/**
 * Paint one stroke onto a 2d context that is already transformed into PDF
 * space (see `InkLayer`'s transform — scale + y-flip live there, not here).
 *
 * Dots are filled circles rather than zero-length lines: whether a zero-length
 * segment paints anything depends on the platform's stroke semantics, and a
 * pen tap producing nothing reads as a dead digitiser.
 */
export function paintStroke(
  ctx: CanvasRenderingContext2D,
  stroke: Pick<InkStrokeRow, "points" | "width" | "color">,
  colorResolve: (token: string) => string,
): void {
  const { points, width } = stroke;
  if (points.length === 0) return;
  const color = colorResolve(stroke.color);
  if (points.length === 1) {
    const p = points[0]!;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, sampleWidth(width, p.p) / 2, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  ctx.strokeStyle = color;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (const s of strokeSegments(points, width)) {
    ctx.lineWidth = s.w;
    ctx.beginPath();
    ctx.moveTo(s.x0, s.y0);
    ctx.quadraticCurveTo(s.cx, s.cy, s.x1, s.y1);
    ctx.stroke();
  }
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

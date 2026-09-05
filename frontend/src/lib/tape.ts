/**
 * Tape geometry — the pure half of 胶带.
 *
 * A strip is a rotated rectangle: a CENTRE point, a length (`w`, along its
 * own rotated axis) and a thickness (`h`, across it), and an `angle` in
 * degrees, counter-clockwise from horizontal — the same PDF user-space
 * convention (points at scale 1, origin bottom-left) as ink and highlights.
 * Everything here works in that space; the canvas/DOM transform in
 * `TapeLayer` owns scale and the y-flip, exactly as `lib/ink` documents for
 * strokes.
 */

export interface TapeRect {
  x: number;
  y: number;
  w: number;
  h: number;
  angle: number;
}

/** A floor under a dragged-out strip's length — a strip minted from a near-
 *  zero-length drag (a tap that missed being recognised as a tap) would be
 *  invisible and impossible to select afterward to delete. */
export const MIN_DRAG_LENGTH = 1;

/**
 * The strip a straight drag from A to B describes: length is the drag's own
 * distance, angle is the segment's own direction (so a hand-tremor off
 * perfectly horizontal is exactly what "拉直" — straighten — exists to fix),
 * centre is the midpoint, thickness is whatever the caller decided (a fixed
 * default, or one measured from the text line underneath).
 */
export function tapeFromDrag(
  ax: number,
  ay: number,
  bx: number,
  by: number,
  thickness: number,
): TapeRect {
  const dx = bx - ax;
  const dy = by - ay;
  const w = Math.max(MIN_DRAG_LENGTH, Math.hypot(dx, dy));
  const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
  return { x: (ax + bx) / 2, y: (ay + by) / 2, w, h: thickness, angle };
}

/**
 * The bounding descriptor for a FREEHAND strip — one that follows the pen's
 * own path rather than running straight ("可以直，也可以跟画笔画出来的一样不直").
 *
 * The path itself is stored alongside, but every consumer that only wants to
 * know *where* a strip is (hit-testing, the popover's anchor, a lasso's catch
 * test) reads this box instead, so neither kind of strip needs special-casing
 * there. `angle` is 0 by definition: a path carries its own direction, and
 * rotating the box under it would say something different from what is drawn.
 */
export function tapeBoundsOfPath(
  path: { x: number; y: number }[],
  thickness: number,
): TapeRect {
  if (path.length === 0) return { x: 0, y: 0, w: MIN_DRAG_LENGTH, h: thickness, angle: 0 };
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const p of path) {
    if (p.x < x0) x0 = p.x;
    if (p.y < y0) y0 = p.y;
    if (p.x > x1) x1 = p.x;
    if (p.y > y1) y1 = p.y;
  }
  // The stroke is `thickness` wide around its centreline, so the ink actually
  // reaches half a thickness past the raw sample bounds on every side.
  const half = thickness / 2;
  return {
    x: (x0 + x1) / 2,
    y: (y0 + y1) / 2,
    w: Math.max(MIN_DRAG_LENGTH, x1 - x0 + thickness),
    h: Math.max(half, y1 - y0 + thickness),
    angle: 0,
  };
}

/* ------------------------------------------------------ lasso transforms */
/*
   A tape strip caught by the lasso is transformed by the same drag as the
   strokes around it ("所有对象可以跟框选一样，可以被调大小、旋转"). The maths
   lives here rather than in the layer so the preview and the commit cannot
   disagree about where the strip ends up — the same class of split that made
   a moved stroke's marching ants sit in the old place.
*/

/** Move a strip by (dx, dy). A freehand path moves with it. */
export function translateTape<T extends TapeRect & { points?: { x: number; y: number }[] | null }>(
  t: T,
  dx: number,
  dy: number,
): TapeRect & { points?: { x: number; y: number }[] | null } {
  return {
    x: t.x + dx,
    y: t.y + dy,
    w: t.w,
    h: t.h,
    angle: t.angle,
    ...(t.points ? { points: t.points.map((p) => ({ x: p.x + dx, y: p.y + dy })) } : {}),
  };
}

/** Scale a strip about a pivot — its centre moves, and so do its own size and
 *  path. Thickness scales too, or a strip resized to twice the length would
 *  come out looking like a different, thinner piece of tape. */
export function scaleTape<T extends TapeRect & { points?: { x: number; y: number }[] | null }>(
  t: T,
  cx: number,
  cy: number,
  factor: number,
): TapeRect & { points?: { x: number; y: number }[] | null } {
  const f = Math.max(0.05, factor);
  return {
    x: cx + (t.x - cx) * f,
    y: cy + (t.y - cy) * f,
    w: t.w * f,
    h: t.h * f,
    angle: t.angle,
    ...(t.points
      ? { points: t.points.map((p) => ({ x: cx + (p.x - cx) * f, y: cy + (p.y - cy) * f })) }
      : {}),
  };
}

/** Rotate a strip about a pivot. The centre swings around it and the strip's
 *  own `angle` turns by the same amount, so a straight run stays straight and
 *  points the new way; a freehand path has every sample swung too. */
export function rotateTape<T extends TapeRect & { points?: { x: number; y: number }[] | null }>(
  t: T,
  cx: number,
  cy: number,
  radians: number,
): TapeRect & { points?: { x: number; y: number }[] | null } {
  const sin = Math.sin(radians);
  const cos = Math.cos(radians);
  const spin = (px: number, py: number): { x: number; y: number } => {
    const dx = px - cx;
    const dy = py - cy;
    return { x: cx + dx * cos - dy * sin, y: cy + dx * sin + dy * cos };
  };
  const c = spin(t.x, t.y);
  return {
    x: c.x,
    y: c.y,
    w: t.w,
    h: t.h,
    // Degrees on the wire, radians in the gesture — the one place they meet.
    angle: t.angle + (radians * 180) / Math.PI,
    ...(t.points ? { points: t.points.map((p) => spin(p.x, p.y)) } : {}),
  };
}

/** The outline the lasso hit-tests a strip against: its own path when it has
 *  one, otherwise the four corners of its rotated box. */
export function tapeOutline(t: TapeRect & { points?: { x: number; y: number }[] | null }): {
  x: number;
  y: number;
}[] {
  if (t.points && t.points.length >= 2) return t.points;
  const rad = (t.angle * Math.PI) / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const hw = t.w / 2;
  const hh = t.h / 2;
  return [
    [-hw, -hh],
    [hw, -hh],
    [hw, hh],
    [-hw, hh],
  ].map(([lx, ly]) => ({ x: t.x + lx! * cos - ly! * sin, y: t.y + lx! * sin + ly! * cos }));
}

/** Mirrors `services/tape.MIN_SIZE`/`MAX_SIZE`: a strip resized by a lasso
 *  drag must land inside the bounds the server will accept, or the PATCH is
 *  refused and the preview the reader just watched turns out to be a lie. */
export const MIN_TAPE_SIZE = 4;
export const MAX_TAPE_SIZE = 2000;

export function clampTapeSize(v: number): number {
  if (!Number.isFinite(v)) return MIN_TAPE_SIZE;
  return Math.min(MAX_TAPE_SIZE, Math.max(MIN_TAPE_SIZE, v));
}

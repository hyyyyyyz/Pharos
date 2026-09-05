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

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

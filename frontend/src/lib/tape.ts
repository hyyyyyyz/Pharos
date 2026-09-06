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
  // Clamped into the server's own [MIN_SIZE, MAX_SIZE] rather than sent as
  // measured. A drag qualifies as a drag after 4 CSS pixels, which at a
  // typical zoom is under 3 PDF points — below `MIN_TAPE_SIZE`, so the POST
  // came back 422 and the strip the reader had just watched appear vanished
  // with no explanation. The same goes for a thickness measured off a small
  // text line at high zoom.
  return {
    x: (ax + bx) / 2,
    y: (ay + by) / 2,
    w: clampTapeSize(w),
    h: clampTapeSize(thickness),
    angle,
  };
}

/* ------------------------------------------------------- axis-locked drag */
/*
   胶带 is laid along a line of text, and a line of text is horizontal. A strip
   built from nothing but its two endpoints inherits every degree of hand
   tremor, so covering one line meant dragging, looking at the result, and
   reaching for 拉直 — "直胶条要做到边写边改，而不是单单首尾相连".

   So the strip SNAPS to horizontal or vertical while it is being drawn, and
   then STAYS there. The rule the reader asked for, verbatim:

     "当方向为横或竖时，一直保持其方向，除非笔尖超出胶条宽边界，后可变方向"

   Two halves, and both matter:

   - **Snap** when the drag is near an axis (`AXIS_SNAP_TAN`), so an ordinary
     hand-drawn line along a paragraph comes out exactly level. A deliberately
     diagonal drag is left at its own angle — the rule is about keeping a
     direction that IS horizontal or vertical, not about forbidding others.
   - **Hold** it against the perpendicular wobble that the rest of the drag
     inevitably has, and release it only when the pen leaves the strip
     altogether — more than half a thickness off the axis, which is precisely
     "笔尖超出胶条宽边界". That hysteresis is what makes it feel locked rather
     than twitchy: without it, the axis would flip the instant |dy| crept past
     |dx| in the middle of a long horizontal sweep.
*/

/** Which way a strip is currently locked; `null` = not locked (free angle, or
 *  the drag is still too short to have a direction). */
export type TapeAxis = "x" | "y" | null;

/** How near an axis a drag has to be to snap onto it: tan(20°). Generous,
 *  because a hand dragging along a line of text is rarely within 5°, and the
 *  cases it would wrongly capture (a genuinely diagonal strip) are rare. */
const AXIS_SNAP_TAN = 0.36;

/** Travel before a drag has a direction at all, in PDF points. Below this the
 *  angle is just noise, and snapping to it would pick an axis at random. */
const AXIS_MIN_TRAVEL = 6;

/**
 * The strip this drag describes right now, and the axis it is locked to.
 *
 * Pure, and the caller threads `axis` back in on the next sample — that is
 * what carries the lock across a drag without this function needing state.
 */
export function tapeAlongAxis(
  ax: number,
  ay: number,
  bx: number,
  by: number,
  thickness: number,
  axis: TapeAxis,
): { rect: TapeRect; axis: TapeAxis } {
  const dx = bx - ax;
  const dy = by - ay;
  const ex = Math.abs(dx);
  const ey = Math.abs(dy);
  const half = clampTapeSize(thickness) / 2;

  // Has the pen left the strip's own width? That is the reader's
  // "笔尖超出胶带宽度范围" — and the trigger to reconsider the direction.
  const escaped = (axis === "x" && ey > half) || (axis === "y" && ex > half);

  let next: TapeAxis = axis;
  if (escaped) {
    // "应改变胶带的绘制方向" — once outside, the direction follows wherever the
    // hand is actually going, decided by the DOMINANT component with no
    // tolerance band. The first version re-applied the 20° snap test here,
    // which is why the direction never actually changed: a drag 100 right and
    // 40 down is nowhere near vertical by that test, so it re-locked to
    // horizontal and the strip stayed stubbornly level. The escape IS the
    // permission to switch; the only question left is which way.
    next = ey > ex ? "y" : "x";
  } else if (next === null && Math.hypot(dx, dy) >= AXIS_MIN_TRAVEL) {
    // Not locked yet: snap onto an axis only if the drag is genuinely near
    // one, so a deliberately diagonal strip keeps its own angle.
    if (ey <= ex * AXIS_SNAP_TAN) next = "x";
    else if (ex <= ey * AXIS_SNAP_TAN) next = "y";
  }

  // Locked: project the pen onto the axis, so the strip is exactly level (or
  // exactly upright) however the hand actually moved.
  if (next === "x") return { rect: tapeFromDrag(ax, ay, bx, ay, thickness), axis: next };
  if (next === "y") return { rect: tapeFromDrag(ax, ay, ax, by, thickness), axis: next };
  return { rect: tapeFromDrag(ax, ay, bx, by, thickness), axis: next };
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

/**
 * Who is allowed to make a mark — 防手指.
 *
 * The rule the reader asked for: **marking up the page is the pen's job**
 * ("操作要求用笔"), while ordinary UI — buttons, typing, scrolling — takes a
 * finger or a pen equally. So every gesture that draws, erases, lassoes,
 * restyles or lays down tape asks this first, and a touch is turned away
 * before it can become ink.
 *
 * Two deliberate exceptions:
 *
 * - **A mouse counts as a pen.** This same reader runs on a desktop, where
 *   there is no stylus and a mouse is the only way to draw at all. Rejecting
 *   anything that is not literally `pen` would make the whole toolset
 *   unusable there, so the test is "not a finger" rather than "is a stylus".
 * - **`fingerDraw` overrides it.** A tablet without a stylus (or a reader who
 *   simply prefers a finger) can still opt in via 手指书写; that switch used
 *   to be about palm rejection while drawing, and is now the escape hatch
 *   from this rule.
 *
 * Panning is NOT gated by this: two fingers still move the page, which is the
 * whole point of keeping touch free of ink.
 */
export function isDrawingPointer(
  e: { pointerType: string },
  fingerDraw = false,
): boolean {
  return e.pointerType !== "touch" || fingerDraw;
}

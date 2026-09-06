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

/**
 * Is this pointer a stylus — of either kind a stylus can arrive as?
 *
 * `"eraser"` is the one that keeps getting missed, and missing it is why
 * "按下 S Pen 按键变橡皮功能还是不对" survived a round of fixes. Android
 * reports a stylus whose barrel button is held as `MotionEvent.TOOL_TYPE_ERASER`
 * — that is how Samsung's own apps implement button-to-erase — and Chromium
 * passes it through as `pointerType: "eraser"`, NOT as a `"pen"` with a
 * button bit set. Every `pointerType === "pen"` test therefore fell through
 * to the plain-mouse branch: the button did not switch to the eraser, it
 * switched off the pen handling entirely and drew a line.
 *
 * So this is the test for "a stylus is doing something", and
 * `penEraseHeld` in `InkLayer` is the test for "and it is asking to erase".
 */
export function isStylus(e: { pointerType: string }): boolean {
  return e.pointerType === "pen" || e.pointerType === "eraser";
}

/* ------------------------------------------------- who is touching, globally */
/**
 * Is a stylus on the glass right now, and what kind of pointer arrived last?
 *
 * This exists because the two things that PAN the reader cannot see a pointer
 * type. One is a `MouseEvent` handler and the other a `TouchEvent` handler,
 * and neither event carries `pointerType` — a stylus reaches both as an
 * ordinary contact. So "笔不应触发画布拖动" cannot be expressed where the pan
 * actually happens; it has to be remembered from the pointer stream, which is
 * the only place the distinction survives.
 *
 * A capture-phase listener on `window`, because Chromium dispatches
 * `pointerdown` BEFORE both the corresponding `touchstart` and the
 * compatibility `mousedown`. By the time either pan handler runs, this is
 * already correct for the gesture that is starting.
 *
 * One listener for the whole app rather than a hook per component: there is
 * exactly one pointer, and any component asking "is it a pen" wants the same
 * answer.
 */
let penDown = false;
let lastType = "";

if (typeof window !== "undefined") {
  const opts = { capture: true, passive: true } as const;
  window.addEventListener(
    "pointerdown",
    (e) => {
      lastType = e.pointerType;
      if (isStylus(e)) penDown = true;
    },
    opts,
  );
  const up = (e: PointerEvent): void => {
    lastType = e.pointerType;
    if (isStylus(e)) penDown = false;
  };
  window.addEventListener("pointerup", up, opts);
  window.addEventListener("pointercancel", up, opts);
  // A pen lifted out of range without a pointerup (it happens on Android when
  // the digitiser loses the stylus) would otherwise leave the flag stuck on,
  // and with it the viewport permanently unpannable.
  window.addEventListener(
    "pointerleave",
    (e) => {
      if (isStylus(e)) penDown = false;
    },
    opts,
  );
}

/** Is a stylus in contact right now? */
export const isPenDown = (): boolean => penDown;

/**
 * Was the most recent pointer a stylus?
 *
 * Needed as well as `isPenDown` because some things are decided AFTER the pen
 * lifts — a double-tap is recognised on `touchend`, by which time `pointerup`
 * has already cleared the down flag.
 */
export const lastPointerWasStylus = (): boolean =>
  lastType === "pen" || lastType === "eraser";

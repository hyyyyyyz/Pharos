/**
 * InkLayer — handwritten strokes on one PDF page, captured from a stylus.
 *
 * Two canvases, the note-app architecture:
 *
 * - **dry** (z-index 3): every committed stroke, repainted from the query
 *   cache whenever strokes or zoom change.
 * - **wet** (z-index 4, above dry): the gesture in progress — the stroke
 *   being drawn, the lasso loop, the eraser preview, the selection marching
 *   ants, the live move preview. Redrawn from scratch on every input batch —
 *   cheap, because it only ever holds one gesture — so the outline can
 *   breathe with pressure without tearing the already-drawn tail. Predicted
 *   samples (Chromium's `getPredictedEvents`) extend the wet outline past the
 *   physical pen tip to hide digitiser latency; they are discarded on the
 *   next real sample.
 *
 * Mounted inside `.ph-pc-page` like `HighlightLayer`. Above the text layer
 * (ink sits ON the paper, and with a tool active the canvas — not the text
 * spans — receives the pointer, so writing never starts a selection) and
 * below this layer's own toolbar popup (z-index 6).
 *
 * Interaction model follows the note apps people already know:
 *
 * - **A stylus always writes**, whatever `inkFingerDraw` says. Touch writes
 *   only when the user opted in; that IS the palm rejection — a resting palm
 *   arrives as touch pointers, and they are ignored while a pen is down.
 * - **Two fingers pan.** With a tool active the canvas sets
 *   `touch-action: none`, which stills the browser's own scrolling, so the
 *   two-finger pan is implemented here against the page viewport. A finger
 *   stroke still in progress is abandoned when a second finger lands — it was
 *   never committed, so nothing is lost.
 * - **The eraser removes whole strokes** (OneNote's default) or, in 局部
 *   mode, splits strokes where it passes — the split parts are persisted as
 *   strokes in their own right, and the whole gesture is one undoable edit.
 * - **The lasso catches whole strokes** ("any part inside", like every note
 *   app): selected strokes can be dragged to move, recoloured, or deleted.
 *   A finger lassos only when finger-draw is on, for the same palm-rejection
 *   reason a finger writes only then.
 * - A finished gesture is written to the backend the moment the pen lifts;
 *   the gesture lands in the document-level undo stack (`store.inkPast`) as
 *   one op — an add, a remove, or an edit (rows replaced).
 *
 * Coordinates are PDF user space (points, scale 1, bottom-left origin) — the
 * same contract as highlights, so strokes follow their page through zooms and
 * devices. The canvas transform applies scale and the y-flip; `lib/ink` owns
 * the geometry and the outline rendering in that space.
 *
 * Input is handled with native listeners (not React synthetic events) because
 * the wet canvas needs `pointermove` at digitiser rate with coalesced
 * samples, and because React's root delegation adds one scheduling hop we
 * cannot afford mid-stroke.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type {
  InkPoint,
  InkStrokeRow,
  PageNoteRow,
  PageNoteStyle,
  TapeRow,
} from "../api/types";
import {
  INK_COLORS,
  WATER_COLORS,
  isWaterColor,
  paintInk,
  paintStroke,
  pointToPdf,
  rankInkColors,
  rotatePoints,
  scalePoints,
  splitStroke,
  strokeBounds,
  strokeCaughtBy,
  strokeNear,
  strokeOutline,
  paintOutline,
  translatePoints,
  unionBounds,
} from "../lib/ink";
import { Icons } from "../design/icons";
import { NEW_NOTE_H, NEW_NOTE_W } from "./NoteLayer";
import { isDrawingPointer, isStylus } from "../lib/pointer";
import { penSound } from "../lib/penSound";
import { clampTapeSize, rotateTape, scaleTape, tapeOutline, translateTape } from "../lib/tape";
import {
  MAX_INK_WIDTH,
  MIN_INK_WIDTH,
  batchOps,
  clampInkWidth,
  useUI,
  type InkClipboard,
  type InkMode,
  type InkOp,
} from "../store";
import "./InkLayer.css";

/** Server-side ceiling for one stroke's sample count (see services/ink.py). */
const MAX_POINTS = 2000;
/** Start thinning below the ceiling, leaving the server a little headroom. */
const THIN_THRESHOLD = 1900;

/**
 * 激光笔 — never a stored colour token, because a laser mark is never saved
 * and so never has to live in the backend's closed set.
 *
 * "颜色炫一点，做成七彩渐变色的": each mark is painted with a gradient down
 * its own length through these stops rather than one flat colour, which is
 * also what makes a long sweep readable — the hue tells you which end you
 * drew first.
 */
const LASER_STOPS = ["#ff2d55", "#ff9500", "#ffd60a", "#34c759", "#00c7ff", "#5e5ce6", "#bf5af2"];
const LASER_WIDTH = 3;
/**
 * The mark holds at full brightness until the pen has been QUIET this long,
 * then fades. Not "this long after the stroke ended": while you are still
 * drawing — mark after mark, explaining something — everything you have drawn
 * stays up, and the whole trail clears together once you actually stop
 * ("在检测到 2s 内没有写入再消退"). The first version faded 250ms after each
 * pen-up, which wiped the trail out from under an explanation still in
 * progress.
 */
const LASER_HOLD_MS = 2000;
const LASER_FADE_MS = 900;

/** 改字体粗细与颜色 (style brush) hit-test reach, in CSS pixels — small and
 *  fixed, unlike the eraser's adjustable presets: this tool is meant to pick
 *  out ONE stroke precisely, not sweep an area. */
const STYLE_BRUSH_REACH_PX = 10;

/** Hold this long, this still, and the 粘贴/文本框/便利贴 menu appears.
 *  1000ms is what the reader asked for ("摁一个位置 1s"); the slop is
 *  deliberately generous because a stylus resting on glass jitters by a point
 *  or two, and a menu that will not appear because of digitiser noise is worse
 *  than one that appears a fraction late. */
const LONG_PRESS_MS = 1000;
const LONG_PRESS_SLOP_PX = 8;

/**
 * The `PointerEvent.buttons` bits that mean "this stylus is asking to erase".
 *
 * Bit 1 (value **2**) is the BARREL button — the side button on a Samsung
 * S Pen, and the one the first attempt at this feature missed entirely, which
 * is why "按下按钮没有变出橡皮": an S Pen has no eraser end to report bit 5
 * with, so nothing ever fired.
 *
 * Bit 5 (value **32**) is the dedicated eraser END that Surface- and
 * Wacom-class pens have instead of (or as well as) a side button.
 *
 * Either one means the same thing to this reader, so both are accepted. Bit 0
 * (value 1) is the tip touching the glass and is checked separately — a
 * button pressed in mid-air must flip the tool WITHOUT starting a stroke.
 */
const PEN_ERASE_BUTTONS = 2 | 32;
const PEN_TIP_BUTTON = 1;

/**
 * Is this stylus asking to erase?
 *
 * THREE signals, because Android does not agree with the Pointer Events spec
 * about which one it sends, and the second attempt at this feature checked
 * only the ones the spec describes:
 *
 * 1. `pointerType === "eraser"`. This is the one that was missing, and it is
 *    the one a Samsung S Pen actually produces: Android reports a stylus with
 *    its barrel button held as `MotionEvent.TOOL_TYPE_ERASER`, and Chromium
 *    forwards that as a pointer of type `"eraser"` rather than as a `"pen"`
 *    carrying a button bit. Every `pointerType === "pen"` gate in this file
 *    therefore *rejected* the button press — so holding the button did not
 *    fail to erase, it fell through to the mouse branch and drew a line,
 *    which is exactly what "按下按钮没有变出橡皮" describes.
 * 2. The `buttons` bits above, for devices that do follow the spec.
 * 3. `button === 2` / `button === 5` on the transition event itself, for
 *    drivers that announce the press but report `buttons` a frame late.
 */
const penEraseHeld = (e: PointerEvent): boolean => {
  if (!isStylus(e)) return false;
  if (e.pointerType === "eraser") return true;
  if ((e.buttons & PEN_ERASE_BUTTONS) !== 0) return true;
  // `button` is -1 on a plain move and 0 on a tip transition; 2 is the barrel
  // button and 5 the eraser end, per the spec's button-value table.
  return e.button === 2 || e.button === 5;
};

/** Gesture state — refs, because it changes at digitiser rate. */
interface Session {
  pointerId: number;
  points: InkPoint[];
  erasing: boolean;
  /** 套索 loop in progress instead of a stroke. */
  lasso: boolean;
  /**
   * Dragging the selection: the ids being moved and the live (dx, dy) in PDF
   * space. The ids also tell `repaintDry` which strokes to hide while they
   * are being carried, so the moved copy never doubles the original. The
   * start point is where the drag began, in PDF space — the delta is
   * measured against it, not against the stroke's own geometry.
   */
  moving: {
    ids: Set<string>;
    dx: number;
    dy: number;
    startX: number;
    startY: number;
  } | null;
  /**
   * Dragging a resize corner or the rotate handle: the ids being transformed,
   * the pivot the transform is anchored to (the opposite corner for a
   * resize, the selection's own centre for a rotate), and the live factor
   * (1 = untouched) or angle (0 = untouched) measured against where the
   * drag started. Mutually exclusive with `moving` — a gesture is one kind
   * of edit.
   */
  transform: {
    kind: "resize" | "rotate";
    ids: Set<string>;
    cx: number;
    cy: number;
    startX: number;
    startY: number;
    factor: number;
    radians: number;
  } | null;
}

/** A corner drives a resize (anchored to the opposite corner); "rotate" is
 *  the standalone handle above the selection's top edge. */
type HandleKind = "nw" | "ne" | "sw" | "se" | "rotate";

/** Constant on-screen sizes for the selection's resize/rotate handles —
 *  device pixels, not PDF points, so a handle is exactly as easy to grab at
 *  25% zoom as at 400%. */
const HANDLE_HIT_PX = 22;
const ROTATE_GAP_PX = 26;
/** Drawn radius of a corner/rotate handle, in CSS pixels. Smaller than the hit
 *  radius above on purpose: the target a finger has to find should be bigger
 *  than the dot it is aiming at, not the same size. */
const HANDLE_DRAW_PX = 5.5;
/** The selection's own chrome — a thin accent-blue frame with round handles,
 *  the marquee every note app draws and the one in the reference. The dark
 *  dashed rectangle it replaces read as a crop tool, and its square handles
 *  read as something you resize a window by. */
const SELECT_STROKE = "#5b8def";
const SELECT_FILL = "#ffffff";
/** Breathing room between the caught marks and the frame, in PDF points at
 *  zoom 1 — enough that the frame never touches the ink it is describing. */
const SELECT_PAD_PX = 6;

/**
 * The frame's rectangle: the marks' own bounds plus a constant on-screen gap.
 *
 * One function for it because the frame, the handles and the hit test all
 * have to agree on where the corners are — when the painter padded and the
 * hit test did not, every handle was `SELECT_PAD_PX` away from the dot the
 * reader was aiming at, which at a tablet's finger sizes is a miss.
 */
function paddedBox(
  box: { x0: number; y0: number; x1: number; y1: number },
  scale: number,
): { x0: number; y0: number; x1: number; y1: number } {
  const pad = SELECT_PAD_PX / scale;
  return { x0: box.x0 - pad, y0: box.y0 - pad, x1: box.x1 + pad, y1: box.y1 + pad };
}

/** Where the selection's handles sit, in PDF space, for one bounding box at
 *  the current zoom. The opposite-corner map is what a resize drag anchors
 *  to — dragging "se" scales away from "nw", and so on. */
function selectionHandles(
  box: { x0: number; y0: number; x1: number; y1: number },
  scale: number,
): { kind: HandleKind; x: number; y: number }[] {
  const gap = ROTATE_GAP_PX / scale;
  return [
    { kind: "sw", x: box.x0, y: box.y0 },
    { kind: "se", x: box.x1, y: box.y0 },
    { kind: "nw", x: box.x0, y: box.y1 },
    { kind: "ne", x: box.x1, y: box.y1 },
    { kind: "rotate", x: (box.x0 + box.x1) / 2, y: box.y1 + gap },
  ];
}

const OPPOSITE_CORNER: Record<Exclude<HandleKind, "rotate">, Exclude<HandleKind, "rotate">> = {
  nw: "se",
  ne: "sw",
  sw: "ne",
  se: "nw",
};

/** The selection itself: the rows the loop caught. The toolbar's position is
 *  derived from the selection's own box rather than stored — see
 *  `selectionAnchor`. Anchoring it where the lasso happened to END put the
 *  toolbar in an arbitrary place, often over the marks it acts on. */
interface Selection {
  rows: InkStrokeRow[];
  /** 胶带 caught by the same loop. The lasso is the reader's ONE transform
   *  tool — "所有对象可以跟框选一样，可以被调大小、旋转" — so a strip inside
   *  the loop moves, scales and turns with the strokes around it. */
  tapes: TapeRow[];
}

/** The one box the ants and the handles both use: everything caught by the
 *  lasso, strokes and 胶带 alike. */
function selectionBox(
  rows: InkStrokeRow[],
  tapes: TapeRow[],
): { x0: number; y0: number; x1: number; y1: number } {
  return unionBounds([...rows.map((r) => strokeBounds(r.points)), ...tapes.map(tapeBox)]);
}

/** The bounding box of one tape strip, in the same PDF space stroke bounds
 *  use — its own path when it has one, otherwise its rotated corners. */
function tapeBox(t: TapeRow): { x0: number; y0: number; x1: number; y1: number } {
  const pts = tapeOutline(t);
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const p of pts) {
    if (p.x < x0) x0 = p.x;
    if (p.y < y0) y0 = p.y;
    if (p.x > x1) x1 = p.x;
    if (p.y > y1) y1 = p.y;
  }
  // A freehand strip's path is its centreline, so the ink reaches half a
  // thickness past it on each side.
  const pad = t.points ? t.h / 2 : 0;
  return { x0: x0 - pad, y0: y0 - pad, x1: x1 + pad, y1: y1 + pad };
}

export function InkLayer({
  paperId,
  kind,
  page,
  scale,
  pageHeight,
}: {
  paperId: string;
  kind: "original" | "mono" | "dual";
  /** 1-based. */
  page: number;
  /** PDF points -> CSS pixels for the current zoom. */
  scale: number;
  pageHeight: number;
}): JSX.Element {
  const qc = useQueryClient();
  const dryRef = useRef<HTMLCanvasElement>(null);
  const waterRef = useRef<HTMLCanvasElement>(null);
  /**
   * The live 水彩笔 stroke — its own canvas, sharing `.ph-ink-water`'s
   * `mix-blend-mode: multiply` and its place BELOW the pen layers.
   *
   * "水彩笔画的时候先盖住字，然后再作为色底于字后，这不对，应该直接作为色底."
   * Exactly right, and the cause was structural: the wash was committed to a
   * multiply-blended canvas but *previewed* on the ordinary wet canvas, which
   * sits above the ink and does not blend. So the stroke under the pen was an
   * opaque bar over the glyphs, and it only dropped behind them once the pen
   * lifted and the commit moved it to the other layer. A blend mode is a
   * property of the canvas, not of a draw call, so the fix is a canvas — not
   * a flag on `paintWet`.
   */
  const wetWaterRef = useRef<HTMLCanvasElement>(null);
  const wetRef = useRef<HTMLCanvasElement>(null);

  const inkMode = useUI((s) => s.inkMode);
  const setInkMode = useUI((s) => s.setInkMode);
  const setInkTray = useUI((s) => s.setInkTray);
  const setInkCarried = useUI((s) => s.setInkCarried);
  /** Shut the toolbar's open tray. Stable, so it can sit in the gesture
   *  effect's deps without rebinding it every render. */
  const closeInkTray = useCallback(() => setInkTray(null), [setInkTray]);
  const inkColor = useUI((s) => s.inkColor);
  const inkColorUsage = useUI((s) => s.inkColorUsage);
  /**
   * The palette behind 更改风格 on the selection bar.
   *
   * A quick action on a floating toolbar, not a place to browse the whole
   * palette: the same four inks the draw toolbar leads with (墨黑 plus this
   * reader's top three), and then the washes — because what the lasso caught
   * may well BE a wash, and a restyle bar that cannot express what is
   * selected is a restyle bar that can only make things worse.
   */
  const selStyleColors = useMemo(() => {
    const keys = rankInkColors(INK_COLORS, inkColorUsage, Date.now());
    const inks = keys.map((key) => INK_COLORS.find((c) => c.key === key)).filter((c) => c != null);
    return [...inks, ...WATER_COLORS];
  }, [inkColorUsage]);
  const inkWidth = useUI((s) => s.inkWidth);
  const inkFingerDraw = useUI((s) => s.inkFingerDraw);
  const inkSound = useUI((s) => s.inkSound);
  const inkEraserSize = useUI((s) => s.inkEraserSize);
  const inkEraseMode = useUI((s) => s.inkEraseMode);
  const inkPenDebug = useUI((s) => s.inkPenDebug);
  const setPenProbe = useUI((s) => s.setInkPenProbe);
  const clipboard = useUI((s) => s.inkClipboard);
  const setClipboard = useUI((s) => s.setInkClipboard);
  const noteColor = useUI((s) => s.noteColor);
  const setNoteFocus = useUI((s) => s.setNoteFocusId);
  const pushInkOps = useUI((s) => s.pushInkOps);

  // One fetch per document+rendition; every page instance shares the cache.
  // `staleTime: Infinity` matters here, not just as a network saving: every
  // write goes through `updateCache` (an optimistic `setQueryData`) the
  // instant it happens, so this query is never the source of truth again
  // after the first load. Without it, scrolling reveals a new page, mounts a
  // fresh `InkLayer` for the SAME query key, and — because the global
  // `staleTime` is 5s — that remounts triggers a background refetch. If that
  // request happened to start before an erase/edit and resolves after it,
  // `useQuery` overwrites the cache with the pre-edit snapshot: erased
  // strokes reappear (or an undo/redo you already did silently reverts).
  // The cache is patched locally on every mutation and explicitly
  // invalidated on failure, so a background refetch was never buying
  // freshness — only this race.
  const { data: all } = useQuery({
    queryKey: ["ink", paperId, kind],
    queryFn: ({ signal }) => api.ink.list(paperId, kind, signal),
    staleTime: Infinity,
  });

  const mine = useMemo(
    () => (all ?? []).filter((s) => s.page === page && s.points.length > 0),
    [all, page],
  );

  // The same cache entry `TapeLayer` fills — one fetch, two readers. The lasso
  // needs these to catch strips, and the transform preview needs them live.
  const { data: allTape } = useQuery({
    queryKey: ["tape", paperId, kind],
    queryFn: ({ signal }) => api.tape.list(paperId, kind, signal),
    staleTime: Infinity,
  });
  const myTape = useMemo(
    () => (allTape ?? []).filter((t) => t.page === page),
    [allTape, page],
  );
  const tapeRef = useRef(myTape);
  tapeRef.current = myTape;
  /**
   * The gesture handlers read `mine` through this ref, never through the
   * effect closure: an eraser gesture mutates the cache mid-drag (strokes
   * leave), and a `mine`-keyed effect would tear its own handlers down
   * mid-gesture — killing the session after the very first split.
   */
  const mineRef = useRef(mine);
  mineRef.current = mine;

  const key = `${paperId} ${kind}`;

  /* ------------------------------------------------------------ selection */

  const [selection, setSelection] = useState<Selection | null>(null);
  const selRef = useRef<Selection | null>(null);
  selRef.current = selection;
  /** Is 更改风格's colour/width tray open on the selection bar? Collapsed by
   *  default and reset with the selection, so the bar stays the width of its
   *  own row of commands until asked to be more. */
  const [styleOpen, setStyleOpen] = useState(false);
  useEffect(() => {
    if (selection === null) setStyleOpen(false);
  }, [selection]);

  /**
   * temp id -> the row the server returned for it.
   *
   * Written synchronously in `commitReplace`'s `settle`, BEFORE the cache
   * write, so a render triggered by that write can always follow an id the
   * selection is still holding. Bounded rather than cleaned per-entry: a
   * mapping is only interesting for the few hundred milliseconds between the
   * POST landing and the selection catching up.
   */
  const settledRef = useRef(new Map<string, InkStrokeRow>());

  /** Live move preview, consulted by `repaintDry` (hide the carried rows). */
  const movingRef = useRef<{ ids: Set<string>; dx: number; dy: number } | null>(null);
  /** A lasso/move session's pointer moved enough to be a gesture, not a tap. */
  const movedRef = useRef(false);

  /* ------------------------------------------------------------- painting */

  /** Size a canvas's backing store to its CSS box × dpr, and return the
   *  PDF-space transform factor. Null when the canvas is not laid out yet. */
  const prepare = useCallback(
    (canvas: HTMLCanvasElement): { k: number; w: number } | null => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      if (w === 0 || h === 0) return null;
      const bw = Math.floor(w * dpr);
      const bh = Math.floor(h * dpr);
      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }
      const k = dpr * scale;
      canvas.getContext("2d")?.setTransform(k, 0, 0, -k, 0, pageHeight * k);
      return { k, w };
    },
    [scale, pageHeight],
  );

  const clearInSpace = useCallback(
    (canvas: HTMLCanvasElement, w: number) => {
      canvas
        .getContext("2d")
        ?.clearRect(0, 0, w / scale, pageHeight);
    },
    [scale, pageHeight],
  );

  /** The token->colour resolver, read once per repaint: 300 strokes must not
   *  cost 300 forced style resolutions. */
  const colorResolver = useCallback((canvas: HTMLCanvasElement) => {
    const style = getComputedStyle(canvas);
    return (token: string): string =>
      style.getPropertyValue(`--c-ink-${token}`).trim() ||
      style.getPropertyValue("--c-tx").trim() ||
      "#1e222a";
  }, []);

  /**
   * Repaint every committed stroke from the cache — through `mineRef`, so a
   * data change never re-creates this callback (and thus never rebinds the
   * gesture effect mid-drag; see the ref's note).
   */
  /** Repaints BOTH committed-stroke canvases: the regular opaque dry layer,
   *  and 水彩笔's separate multiply-blended wash layer (`.ph-ink-water`) —
   *  kept apart so an opaque pen stroke never gets the wash's blend mode,
   *  and a wash never gets the pen's opacity. One function, so every one of
   *  this file's several repaint-after-a-gesture call sites refreshes both
   *  layers without having to remember a second call. */
  const repaintDry = useCallback(() => {
    const dry = dryRef.current;
    const water = waterRef.current;
    if (!dry || !water) return;
    const dryReady = prepare(dry);
    const waterReady = prepare(water);
    if (!dryReady || !waterReady) return;
    clearInSpace(dry, dryReady.w);
    clearInSpace(water, waterReady.w);
    const colorOfDry = colorResolver(dry);
    const colorOfWater = colorResolver(water);
    const carried = movingRef.current?.ids;
    for (const stroke of mineRef.current) {
      if (carried?.has(stroke.id)) continue; // being dragged; the wet layer shows it
      if (isWaterColor(stroke.color)) {
        paintStroke(water.getContext("2d")!, stroke, colorOfWater);
      } else {
        paintStroke(dry.getContext("2d")!, stroke, colorOfDry);
      }
    }
  }, [prepare, clearInSpace, colorResolver]);

  // Data (or zoom) changed: dry follows the cache. `mine` in the deps is the
  // trigger; `mineRef` inside is what actually gets painted.
  useEffect(() => {
    repaintDry();
  }, [repaintDry, mine]);

  /**
   * The selection frame: ONE thin accent rectangle around everything caught.
   *
   * It used to draw a separate dashed box per caught stroke, which read as a
   * pile of unrelated boxes rather than one thing you can grab — while the
   * resize/rotate handles were already on the combined box, so the chrome
   * disagreed with itself about what was selected. One box, one set of
   * handles, one thing to drag.
   *
   * Solid and accent-coloured now, not dark and dashed. Dashes are the LASSO's
   * language — a loop being drawn — and using them for the result too meant
   * the "I am cutting a shape out" and "here is what I cut" states looked the
   * same. This is the marquee from the reference: a hairline frame, and the
   * handles below sitting on it.
   */
  const paintSelection = useCallback(
    (ctx: CanvasRenderingContext2D, box: { x0: number; y0: number; x1: number; y1: number }) => {
      const b = paddedBox(box, scale);
      ctx.save();
      ctx.strokeStyle = SELECT_STROKE;
      ctx.lineWidth = 1.25 / scale;
      ctx.strokeRect(b.x0, b.y0, b.x1 - b.x0, b.y1 - b.y0);
      ctx.restore();
    },
    [scale],
  );

  /** The resize corners + rotate handle around the WHOLE selection (a single
   *  combined box, not one per stroke — dragging one handle transforms the
   *  group together). Round, white-filled and accent-ringed, matching the
   *  frame; the rotate handle sits above the top edge on a short stem and
   *  carries an arc so it cannot be mistaken for a fifth resize corner.
   *  Idle only: a live resize/rotate repaints its own preview instead (see
   *  `paintWet`'s `transform` branch), so the handles never fight the drag
   *  they belong to. */
  const paintSelectionHandles = useCallback(
    (ctx: CanvasRenderingContext2D, box: { x0: number; y0: number; x1: number; y1: number }) => {
      if (box.x1 - box.x0 <= 0 && box.y1 - box.y0 <= 0) return;
      const padded = paddedBox(box, scale);
      const cx = (padded.x0 + padded.x1) / 2;
      const r = HANDLE_DRAW_PX / scale;
      const gap = ROTATE_GAP_PX / scale;
      ctx.save();
      ctx.strokeStyle = SELECT_STROKE;
      ctx.lineWidth = 1.25 / scale;
      ctx.beginPath();
      ctx.moveTo(cx, padded.y1);
      ctx.lineTo(cx, padded.y1 + gap);
      ctx.stroke();
      for (const h of selectionHandles(padded, scale)) {
        // The rotate handle is a size up from the four that resize: it does a
        // different job, and at corner-dot size there is simply no room inside
        // for a glyph that reads as anything.
        const hr = h.kind === "rotate" ? r * 1.5 : r;
        ctx.beginPath();
        ctx.arc(h.x, h.y, hr, 0, Math.PI * 2);
        ctx.fillStyle = SELECT_FILL;
        ctx.fill();
        ctx.stroke();
        if (h.kind === "rotate") {
          // A curved arrow: an arc of about 200 degrees with a solid head on
          // the end. Both properties are load-bearing. The first attempt drew
          // a nearly-closed ring, which at this size reads as the © glyph, not
          // as "turn me"; what makes it an arrow is that it is visibly OPEN
          // and that one end is pointed.
          const ar = hr * 0.52;
          const from = Math.PI * 0.2;
          const to = Math.PI * 1.3;
          ctx.beginPath();
          ctx.arc(h.x, h.y, ar, from, to);
          ctx.stroke();
          const hx = h.x + ar * Math.cos(to);
          const hy = h.y + ar * Math.sin(to);
          const t = to + Math.PI / 2; // the tangent, i.e. where it is heading
          const head = hr * 0.42;
          ctx.beginPath();
          ctx.moveTo(hx + head * Math.cos(t), hy + head * Math.sin(t));
          ctx.lineTo(hx + head * Math.cos(t + 2.3), hy + head * Math.sin(t + 2.3));
          ctx.lineTo(hx + head * Math.cos(t - 2.3), hy + head * Math.sin(t - 2.3));
          ctx.closePath();
          ctx.fillStyle = SELECT_STROKE;
          ctx.fill();
        }
      }
      ctx.restore();
    },
    [scale],
  );

  /* ------------------------------------------------------------- gestures */

  const strokeRef = useRef<Session | null>(null);
  /** Pointer id of the active pen, while down — the palm-rejection gate. */
  const penRef = useRef<number | null>(null);
  /** The tool a S Pen-style barrel button borrowed, restored on release. Set
   *  only while hovering (never mid-gesture — flipping `inkMode` there would
   *  rebind this whole effect and abandon the stroke in progress). `null`
   *  means the button is up, or the mode it would restore is already current
   *  (the user picked erase themselves; releasing must not fight that). */
  const barrelPrevModeRef = useRef<InkMode | null>(null);
  /** Last input sample, for the nib's speed (CSS px and ms — the sound is
   *  about how fast the HAND is moving, which is a screen-space question,
   *  not a PDF-space one: the same hand at 400% zoom is not writing faster. */
  const lastSampleRef = useRef<{ x: number; y: number; t: number } | null>(null);
  /** 笔尖诊断: read through a ref inside the gesture handlers, so switching it
   *  on does not rebind them (and abandon a stroke) mid-writing. */
  const penDebugRef = useRef(inkPenDebug);
  penDebugRef.current = inkPenDebug;
  const probeAtRef = useRef(0);

  /* ------------------------------------------------------ the long press */
  /**
   * Hold the pen still for a second and a small menu appears — 粘贴, 文本框,
   * 便利贴.
   *
   * The gesture exists because there is nowhere else for those three to live.
   * A paste has no toolbar button (paste WHERE? the answer is "here", and only
   * a press knows where "here" is), and putting a text box down means naming a
   * spot on the page. A long press is the one gesture that carries a location
   * and is not already spoken for by drawing.
   *
   * It is armed on pointerdown and disarmed by movement or by the pen lifting,
   * so it can never fire in the middle of a stroke — the timer is cancelled by
   * the first sample that travels more than `LONG_PRESS_SLOP` from where the
   * pen landed. That threshold is deliberately generous: a stylus resting on
   * glass jitters by a point or two, and a menu that refuses to appear because
   * of digitiser noise is worse than one that appears a fraction late.
   */
  const [pressMenu, setPressMenu] = useState<{
    /** CSS page pixels, for placing the menu. */
    left: number;
    top: number;
    /** PDF space, for placing whatever it creates. */
    x: number;
    y: number;
  } | null>(null);
  const pressTimerRef = useRef(0);
  const cancelLongPress = useCallback(() => {
    if (pressTimerRef.current !== 0) {
      clearTimeout(pressTimerRef.current);
      pressTimerRef.current = 0;
    }
  }, []);
  /** rAF handle for the laser's hold-then-fade loop; 0 = none in flight. */
  const laserFadeRef = useRef(0);
  /**
   * Laser marks still on screen, oldest first — each one a finished sweep.
   *
   * A list, not a single mark, because the trail has to survive the NEXT
   * sweep: you point at three things in a row while explaining, and all
   * three stay up until you stop. `laserActiveAtRef` is the last moment the
   * laser was actually drawing, and the whole trail holds until it has been
   * quiet for `LASER_HOLD_MS`, then fades out together.
   */
  const laserMarksRef = useRef<InkPoint[][]>([]);
  const laserActiveAtRef = useRef(0);
  /** Live touch pointers, for the two-finger pan. */
  const touchesRef = useRef(new Map<number, { x: number; y: number }>());
  const panMidRef = useRef<{ x: number; y: number } | null>(null);
  /** Strokes deleted by the in-flight 整笔 eraser gesture, for one undo op. */
  const erasedRef = useRef<InkStrokeRow[]>([]);
  /** 局部 eraser account: rows removed / parts created during one gesture. */
  const pixelRemovedRef = useRef<InkStrokeRow[]>([]);
  const pixelAddedRef = useRef<InkStrokeRow[]>([]);
  /** 改字体粗细与颜色 (style brush) account: same shape as the 局部 eraser's —
   *  originals the pen touched this gesture, and their restyled replacements —
   *  so one drag that crosses several strokes still lands as one undo op. */
  const styleRemovedRef = useRef<InkStrokeRow[]>([]);
  const styleAddedRef = useRef<InkStrokeRow[]>([]);

  /** Functional cache write: safe against mid-gesture staleness. */
  const updateCache = useCallback(
    (updater: (prev: InkStrokeRow[]) => InkStrokeRow[]) => {
      qc.setQueryData<InkStrokeRow[]>(["ink", paperId, kind], (prev) =>
        updater(prev ?? []),
      );
    },
    [qc, paperId, kind],
  );

  const viewportPan = useCallback((dx: number, dy: number) => {
    const vp = wetRef.current?.closest(".ph-pc") as HTMLElement | null;
    if (!vp) return;
    vp.scrollLeft += dx;
    vp.scrollTop += dy;
  }, []);

  /** Replace whole rows: optimistic cache swap, one undoable edit op, then
   *  the network settles it. Used by the lasso (move, recolour) and the 局部
   *  eraser (split). `specs` are the replacement row payloads in the same
   *  order as the temp rows this function mints.
   *
   *  Returns the temps, so a caller that wants the selection to SURVIVE the
   *  edit can re-select them. Moving a stroke replaces the row, so the rows a
   *  selection was holding are stale the moment a drag commits — which is
   *  exactly why a selection used to evaporate after one operation. */
  const commitReplace = useCallback(
    (
      removedRows: InkStrokeRow[],
      specs: { points: InkPoint[]; color: string; width: number }[],
      /** Ops from the SAME gesture (a lasso drag's 胶带 edits), folded into
       *  one history entry with this one — one drag, one undo. */
      alsoOps: InkOp[] = [],
    ): InkStrokeRow[] => {
      const temps: InkStrokeRow[] = specs.map((s) => ({
        id: `temp-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
        paper_id: paperId,
        kind,
        page,
        points: s.points,
        color: s.color,
        width: s.width,
        created_at: new Date().toISOString(),
      }));
      const goneIds = new Set(removedRows.map((r) => r.id));
      updateCache((prev) => [
        ...prev.filter((r) => !goneIds.has(r.id)),
        ...temps,
      ]);
      pushInkOps(
        key,
        batchOps([...alsoOps, { kind: "edit", removed: removedRows, added: temps }]),
      );

      /** Point one settled row at its temp — in the cache, in the undo op, and
       *  in the SELECTION, so a selection kept alive across an edit ends up
       *  holding server rows rather than ids the server has never heard of. */
      const settle = (tempId: string, row: InkStrokeRow): void => {
        // Recorded FIRST, and synchronously: `updateCache` notifies TanStack
        // subscribers immediately, so the very next render can already see the
        // temp gone — and the selection resync effect needs to be able to
        // follow it to `row` at that moment, not one batched state update
        // later.
        if (settledRef.current.size > 200) settledRef.current.clear();
        settledRef.current.set(tempId, row);
        updateCache((prev) => prev.map((s) => (s.id === tempId ? row : s)));
        useUI.setState((s) => {
          if (s.inkOpsKey !== key) return s;
          for (let i = s.inkPast.length - 1; i >= 0; i--) {
            const op = s.inkPast[i];
            if (op.kind !== "edit") continue;
            const idx = op.added.findIndex((a) => a.id === tempId);
            if (idx === -1) continue;
            const added = op.added.slice();
            added[idx] = row;
            const past = s.inkPast.slice();
            past[i] = { ...op, added };
            return { inkPast: past };
          }
          return s;
        });
      };

      void (async () => {
        let failed = false;
        for (const row of removedRows) {
          if (row.id.startsWith("temp-")) continue; // never reached the server
          await api.ink.remove(row.id).catch(() => {
            failed = true;
          });
        }
        const settled: InkStrokeRow[] = [];
        for (let i = 0; i < specs.length; i++) {
          try {
            const row = await api.ink.create(paperId, {
              kind,
              page,
              points: specs[i]!.points,
              color: specs[i]!.color,
              width: specs[i]!.width,
            });
            settled.push(row);
            settle(temps[i]!.id, row);
          } catch {
            failed = true;
            break;
          }
        }
        if (failed || settled.length !== specs.length) {
          // The server refused part of the edit. Nothing clever here: the
          // refetch is the truth (deleted rows are gone, made rows persist),
          // and the pushed op no longer describes reality, so it comes off
          // the undo stack before anyone can undo into it.
          void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
          useUI.setState((s) => {
            if (s.inkOpsKey !== key) return s;
            const last = s.inkPast[s.inkPast.length - 1];
            if (
              last &&
              last.kind === "edit" &&
              last.added.some((a) => temps.some((t) => t.id === a.id))
            ) {
              return { inkPast: s.inkPast.slice(0, -1) };
            }
            return s;
          });
          // The rows the selection is holding no longer exist. Dropping it is
          // the honest answer: keeping a box around ids the server refused
          // would offer operations that cannot work.
          setSelection(null);
        }
      })();
      return temps;
    },
    [key, kind, page, paperId, pushInkOps, updateCache, qc],
  );

  /* 整笔: erase every stored stroke the point touches, optimistically. */
  const eraseAt = useCallback(
    (x: number, y: number) => {
      const done = new Set(erasedRef.current.map((s) => s.id));
      for (const stroke of mineRef.current) {
        if (done.has(stroke.id)) continue;
        if (!strokeNear(stroke.points, stroke.width, x, y, inkEraserSize / scale)) continue;
        erasedRef.current.push(stroke);
        updateCache((prev) => prev.filter((s) => s.id !== stroke.id));
        void api.ink.remove(stroke.id).catch(() => {
          void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
        });
      }
    },
    [scale, inkEraserSize, updateCache, qc, paperId, kind],
  );

  /* 局部: split every stroke the point touches; parts stay as real strokes.
     Re-splitting parts created earlier in the SAME gesture is expected — the
     account below keeps the undo op exact (removed originals, final parts). */
  const eraseAtPixel = useCallback(
    (x: number, y: number) => {
      const reach = inkEraserSize / scale;
      for (const stroke of mineRef.current) {
        const parts = splitStroke(stroke.points, stroke.width, x, y, reach);
        if (parts.length === 1 && parts[0]!.length === stroke.points.length) continue;
        const kept = parts.filter((p) => p.length >= 2); // specks are noise
        const temps: InkStrokeRow[] = kept.map((points) => ({
          id: `temp-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
          paper_id: paperId,
          kind,
          page,
          points,
          color: stroke.color,
          width: stroke.width,
          created_at: new Date().toISOString(),
        }));
        const goneId = stroke.id;
        // A part created earlier this gesture may now be split further: it
        // leaves the account, its children enter it. Optimistic cache move
        // only — the server half of the gesture commits at gesture end, as
        // one edit op.
        updateCache((prev) => [...prev.filter((s) => s.id !== goneId), ...temps]);
        pixelAddedRef.current = pixelAddedRef.current.filter((a) => a.id !== stroke.id);
        if (!stroke.id.startsWith("temp-")) pixelRemovedRef.current.push(stroke);
        pixelAddedRef.current.push(...temps);
      }
    },
    [scale, inkEraserSize, updateCache, kind, page, paperId],
  );

  /**
   * Throw away an in-flight 局部 erase: its temp parts leave the cache and
   * the originals it cut come back. Only ever called with a gesture that
   * never committed, so nothing server-side has happened yet.
   */
  const abandonPixelGesture = useCallback(() => {
    const addedIds = new Set(pixelAddedRef.current.map((a) => a.id));
    const removed = pixelRemovedRef.current;
    pixelAddedRef.current = [];
    pixelRemovedRef.current = [];
    if (addedIds.size === 0 && removed.length === 0) return;
    updateCache((prev) => [
      ...prev.filter((s) => !addedIds.has(s.id)),
      // The originals are still on the server (deletes are deferred to the
      // commit), but they left the cache as they were cut.
      ...removed,
    ]);
  }, [updateCache]);

  /**
   * 改字体粗细与颜色: the pen touching a stroke recolours/rewidths it to the
   * CURRENT colour + thickness — the same pickers the pen tool itself uses,
   * repurposed as "apply this style" rather than "draw with it". A stroke
   * already at the target style is skipped, both because there is nothing to
   * change and so re-crossing an already-touched stroke mid-drag is a no-op
   * rather than minting a fresh temp row every pass.
   */
  const styleAt = useCallback(
    (x: number, y: number) => {
      const reach = STYLE_BRUSH_REACH_PX / scale;
      for (const stroke of mineRef.current) {
        if (stroke.color === inkColor && stroke.width === inkWidth) continue;
        if (!strokeNear(stroke.points, stroke.width, x, y, reach)) continue;
        const temp: InkStrokeRow = {
          id: `temp-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
          paper_id: paperId,
          kind,
          page,
          points: stroke.points,
          color: inkColor,
          width: inkWidth,
          created_at: new Date().toISOString(),
        };
        updateCache((prev) => prev.map((s) => (s.id === stroke.id ? temp : s)));
        if (!stroke.id.startsWith("temp-")) styleRemovedRef.current.push(stroke);
        styleAddedRef.current.push(temp);
      }
    },
    [scale, inkColor, inkWidth, updateCache, kind, page, paperId],
  );

  /** Throw away an in-flight style-brush gesture — same reasoning as
   *  `abandonPixelGesture`: nothing has reached the server yet. */
  const abandonStyleGesture = useCallback(() => {
    const addedIds = new Set(styleAddedRef.current.map((a) => a.id));
    const removed = styleRemovedRef.current;
    styleAddedRef.current = [];
    styleRemovedRef.current = [];
    if (addedIds.size === 0 && removed.length === 0) return;
    updateCache((prev) => [...prev.filter((s) => !addedIds.has(s.id)), ...removed]);
  }, [updateCache]);

  /**
   * The eraser preview: a white disc with a dark ring, exactly the reach of
   * the eraser, drawn in DEVICE space (the PDF transform would rescale it
   * with zoom — the preview must be a constant screen-size promise of what a
   * touch will remove). One computed-style read avoided: pure white over the
   * paper needs no theme tokens.
   */
  const paintEraserCursor = useCallback(
    (clientX: number, clientY: number) => {
      const wet = wetRef.current;
      if (!wet) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
      const w = wet.clientWidth;
      const h = wet.clientHeight;
      if (w === 0 || h === 0) return;
      const bw = Math.floor(w * dpr);
      const bh = Math.floor(h * dpr);
      if (wet.width !== bw || wet.height !== bh) {
        wet.width = bw;
        wet.height = bh;
      } else {
        wet.getContext("2d")?.clearRect(0, 0, bw, bh);
      }
      const rect = wet.getBoundingClientRect();
      const ctx = wet.getContext("2d")!;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.beginPath();
      ctx.arc(
        (clientX - rect.left) * dpr,
        (clientY - rect.top) * dpr,
        inkEraserSize * dpr,
        0,
        Math.PI * 2,
      );
      ctx.fillStyle = "rgba(255, 255, 255, 0.75)";
      ctx.fill();
      ctx.lineWidth = 1.5 * dpr;
      ctx.strokeStyle = "rgba(28, 32, 40, 0.95)";
      ctx.stroke();
    },
    [inkEraserSize],
  );

  /** Wipe the live-wash canvas. Called on EVERY `paintWet`, not only when a
   *  wash is being drawn: it is a second canvas that nothing else clears, so
   *  a wash left on it would stay under the page until the next wash. */
  const clearWetWater = useCallback(() => {
    const c = wetWaterRef.current;
    if (!c) return;
    c.getContext("2d")?.clearRect(0, 0, c.width, c.height);
  }, []);

  /** Empty BOTH wet layers (hover cursor gone, gesture ghost gone, live wash
   *  gone). One function, because a caller that cleared only the top one left
   *  the last wash sitting under the page with nothing to remove it. */
  const clearWet = useCallback(() => {
    wetRef.current?.getContext("2d")?.clearRect(0, 0, wetRef.current.width, wetRef.current.height);
    clearWetWater();
  }, [clearWetWater]);

  /** The 七彩 sweep: a gradient down the mark's own longest axis, so a long
   *  stroke runs the spectrum instead of ending up one muddy average. */
  const laserGradient = useCallback(
    (ctx: CanvasRenderingContext2D, points: InkPoint[]): CanvasGradient | string => {
      if (points.length < 2) return LASER_STOPS[0]!;
      const b = strokeBounds(points);
      // Along whichever axis the mark actually spans — a vertical sweep should
      // read top-to-bottom, not be flattened into a single hue.
      const horizontal = b.x1 - b.x0 >= b.y1 - b.y0;
      const g = horizontal
        ? ctx.createLinearGradient(b.x0, b.y0, b.x1, b.y0)
        : ctx.createLinearGradient(b.x0, b.y0, b.x0, b.y1);
      LASER_STOPS.forEach((stop, i) => g.addColorStop(i / (LASER_STOPS.length - 1), stop));
      return g;
    },
    [],
  );

  /** Paint every live laser mark at one shared alpha, glow and all. */
  const paintLaserMarks = useCallback(
    (ctx: CanvasRenderingContext2D, alpha: number) => {
      if (alpha <= 0) return;
      for (const points of laserMarksRef.current) {
        const outline = strokeOutline(points, LASER_WIDTH);
        if (outline.length < 3) continue;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.shadowColor = LASER_STOPS[0]!;
        ctx.shadowBlur = 14 / scale;
        paintOutline(ctx, outline, laserGradient(ctx, points));
        ctx.restore();
      }
    },
    [scale, laserGradient],
  );

  /**
   * A carried 胶带 strip, drawn on the wet canvas while a lasso drag is in
   * flight. `TapeLayer` hides the committed strip meanwhile (`inkCarried`),
   * so the reader sees one strip moving rather than a ghost and a copy.
   *
   * Plain and opaque: this is a preview of a cover, and its exact texture is
   * `TapeLayer`'s business once the drag commits.
   */
  const paintTapePreview = useCallback(
    (ctx: CanvasRenderingContext2D, t: TapeRow) => {
      ctx.save();
      ctx.fillStyle = "rgba(232, 220, 184, 0.92)";
      ctx.strokeStyle = "rgba(120, 100, 40, 0.5)";
      ctx.lineWidth = 1 / scale;
      if (t.points && t.points.length >= 2) {
        // A freehand strip is its path, stroked at its own thickness.
        ctx.strokeStyle = "rgba(232, 220, 184, 0.92)";
        ctx.lineWidth = t.h;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(t.points[0]!.x, t.points[0]!.y);
        for (const p of t.points.slice(1)) ctx.lineTo(p.x, p.y);
        ctx.stroke();
      } else {
        const corners = tapeOutline(t);
        ctx.beginPath();
        ctx.moveTo(corners[0]!.x, corners[0]!.y);
        for (const c of corners.slice(1)) ctx.lineTo(c.x, c.y);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
      ctx.restore();
    },
    [scale],
  );

  /** Drop the whole trail and stop the loop — switching away from the laser,
   *  or the fade running to zero. */
  const clearLaser = useCallback(() => {
    if (laserFadeRef.current) {
      cancelAnimationFrame(laserFadeRef.current);
      laserFadeRef.current = 0;
    }
    laserMarksRef.current = [];
  }, []);

  /** Repaint the wet layer, by gesture kind:
   *  - erase: the reach preview (or nothing while a touch erases without hover);
   *  - draw: the live outline, plus the predicted tail — on the blended wash
   *    canvas when the colour is a 水彩笔 tone, so it is a colour底 from the
   *    first sample rather than only after the pen lifts;
   *  - lasso: the dashed loop;
   *  - move: the carried strokes at their live offset, the frame over them.
   *
   *  Call `schedulePaintWet` from an input handler, not this — see there. */
  const paintWet = useCallback(
    (session: Session, predicted: InkPoint[]) => {
      const wet = wetRef.current;
      if (!wet) return;
      const sized = prepare(wet);
      if (!sized) return;
      clearInSpace(wet, sized.w);
      clearWetWater();
      const ctx = wet.getContext("2d")!;
      const colorOf = colorResolver(wet);
      if (session.lasso) {
        if (session.points.length >= 2) {
          ctx.setLineDash([5 / scale, 4 / scale]);
          ctx.lineWidth = 1.5 / scale;
          ctx.strokeStyle = "rgba(28, 32, 40, 0.95)";
          ctx.beginPath();
          ctx.moveTo(session.points[0]!.x, session.points[0]!.y);
          for (const p of session.points.slice(1)) ctx.lineTo(p.x, p.y);
          // NOT closed visually. `pointInPolygon` closes the loop implicitly
          // when it hit-tests, so the catch area is the same either way — but
          // drawing that closing edge put a long dashed chord across the page
          // between wherever the pen started and wherever it is now, which is
          // what "套索工具首尾一直有一条黑虚线" is about. The loop you see is
          // now just the path you drew.
          ctx.stroke();
          ctx.setLineDash([]);
        }
        return;
      }
      if (session.moving) {
        const { ids, dx, dy } = session.moving;
        const rows = (selRef.current?.rows ?? []).filter((r) => ids.has(r.id));
        for (const stroke of rows) {
          paintStroke(
            ctx,
            {
              points: translatePoints(stroke.points, dx, dy),
              width: stroke.width,
              color: stroke.color,
            },
            colorOf,
          );
        }
        const tapes = (selRef.current?.tapes ?? [])
          .filter((t) => ids.has(t.id))
          .map((t) => ({ ...t, ...translateTape(t, dx, dy) }));
        for (const t of tapes) paintTapePreview(ctx, t);
        paintSelection(ctx, selectionBox(rows.map((r) => ({ ...r, points: translatePoints(r.points, dx, dy) })), tapes));
        return;
      }
      if (session.transform) {
        const { ids, cx, cy, factor, radians } = session.transform;
        const resize = session.transform.kind === "resize";
        const rows = (selRef.current?.rows ?? []).filter((r) => ids.has(r.id));
        const preview = rows.map((r) => ({
          ...r,
          points: resize
            ? scalePoints(r.points, cx, cy, factor)
            : rotatePoints(r.points, cx, cy, radians),
          width: resize ? r.width * Math.max(0.05, factor) : r.width,
        }));
        for (const stroke of preview) {
          paintStroke(ctx, { points: stroke.points, width: stroke.width, color: stroke.color }, colorOf);
        }
        const tapes = (selRef.current?.tapes ?? [])
          .filter((t) => ids.has(t.id))
          .map((t) => ({
            ...t,
            ...(resize ? scaleTape(t, cx, cy, factor) : rotateTape(t, cx, cy, radians)),
          }));
        for (const t of tapes) paintTapePreview(ctx, t);
        paintSelection(ctx, selectionBox(preview, tapes));
        return;
      }
      if (session.erasing) return;
      // The trail already on screen has to be repainted with the live stroke:
      // this canvas is cleared every batch, so anything not redrawn here
      // blinks out the moment the next sweep starts.
      if (inkMode === "laser") paintLaserMarks(ctx, 1);
      const live = [...session.points, ...predicted];
      if (inkMode === "laser") {
        // 炫一点: a 七彩 gradient with a glow, not a flat fill — the pointer
        // is meant to catch the eye for the seconds it lives, unlike ink
        // meant to be read calmly afterward.
        const outline = strokeOutline(live, LASER_WIDTH, true);
        if (outline.length >= 3) {
          ctx.save();
          ctx.shadowColor = LASER_STOPS[0]!;
          ctx.shadowBlur = 14 / scale;
          paintOutline(ctx, outline, laserGradient(ctx, live));
          ctx.restore();
        }
        return;
      }
      // A wash goes on the blended canvas below the ink, exactly where its
      // committed self will land; a pen stroke stays on the wet canvas above.
      // Same painter either way — `paintInk` is where "a tap is a dot" lives,
      // and the preview must not be the one place that forgets it.
      let target = ctx;
      if (isWaterColor(inkColor)) {
        const wetWater = wetWaterRef.current;
        if (wetWater && prepare(wetWater)) target = wetWater.getContext("2d")!;
      }
      paintInk(target, live, inkWidth, colorOf(inkColor), true);
    },
    [
      prepare,
      clearInSpace,
      clearWetWater,
      colorResolver,
      inkWidth,
      inkColor,
      inkMode,
      scale,
      paintSelection,
      paintLaserMarks,
      laserGradient,
      paintTapePreview,
    ],
  );

  /**
   * One wet repaint per FRAME, whatever rate the digitiser delivers at.
   *
   * "荧光笔不稳定，会闪烁." A stylus reports at 120-240 Hz and the display
   * refreshes at 60, so an unthrottled `paintWet` cleared and redrew the canvas
   * two to four times per displayed frame. For a plain stroke that is merely
   * wasteful; for the laser it flickers, because each pass clears the canvas
   * and then rebuilds a `shadowBlur` glow and a seven-stop gradient for every
   * mark on screen — expensive enough to run past the frame budget, so the
   * compositor sometimes samples the canvas in its CLEARED state.
   *
   * The samples themselves are never dropped: `onMove` still pushes every
   * coalesced point into the session before asking for a repaint, so the stroke
   * that gets committed has full resolution. Only the drawing is coalesced.
   */
  const paintFrameRef = useRef(0);
  const pendingPaintRef = useRef<{ session: Session; predicted: InkPoint[] } | null>(null);
  const schedulePaintWet = useCallback(
    (session: Session, predicted: InkPoint[]) => {
      pendingPaintRef.current = { session, predicted };
      if (paintFrameRef.current !== 0) return;
      paintFrameRef.current = requestAnimationFrame(() => {
        paintFrameRef.current = 0;
        const next = pendingPaintRef.current;
        pendingPaintRef.current = null;
        if (next) paintWet(next.session, next.predicted);
      });
    },
    [paintWet],
  );

  /** Drop a frame that has not run yet — the gesture it belonged to is over,
   *  and painting it now would put a ghost back on a canvas just cleared. */
  const cancelPendingPaint = useCallback(() => {
    if (paintFrameRef.current !== 0) {
      cancelAnimationFrame(paintFrameRef.current);
      paintFrameRef.current = 0;
    }
    pendingPaintRef.current = null;
  }, []);

  /* --------------------------------------------------- selection overlays */

  /**
   * Draw the selection's frame and handles onto the (cleared) wet canvas.
   *
   * A callback rather than only an effect, because the chrome has to be
   * restored as well as painted: the wet canvas doubles as the hover-preview
   * surface, and anything that wipes it — most obviously `pointerleave` when
   * the reader moves off the page toward the selection's own toolbar — takes
   * the frame with it. Reaching for 更改风格 made the box you were acting on
   * disappear, which is a good half of "套索工具不好用".
   *
   * Reads `selRef`, not `selection`, so it stays stable and the gesture
   * effect can depend on it without rebinding every time a selection changes.
   */
  const repaintSelectionChrome = useCallback((): boolean => {
    const sel = selRef.current;
    const wet = wetRef.current;
    if (!sel || sel.rows.length + sel.tapes.length === 0 || !wet) return false;
    const sized = prepare(wet);
    if (!sized) return false;
    clearInSpace(wet, sized.w);
    const ctx = wet.getContext("2d")!;
    const box = selectionBox(sel.rows, sel.tapes);
    paintSelection(ctx, box);
    paintSelectionHandles(ctx, box);
    return true;
  }, [prepare, clearInSpace, paintSelection, paintSelectionHandles]);

  /** The frame follows the zoom; also repaints on selection/stroke changes. */
  useEffect(() => {
    repaintSelectionChrome();
  }, [selection, repaintSelectionChrome]);

  /**
   * Keep the selection pointing at rows that exist — RESYNC it, do not drop it.
   *
   * The distinction is the whole of "框选不要操作一次后立马断了". A drag
   * replaces every stroke it moved: the optimistic temps go into the cache
   * first and the server's rows replace them one at a time as the POSTs land.
   * The previous version cleared the selection the moment ANY id in it was not
   * in the cache — which is guaranteed to happen mid-settle, and measurably
   * did: three temps selected, one already swapped for `181080d3…`, whole
   * selection gone.
   *
   * So an id that has been settled is followed to its replacement rather than
   * counted as missing, and the selection is only cleared when nothing in it
   * survives at all (a real deletion, from here or another view). Resyncing
   * also refreshes the rows' geometry, which is what keeps the frame on the
   * marks after a change that came from somewhere else.
   */
  useEffect(() => {
    if (!selection) return;
    if (inkMode !== "select") {
      setSelection(null);
      return;
    }
    const rowById = new Map(mine.map((r) => [r.id, r]));
    const tapeById = new Map(myTape.map((t) => [t.id, t]));
    const follow = (row: InkStrokeRow): InkStrokeRow | undefined => {
      const direct = rowById.get(row.id);
      if (direct) return direct;
      const settled = settledRef.current.get(row.id);
      return settled ? rowById.get(settled.id) : undefined;
    };
    const rows = selection.rows.map(follow).filter((r): r is InkStrokeRow => r != null);
    const tapes = selection.tapes
      .map((t) => tapeById.get(t.id))
      .filter((t): t is TapeRow => t != null);
    if (rows.length + tapes.length === 0) {
      setSelection(null);
      return;
    }
    // Only write when something actually changed, or this effect re-triggers
    // itself forever on the array identities it just created.
    const same =
      rows.length === selection.rows.length &&
      tapes.length === selection.tapes.length &&
      rows.every((r, i) => r === selection.rows[i]) &&
      tapes.every((t, i) => t === selection.tapes[i]);
    if (!same) setSelection({ rows, tapes });
  }, [inkMode, mine, myTape, selection]);

  /**
   * Persist a lasso transform applied to 胶带.
   *
   * Unlike a stroke — which is replaced wholesale, because its geometry IS
   * its samples — a strip is edited in place: the same PATCH the popover's
   * steppers use, with the new box (and, for a freehand strip, the new path).
   * Each strip's change goes on the undo stack as its own `tape-edit`, so one
   * undo of a mixed selection puts everything back together.
   */
  const commitTapeTransform = useCallback(
    (
      tapes: TapeRow[],
      apply: (t: TapeRow) => Partial<TapeRow>,
    ): { ops: InkOp[]; moved: TapeRow[] } => {
      const ops: InkOp[] = [];
      // The strips as they now are. A strip is edited in place, so its id
      // survives — but its geometry does not, and a selection kept alive
      // across the drag has to hold the NEW numbers or its frame would sit
      // where the strip used to be.
      const moved: TapeRow[] = [];
      for (const t of tapes) {
        const next = apply(t);
        const patch = {
          x: next.x ?? t.x,
          y: next.y ?? t.y,
          w: clampTapeSize(next.w ?? t.w),
          h: clampTapeSize(next.h ?? t.h),
          angle: ((next.angle ?? t.angle) % 360 + 360) % 360,
          ...(next.points ? { points: next.points } : {}),
        };
        ops.push(
          {
            kind: "tape-edit",
            id: t.id,
            // The whole box AND the path: a move changes x/y, a rotate
            // rewrites a freehand strip's samples, and an undo that put back
            // only some of that would leave the strip drawn along its old
            // curve in its new place.
            before: { x: t.x, y: t.y, w: t.w, h: t.h, angle: t.angle, points: t.points },
            after: patch,
          },
        );
        qc.setQueryData<TapeRow[]>(["tape", paperId, kind], (prev) =>
          (prev ?? []).map((row) => (row.id === t.id ? { ...row, ...patch } : row)),
        );
        moved.push({ ...t, ...patch });
        void api.tape.update(t.id, patch).catch(() => {
          void qc.invalidateQueries({ queryKey: ["tape", paperId, kind] });
        });
      }
      setInkCarried([]);
      return { ops, moved };
    },
    [qc, paperId, kind, setInkCarried],
  );

  /** Commit the drag: translate the selection by the live delta, swap rows. */
  const commitMove = useCallback(
    (session: Session) => {
      const moving = session.moving;
      if (!moving || (moving.dx === 0 && moving.dy === 0)) {
        movingRef.current = null;
        setInkCarried([]);
        repaintDry();
        return;
      }
      const rows = (selRef.current?.rows ?? []).filter((r) => moving.ids.has(r.id));
      movingRef.current = null;
      const specs = rows.map((r) => ({
        points: translatePoints(r.points, moving.dx, moving.dy),
        color: r.color,
        width: r.width,
      }));
      const tapes = (selRef.current?.tapes ?? []).filter((t) => moving.ids.has(t.id));
      const { ops: tapeOps, moved } = commitTapeTransform(tapes, (t) =>
        translateTape(t, moving.dx, moving.dy),
      );
      const made = commitReplace(rows, specs, tapeOps);
      // The selection SURVIVES the drag, now pointing at what the drag
      // produced: "框选不要操作一次后立马断了，应可多次拖动对其操作". Moving a
      // stroke replaces its row, so re-selecting the temps (which `settle`
      // later swaps for the server's rows) is what keeps the box on the marks
      // rather than on ids that no longer exist.
      setSelection({ rows: made, tapes: moved });
    },
    [commitReplace, repaintDry, commitTapeTransform, setInkCarried],
  );

  /** Commit a resize or rotate: apply the live factor/angle once, swap rows —
   *  same shape as `commitMove`, a different geometry function. */
  const commitTransform = useCallback(
    (session: Session) => {
      const t = session.transform;
      if (!t || (t.factor === 1 && t.radians === 0)) {
        movingRef.current = null;
        setInkCarried([]);
        repaintDry();
        return;
      }
      const rows = (selRef.current?.rows ?? []).filter((r) => t.ids.has(r.id));
      movingRef.current = null;
      const specs = rows.map((r) => ({
        points:
          t.kind === "resize"
            ? scalePoints(r.points, t.cx, t.cy, t.factor)
            : rotatePoints(r.points, t.cx, t.cy, t.radians),
        color: r.color,
        width: t.kind === "resize" ? clampInkWidth(r.width * Math.max(0.05, t.factor)) : r.width,
      }));
      const tapes = (selRef.current?.tapes ?? []).filter((tp) => t.ids.has(tp.id));
      const { ops: tapeOps, moved } = commitTapeTransform(tapes, (tp) =>
        t.kind === "resize"
          ? scaleTape(tp, t.cx, t.cy, t.factor)
          : rotateTape(tp, t.cx, t.cy, t.radians),
      );
      // Same as `commitMove`: keep the selection on the result, so a resize
      // can be followed by a rotate can be followed by a drag.
      setSelection({ rows: commitReplace(rows, specs, tapeOps), tapes: moved });
    },
    [commitReplace, repaintDry, commitTapeTransform, setInkCarried],
  );

  /* ------------------------------------------------- native event handlers */

  useEffect(() => {
    // "tape" is TapeLayer's own tool and "text" is NoteLayer's — nobody should
    // be drawing ink while a strip is placed or a text box typed into, so this
    // layer goes fully inert for both, same as "off".
    if (inkMode === "off" || inkMode === "tape" || inkMode === "text") return;
    const wet = wetRef.current;
    if (!wet) return;
    // The page transform is not ready yet (first layout pass, or mid pinch-
    // zoom before the new scale settles): `pointToPdf` divides by `scale`, so
    // a zero or not-yet-finite value turns every point into Infinity/NaN.
    // `JSON.stringify` renders those as `null`, and the backend's `PointIn`
    // requires a finite float — every stroke drawn in that window landed as
    // a 422 the user could not explain (tablet testing: writing "randomly"
    // stopped saving). Wait for real geometry instead of capturing garbage.
    if (!Number.isFinite(scale) || scale <= 0 || !Number.isFinite(pageHeight) || pageHeight <= 0) {
      return;
    }
    const debug = new URLSearchParams(window.location.search).has("inkdebug");

    const inPageSpace = (clientX: number, clientY: number, pressure: number): InkPoint => {
      const origin = wet.getBoundingClientRect();
      return pointToPdf(clientX, clientY, origin, scale, pageHeight, pressure);
    };

    /**
     * Is (x, y) inside the selection's frame?
     *
     * The FRAME — the rectangle the reader can see — not each caught stroke's
     * own bounds, which is what it tested before. Those are two different
     * shapes: press inside the frame but in the gap between two strokes and
     * nothing happened, so the box you were plainly holding did not move.
     * What is drawn as one object behaves as one object.
     */
    const overSelection = (x: number, y: number): boolean => {
      const sel = selRef.current;
      if (!sel || sel.rows.length + sel.tapes.length === 0) return false;
      const b = paddedBox(selectionBox(sel.rows, sel.tapes), scale);
      return x >= b.x0 && x <= b.x1 && y >= b.y0 && y <= b.y1;
    };

    /** Which resize/rotate handle (x, y) is over, checked BEFORE a plain
     *  drag-to-move so a touch near a corner grabs the handle, not the
     *  stroke under it. Against the PADDED box — the same rectangle the
     *  handles are painted on. */
    const handleAt = (x: number, y: number): { kind: HandleKind; x: number; y: number } | null => {
      const sel = selRef.current;
      if (!sel || sel.rows.length + sel.tapes.length === 0) return null;
      const box = paddedBox(selectionBox(sel.rows, sel.tapes), scale);
      const reach = HANDLE_HIT_PX / scale;
      for (const h of selectionHandles(box, scale)) {
        if (Math.hypot(x - h.x, y - h.y) <= reach) return h;
      }
      return null;
    };

    const startSession = (e: PointerEvent): void => {
      if (strokeRef.current !== null) return; // one gesture at a time
      // 开始书写后，折叠栏应该自动收起来: the palette/thickness tray has done
      // its job the moment the pen touches down, and from then on it is just
      // a panel sitting over the page being written on.
      closeInkTray();
      // The fade loop must stop repainting under the gesture about to start.
      // For a laser stroke the TRAIL survives (this sweep joins it, and the
      // quiet-timer restarts); for any other tool the trail is abandoned —
      // switching away from the laser should not leave marks hanging over
      // the page.
      if (laserFadeRef.current) {
        cancelAnimationFrame(laserFadeRef.current);
        laserFadeRef.current = 0;
      }
      if (inkMode === "laser") laserActiveAtRef.current = performance.now();
      else laserMarksRef.current = [];
      // Barrel-button / eraser-end erases in any tool.
      const eraserButton = penEraseHeld(e);
      const pressure = e.pressure > 0 ? e.pressure : 0.5;
      const pt = inPageSpace(e.clientX, e.clientY, pressure);

      if (inkMode === "select") {
        // A handle wins over a plain carry — grabbing near a corner resizes
        // (or, at the top handle, rotates) rather than dragging the stroke
        // that happens to sit under it.
        const handle = handleAt(pt.x, pt.y);
        const sel = selRef.current;
        if (handle && sel) {
          const ids = new Set([...sel.rows, ...sel.tapes].map((r) => r.id));
          setInkCarried(sel.tapes.map((t) => t.id));
          // The padded box, matching what was painted and what `handleAt`
          // just hit: a resize must pivot on the corner the reader can see
          // opposite the one under their pen, not on an unpadded corner a few
          // points inside it.
          const box = paddedBox(selectionBox(sel.rows, sel.tapes), scale);
          const center = { x: (box.x0 + box.x1) / 2, y: (box.y0 + box.y1) / 2 };
          const corner = handle.kind === "rotate" ? null : OPPOSITE_CORNER[handle.kind];
          const pivot =
            corner === null
              ? center
              : selectionHandles(box, scale).find((h) => h.kind === corner)!;
          strokeRef.current = {
            pointerId: e.pointerId,
            points: [],
            erasing: false,
            lasso: false,
            moving: null,
            transform: {
              kind: handle.kind === "rotate" ? "rotate" : "resize",
              ids,
              cx: pivot.x,
              cy: pivot.y,
              startX: pt.x,
              startY: pt.y,
              factor: 1,
              radians: 0,
            },
          };
          movedRef.current = false;
        } else if (sel && overSelection(pt.x, pt.y)) {
          const ids = new Set([...sel.rows, ...sel.tapes].map((r) => r.id));
          setInkCarried(sel.tapes.map((t) => t.id));
          strokeRef.current = {
            pointerId: e.pointerId,
            points: [],
            erasing: false,
            lasso: false,
            moving: { ids, dx: 0, dy: 0, startX: pt.x, startY: pt.y },
            transform: null,
          };
          movedRef.current = false;
        } else {
          setSelection(null);
          strokeRef.current = {
            pointerId: e.pointerId,
            points: [pt],
            erasing: false,
            lasso: true,
            moving: null,
            transform: null,
          };
        }
      } else {
        const erasing = inkMode === "erase" || eraserButton;
        strokeRef.current = {
          pointerId: e.pointerId,
          points: [pt],
          erasing,
          lasso: false,
          moving: null,
          transform: null,
        };
        if (erasing) {
          if (inkEraseMode === "pixel") {
            pixelRemovedRef.current = [];
            pixelAddedRef.current = [];
          } else {
            erasedRef.current = [];
          }
        }
      }
      // Arm the long press. Any real movement disarms it (see `onMove`), so it
      // can only ever fire on a pen that has genuinely been held still.
      cancelLongPress();
      const downAt = { x: e.clientX, y: e.clientY };
      const rect = wet.getBoundingClientRect();
      pressTimerRef.current = window.setTimeout(() => {
        pressTimerRef.current = 0;
        // A gesture that has become a stroke, a lasso or a drag is no longer a
        // press — bail rather than interrupt it.
        const session = strokeRef.current;
        if (!session || session.points.length > 2 || session.moving || session.transform) return;
        strokeRef.current = null;
        cancelPendingPaint();
        clearWet();
        setPressMenu({
          left: downAt.x - rect.left,
          top: downAt.y - rect.top,
          x: pt.x,
          y: pt.y,
        });
      }, LONG_PRESS_MS);
      try {
        wet.setPointerCapture(e.pointerId);
      } catch {
        /* capture is an optimisation; events still target the canvas */
      }
      if (debug) console.debug("[ink] down", e.pointerType, e.pressure, e.pointerId);
    };

    const onTouchDown = (e: PointerEvent): void => {
      // A pen outranks everything: while it is down, a touch pointer is a palm.
      if (penRef.current !== null) return;
      // 防手指 says a finger is not ink. The corollary — and the half that was
      // missing — is that a finger is therefore NAVIGATION, and navigation
      // belongs to the viewport: one finger pans, two fingers zoom, and
      // 锁定画布 decides which of those are allowed. This layer swallowing
      // every touch to run a two-finger pan of its own is why, with a tool
      // selected, one finger did nothing, two fingers could never zoom, and
      // the lock button looked inert (measured, both ways).
      //
      // With 手指书写 on a finger genuinely IS ink, and everything below —
      // including the layer's own two-finger pan — is right to keep it.
      if (!inkFingerDraw) return;
      touchesRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touchesRef.current.size === 2) {
        // Second finger: abandon the uncommitted gesture (wipe its wet
        // ghost) and become a pan.
        strokeRef.current = null;
        movingRef.current = null;
        cancelPendingPaint();
        paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null, transform: null }, []);
        const pts = [...touchesRef.current.values()];
        panMidRef.current = {
          x: (pts[0]!.x + pts[1]!.x) / 2,
          y: (pts[0]!.y + pts[1]!.y) / 2,
        };
      } else if (touchesRef.current.size > 2) {
        return;
      }
      if (touchesRef.current.size === 1 && strokeRef.current === null) {
        // 防手指: marking up the page is the pen's job — draw, erase, lasso,
        // restyle, point, all of it. A lone finger here is a palm landing
        // while writing, or a scroll that belongs to the viewport, and it now
        // gets to be neither ink nor an edit.
        //
        // This used to exempt erase/select/style/laser on the grounds that
        // they lay down no ink of their own, which was the wrong axis: a
        // finger erasing a paragraph by accident is worse than a finger
        // drawing a line, not better. 手指书写 remains the explicit opt-out
        // for a tablet with no stylus (see `lib/pointer`).
        if (isDrawingPointer(e, inkFingerDraw)) startSession(e);
      }
    };

    /**
     * Borrow the eraser while a stylus button is held, and give the tool back
     * when it lifts — 按下按钮变橡皮，松开退出.
     *
     * Called from EVERY pen event, not just hover: the first version only ran
     * in the hover branch, which assumes the device reports the button while
     * the pen is in the air. Plenty do not — some only report it in contact,
     * and some deliver no hover moves at all — so hover-only meant the
     * feature silently never fired.
     *
     * It still refuses to switch while a gesture is in flight, because
     * `inkMode` is a dependency of this whole effect: flipping it mid-stroke
     * would tear the listeners down and abandon the stroke being drawn. A
     * button pressed mid-stroke is therefore honoured for the NEXT stroke,
     * and the in-flight one keeps whatever it started as (the per-gesture
     * `eraserButton` check in `startSession` already covers "pressed before
     * the tip landed", which is the case that actually matters).
     */
    const syncPenButton = (e: PointerEvent): void => {
      // `isStylus`, not `pointerType === "pen"`: on Android a barrel-held
      // S Pen arrives as pointerType "eraser", and this gate was rejecting
      // exactly the events it exists to react to.
      if (!isStylus(e)) return;
      if (strokeRef.current !== null) return; // mid-gesture: see above
      const held = penEraseHeld(e);
      if (held && barrelPrevModeRef.current === null && inkMode !== "erase") {
        barrelPrevModeRef.current = inkMode;
        setInkMode("erase");
      } else if (!held && barrelPrevModeRef.current !== null) {
        setInkMode(barrelPrevModeRef.current);
        barrelPrevModeRef.current = null;
      }
    };

    /**
     * Feed the 笔尖诊断 readout, when it is switched on.
     *
     * What the device actually reports is the one thing three attempts at the
     * S Pen button have all had to guess at, and it is not guessable from
     * here: Android's stylus-button mapping differs by OEM and by Chromium
     * version. Rate-limited because a `pointermove` arrives at digitiser rate
     * and each write re-renders the toolbar; the interesting transitions
     * (down, up, a button appearing) are never the ones dropped.
     */
    const reportPen = (e: PointerEvent, phase: string): void => {
      if (!penDebugRef.current || e.pointerType === "touch") return;
      const now = e.timeStamp;
      if (phase === "移动" && now - probeAtRef.current < 150) return;
      probeAtRef.current = now;
      setPenProbe(
        `${phase} · ${e.pointerType} · button ${e.button} · buttons ${e.buttons} · 压力 ${e.pressure.toFixed(2)}`,
      );
    };

    const onDown = (e: PointerEvent): void => {
      e.preventDefault();
      if (e.pointerType === "touch") {
        onTouchDown(e);
        return;
      }
      reportPen(e, "按下");
      if (debug) {
        console.debug(
          "[ink] down",
          e.pointerType,
          "button",
          e.button,
          "buttons",
          e.buttons,
          "pressure",
          e.pressure,
        );
      }
      // A stylus button pressed in MID-AIR has no contact bit: it must flip
      // the tool and stop there, never mint a zero-length gesture. (The old
      // gate let such an event through to `startSession` whenever bit 32 was
      // set, and rejected a barrel-held tip-down outright because its
      // `button` is not 0.)
      if (isStylus(e)) {
        // `button === 0` is the tip transition; `buttons & 1` is the tip being
        // held. Either will do: a driver that under-reports `buttons` on
        // pointerdown would otherwise stop the pen drawing AT ALL, which is a
        // far worse failure than the side button not being noticed.
        //
        // An `"eraser"` pointer is always contact — Android only reports that
        // tool type for a stylus that is touching or hovering with the button
        // held, and the barrel-button events it sends carry `button: -1` and
        // no tip bit, so demanding one would throw the whole gesture away.
        const tipDown =
          e.pointerType === "eraser" || e.button === 0 || (e.buttons & PEN_TIP_BUTTON) !== 0;
        if (!tipDown) {
          // A button pressed in MID-AIR: flip the tool and stop there, never
          // mint a zero-length gesture.
          syncPenButton(e);
          return;
        }
        penRef.current = e.pointerId;
        // NOT `syncPenButton` here, and this is the bug that made the previous
        // fix useless even where the pointer type was right: `setInkMode` is a
        // React state change, `inkMode` is a dependency of this whole effect,
        // and so the rebind's cleanup ran — killing `strokeRef` — one render
        // after the gesture started. The result was an eraser cursor that
        // followed the pen and erased nothing. `startSession` reads the button
        // itself (`eraserButton` below) and erases for this gesture without
        // touching the mode at all; the toolbar catches up on the next hover
        // or pen-up.
      } else if (e.button !== 0) {
        return; // a mouse's right/middle click is not a stroke
      }
      // Try to arm the audio graph here — but do NOT rely on it. A
      // pointerdown is an activation-triggering event only when `pointerType`
      // is "mouse"; for the pen this reader actually writes with, it grants
      // nothing, and a context built now starts suspended. `onUp` arms it
      // again from a pointerup, which for a pen DOES activate. See
      // `PenSound.arm`.
      if (inkSound) penSound().arm();
      startSession(e);
      if (debug) console.debug("[ink] mode", inkMode, "color", inkColor, "width", inkWidth);
    };

    const onMove = (e: PointerEvent): void => {
      if (e.pointerType === "touch") {
        const touch = touchesRef.current.get(e.pointerId);
        if (touch) {
          touch.x = e.clientX;
          touch.y = e.clientY;
        }
        const mid = panMidRef.current;
        const pts = [...touchesRef.current.values()];
        if (mid !== null && pts.length >= 2) {
          const nextMid = {
            x: (pts[0]!.x + pts[1]!.x) / 2,
            y: (pts[0]!.y + pts[1]!.y) / 2,
          };
          viewportPan(mid.x - nextMid.x, mid.y - nextMid.y);
          panMidRef.current = nextMid;
          e.preventDefault();
          return;
        }
        // Not panning: fall through — an opted-in finger gesture continues
        // through the shared session path below.
      }

      const session = strokeRef.current;
      // Movement disarms the long press. Checked before anything else so a
      // stroke, a lasso loop and a selection drag all cancel it alike, without
      // each branch having to remember to.
      if (pressTimerRef.current !== 0 && session && session.pointerId === e.pointerId) {
        const from = session.points[0];
        const origin = wet.getBoundingClientRect();
        const cx0 = from ? from.x * scale + origin.left : e.clientX;
        const cy0 = from ? (pageHeight - from.y) * scale + origin.top : e.clientY;
        if (Math.hypot(e.clientX - cx0, e.clientY - cy0) > LONG_PRESS_SLOP_PX) cancelLongPress();
      }
      if (session === null || session.pointerId !== e.pointerId) {
        // Hover is the ONE safe moment to move the toolbar to the eraser and
        // back: nothing is in flight, so the effect rebind `setInkMode`
        // triggers costs nothing. (Doing it on pointerdown killed the gesture
        // it had just started — see `onDown`.)
        reportPen(e, "移动");
        syncPenButton(e);
        // Hover (pen/mouse) with the eraser active: keep the reach preview on
        // screen even when nothing is being erased. Touch has no hover.
        if (inkMode === "erase" && e.pointerType !== "touch") {
          paintEraserCursor(e.clientX, e.clientY);
          e.preventDefault();
        }
        return;
      }

      if (session.lasso) {
        const pressure = e.pressure > 0 ? e.pressure : 0.5;
        session.points.push(inPageSpace(e.clientX, e.clientY, pressure));
        schedulePaintWet(session, []);
        e.preventDefault();
        return;
      }

      if (session.moving) {
        const origin = wet.getBoundingClientRect();
        const dx = (e.clientX - origin.left) / scale - session.moving.startX;
        const dy = pageHeight - (e.clientY - origin.top) / scale - session.moving.startY;
        session.moving = { ...session.moving, dx, dy };
        movedRef.current = Math.abs(dx) > 1 || Math.abs(dy) > 1;
        movingRef.current = session.moving;
        repaintDry();
        schedulePaintWet(session, []);
        e.preventDefault();
        return;
      }

      if (session.transform) {
        const origin = wet.getBoundingClientRect();
        const x = (e.clientX - origin.left) / scale;
        const y = pageHeight - (e.clientY - origin.top) / scale;
        const t = session.transform;
        const startDist = Math.hypot(t.startX - t.cx, t.startY - t.cy);
        const liveDist = Math.hypot(x - t.cx, y - t.cy);
        const factor = t.kind === "resize" && startDist > 1e-6 ? liveDist / startDist : 1;
        const startAngle = Math.atan2(t.startY - t.cy, t.startX - t.cx);
        const liveAngle = Math.atan2(y - t.cy, x - t.cx);
        const radians = t.kind === "rotate" ? liveAngle - startAngle : 0;
        session.transform = { ...t, factor, radians };
        movedRef.current = true;
        movingRef.current = { ids: t.ids, dx: 0, dy: 0 };
        repaintDry();
        schedulePaintWet(session, []);
        e.preventDefault();
        return;
      }

      if (session.erasing) {
        const origin = wet.getBoundingClientRect();
        const x = (e.clientX - origin.left) / scale;
        const y = pageHeight - (e.clientY - origin.top) / scale;
        if (inkEraseMode === "pixel") eraseAtPixel(x, y);
        else eraseAt(x, y);
        // The preview follows the erase so the user always sees the reach.
        paintEraserCursor(e.clientX, e.clientY);
        e.preventDefault();
        return;
      }

      if (inkMode === "style") {
        const origin = wet.getBoundingClientRect();
        const x = (e.clientX - origin.left) / scale;
        const y = pageHeight - (e.clientY - origin.top) / scale;
        styleAt(x, y);
        e.preventDefault();
        return;
      }

      // Every sample since the last delivery, then the renderer's prediction
      // of where the pen will be next (render-only, discarded each batch).
      const coalesced = e.getCoalescedEvents?.() ?? [];
      const batch = coalesced.length > 0 ? coalesced : [e];
      for (const ev of batch) {
        const pressure = ev.pressure > 0 ? ev.pressure : 0.5;
        session.points.push(inPageSpace(ev.clientX, ev.clientY, pressure));
      }
      if (inkSound && inkMode !== "erase") {
        const prev = lastSampleRef.current;
        const now = e.timeStamp;
        if (prev && now > prev.t) {
          const speed = Math.hypot(e.clientX - prev.x, e.clientY - prev.y) / (now - prev.t);
          penSound().nib(speed);
        }
        lastSampleRef.current = { x: e.clientX, y: e.clientY, t: now };
      }
      const predicted = (e.getPredictedEvents?.() ?? []).map((ev) =>
        inPageSpace(ev.clientX, ev.clientY, ev.pressure > 0 ? ev.pressure : 0.5),
      );
      schedulePaintWet(session, predicted);
      e.preventDefault();
      if (debug && e.pointerType === "pen") {
        console.debug("[ink] move", batch.length, "pred", predicted.length);
      }
    };

    const finish = (e: PointerEvent, cancelled: boolean): void => {
      // The pen has left: whatever this gesture was, it was not a hold.
      cancelLongPress();
      if (e.pointerType === "pen" && penRef.current === e.pointerId) penRef.current = null;
      if (e.pointerType === "touch") {
        touchesRef.current.delete(e.pointerId);
        if (touchesRef.current.size < 2) panMidRef.current = null;
      }
      const session = strokeRef.current;
      if (session === null || session.pointerId !== e.pointerId) return;
      strokeRef.current = null;
      if (wet.hasPointerCapture(e.pointerId)) wet.releasePointerCapture(e.pointerId);

      if (cancelled) {
        // A cancelled gesture was never committed — repaint dry as the truth.
        if (session.erasing && inkEraseMode === "pixel") abandonPixelGesture();
        if (inkMode === "style") abandonStyleGesture();
        movingRef.current = null;
        cancelPendingPaint();
        paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null, transform: null }, []);
        repaintDry();
        return;
      }

      if (session.lasso) {
        // A tiny loop was a tap on empty paper: clear the selection.
        if (session.points.length < 3) {
          setSelection(null);
          clearWet();
          return;
        }
        const caught = mineRef.current.filter((s) => strokeCaughtBy(s.points, session.points));
        // 胶带 is caught by the same loop, on its own outline: a strip's path
        // when it has one, its rotated corners when it does not.
        const caughtTape = tapeRef.current.filter((t) =>
          strokeCaughtBy(
            tapeOutline(t).map((pt) => ({ x: pt.x, y: pt.y, p: 0.5 })),
            session.points,
          ),
        );
        if (caught.length === 0 && caughtTape.length === 0) {
          setSelection(null);
          clearWet();
          return;
        }
        setSelection({ rows: caught, tapes: caughtTape });
        cancelPendingPaint();
        paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null, transform: null }, []);
        // Paint the chrome now rather than waiting a render for the effect —
        // `selRef` is assigned during render, and `setSelection` has not
        // re-rendered yet, so feed it the fresh selection directly.
        selRef.current = { rows: caught, tapes: caughtTape };
        repaintSelectionChrome();
        return;
      }

      if (session.moving) {
        commitMove(session);
        clearWet();
        return;
      }

      if (session.transform) {
        commitTransform(session);
        clearWet();
        return;
      }

      if (session.erasing) {
        if (inkEraseMode === "pixel") {
          const removed = pixelRemovedRef.current;
          const added = pixelAddedRef.current;
          pixelRemovedRef.current = [];
          pixelAddedRef.current = [];
          if (removed.length > 0 || added.length > 0) {
            // The mid-gesture temps were a preview; the canonical swap is
            // one edit op holding the originals and the final parts.
            const addedIds = new Set(added.map((a) => a.id));
            updateCache((prev) => prev.filter((s) => !addedIds.has(s.id)));
            commitReplace(
              removed,
              added.map((a) => ({ points: a.points, color: a.color, width: a.width })),
            );
          }
        } else {
          if (erasedRef.current.length > 0) {
            pushInkOps(key, [{ kind: "remove", strokes: erasedRef.current }]);
            erasedRef.current = [];
          }
        }
        // Touch has no hover to keep showing the reach preview — lift the
        // finger, the circle goes too. Pen/mouse keep it for the next drag.
        if (e.pointerType === "touch") clearWet();
        return;
      }

      if (inkMode === "style") {
        const removed = styleRemovedRef.current;
        const added = styleAddedRef.current;
        styleRemovedRef.current = [];
        styleAddedRef.current = [];
        if (removed.length > 0 || added.length > 0) {
          // Same shape as the 局部 eraser's commit: the mid-gesture temps
          // were a preview, the canonical swap is one edit op.
          const addedIds = new Set(added.map((a) => a.id));
          updateCache((prev) => prev.filter((s) => !addedIds.has(s.id)));
          commitReplace(
            removed,
            added.map((a) => ({ points: a.points, color: a.color, width: a.width })),
          );
        }
        if (e.pointerType === "touch") clearWet();
        return;
      }

      if (inkMode === "laser") {
        // Never persisted, never undoable — a pointer, not a note. The mark
        // joins the standing trail, and the trail holds at full brightness
        // until the pen has been QUIET for LASER_HOLD_MS, then fades out
        // together. Drawing again before that resets the clock, so an
        // explanation in progress never has the floor pulled out from under
        // it (the first version faded each mark 250ms after its own pen-up).
        if (session.points.length >= 2) laserMarksRef.current.push(session.points);
        laserActiveAtRef.current = performance.now();
        if (laserFadeRef.current) cancelAnimationFrame(laserFadeRef.current);
        const step = (): void => {
          const wetNow = wetRef.current;
          if (!wetNow) return;
          const sized = prepare(wetNow);
          if (!sized) return;
          clearInSpace(wetNow, sized.w);
          const idle = performance.now() - laserActiveAtRef.current;
          const alpha = Math.max(0, 1 - Math.max(0, idle - LASER_HOLD_MS) / LASER_FADE_MS);
          if (alpha <= 0) {
            laserMarksRef.current = [];
            laserFadeRef.current = 0;
            return;
          }
          paintLaserMarks(wetNow.getContext("2d")!, alpha);
          laserFadeRef.current = requestAnimationFrame(step);
        };
        laserFadeRef.current = requestAnimationFrame(step);
        return;
      }

      let points = session.points;
      if (points.length > MAX_POINTS) {
        const stride = Math.ceil(points.length / THIN_THRESHOLD);
        points = points.filter((_, i) => i % stride === 0 || i === points.length - 1);
      }
      // OPTIMISTIC COMMIT, and why the flicker it fixes matters: clearing the
      // wet layer and waiting for the POST round trip left the stroke
      // *invisible* for the whole network window — the tablet tester saw
      // every pen lift blink the stroke away. Instead the finished stroke
      // enters the cache (and the dry layer) immediately under a temp id,
      // the wet layer clears on the same frame, and the server's row simply
      // replaces the temp when it arrives. Same geometry either way, so the
      // swap is invisible; the only visible difference is the end taper,
      // which the temp already carries (last:true outline).
      const temp: InkStrokeRow = {
        id: `temp-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
        paper_id: paperId,
        kind,
        page,
        points,
        color: inkColor,
        width: inkWidth,
        created_at: new Date().toISOString(),
      };
      updateCache((prev) => [...prev, temp]);
      pushInkOps(key, [{ kind: "add", stroke: temp }]);
      cancelPendingPaint();
      paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null, transform: null }, []);
      const settleStack = (row: InkStrokeRow): void => {
        useUI.setState((s) => {
          if (s.inkOpsKey !== key) return s;
          const past = s.inkPast.slice();
          const last = past[past.length - 1];
          if (last && last.kind === "add" && last.stroke.id === temp.id) {
            past[past.length - 1] = { kind: "add", stroke: row };
          }
          return { inkPast: past };
        });
      };
      void api.ink
        .create(paperId, { kind, page, points, color: inkColor, width: inkWidth })
        .then((row) => {
          updateCache((prev) => prev.map((s) => (s.id === temp.id ? row : s)));
          settleStack(row);
        })
        .catch(() => {
          // The server refused it: roll the optimistic stroke back out of the
          // cache and the undo stack, then let the refetch confirm the truth.
          updateCache((prev) => prev.filter((s) => s.id !== temp.id));
          useUI.setState((s) =>
            s.inkOpsKey === key
              ? { inkPast: s.inkPast.filter((op) => !(op.kind === "add" && op.stroke.id === temp.id)) }
              : s,
          );
          void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
        });
    };

    /**
     * Give back a tool the barrel button borrowed — never take one.
     *
     * The asymmetry is deliberate. BORROWING on pen-up would set `inkMode` and
     * rebind this effect for a stroke that has already finished, for no gain:
     * the next hover (or the next pointerdown's own `eraserButton` check) will
     * do it in time. RELEASING has to happen here, because the button may well
     * have been let go DURING the stroke, and on a tablet there may be no
     * hover event afterward to notice it — leaving the reader stuck in an
     * eraser they never chose.
     */
    const releasePenButton = (e: PointerEvent): void => {
      if (!isStylus(e)) return;
      if (barrelPrevModeRef.current === null) return;
      if (penEraseHeld(e)) return; // still held: keep erasing
      setInkMode(barrelPrevModeRef.current);
      barrelPrevModeRef.current = null;
    };

    const onUp = (e: PointerEvent): void => {
      lastSampleRef.current = null;
      // A pen's pointerup IS an activation-triggering event, unlike its
      // pointerdown — so this is the call that actually gets the audio device
      // running on a tablet, in time for the NEXT stroke.
      if (inkSound) {
        penSound().arm();
        penSound().lift();
      }
      reportPen(e, "抬起");
      finish(e, false);
      releasePenButton(e);
    };
    const onCancel = (e: PointerEvent): void => {
      lastSampleRef.current = null;
      if (inkSound) penSound().lift();
      finish(e, true);
      releasePenButton(e);
    };
    // Pen drags emit compatibility mouse events that would bubble to the
    // viewport's pan handler mid-stroke; the wet canvas is the target, so
    // this is where they stop.
    const stopMouse = (e: MouseEvent): void => e.stopPropagation();
    // Left the page: the hover preview has no position to sit at, and a
    // barrel button borrowed the eraser mid-hover has no more samples coming
    // to tell it the button lifted — give the tool back now rather than
    // strand the reader in "erase" until they hover back in.
    const onLeave = (): void => {
      if (barrelPrevModeRef.current !== null) {
        setInkMode(barrelPrevModeRef.current);
        barrelPrevModeRef.current = null;
      }
      clearWet();
      // …but put the selection's frame back. The wet canvas is shared between
      // the hover preview and the selection chrome, and moving the pointer off
      // the page is the single most common way to reach the selection's own
      // toolbar — so wiping the canvas here used to erase the box the reader
      // was about to act on.
      repaintSelectionChrome();
    };

    wet.addEventListener("pointerdown", onDown);
    wet.addEventListener("pointermove", onMove);
    wet.addEventListener("pointerup", onUp);
    wet.addEventListener("pointercancel", onCancel);
    wet.addEventListener("pointerleave", onLeave);
    wet.addEventListener("mousedown", stopMouse);
    return () => {
      wet.removeEventListener("pointerdown", onDown);
      wet.removeEventListener("pointermove", onMove);
      wet.removeEventListener("pointerup", onUp);
      wet.removeEventListener("pointercancel", onCancel);
      wet.removeEventListener("pointerleave", onLeave);
      wet.removeEventListener("mousedown", stopMouse);
      // A gesture still down when the handlers rebind (tool switch, zoom,
      // data change) never commits: the 局部 account is thrown away, the
      // carried strokes go back onto the paper, and any preview is wiped.
      if (strokeRef.current !== null) {
        const dead = strokeRef.current;
        strokeRef.current = null;
        if (dead.erasing && inkEraseMode === "pixel") abandonPixelGesture();
        if (inkMode === "style") abandonStyleGesture();
        movingRef.current = null;
      }
      // A frame queued by `schedulePaintWet` would repaint a gesture that no
      // longer exists, onto a canvas about to be cleared.
      cancelPendingPaint();
      cancelLongPress();
      // The laser trail belongs to the tool, not to a gesture: a rebind means
      // the tool (or zoom) changed under it, and marks measured at the old
      // scale must not be repainted at the new one. Outside the gesture check
      // above on purpose — the common case is a tool switch with nothing down.
      clearLaser();
      // Rebinding (tool change, zoom, data) invalidates any preview painted
      // under the previous settings.
      clearWet();
    };
  }, [
    inkMode,
    setInkMode,
    inkColor,
    inkWidth,
    inkFingerDraw,
    inkSound,
    inkEraserSize,
    inkEraseMode,
    scale,
    pageHeight,
    eraseAt,
    eraseAtPixel,
    abandonPixelGesture,
    paintWet,
    repaintDry,
    viewportPan,
    paintEraserCursor,
    clearWet,
    schedulePaintWet,
    cancelPendingPaint,
    cancelLongPress,
    paintSelection,
    paintSelectionHandles,
    repaintSelectionChrome,
    paintLaserMarks,
    clearLaser,
    closeInkTray,
    setInkCarried,
    setPenProbe,
    commitMove,
    commitTransform,
    pushInkOps,
    key,
    kind,
    page,
    paperId,
    updateCache,
    qc,
  ]);

  /* ------------------------------------------------- selection commands */

  /** Delete everything the loop caught — strokes and 胶带 together, one undo
   *  entry whatever the mix. */
  const deleteSelection = useCallback(
    (rows: InkStrokeRow[], tapes: TapeRow[]) => {
      setSelection(null);
      const ops: InkOp[] = [];
      if (rows.length > 0) {
        const gone = new Set(rows.map((r) => r.id));
        updateCache((prev) => prev.filter((s) => !gone.has(s.id)));
        ops.push({ kind: "remove", strokes: rows });
        for (const row of rows) {
          if (row.id.startsWith("temp-")) continue;
          void api.ink.remove(row.id).catch(() => {
            void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
          });
        }
      }
      for (const t of tapes) {
        ops.push({ kind: "tape-remove", tape: t });
        qc.setQueryData<TapeRow[]>(["tape", paperId, kind], (prev) =>
          (prev ?? []).filter((row) => row.id !== t.id),
        );
        void api.tape.remove(t.id).catch(() => {
          void qc.invalidateQueries({ queryKey: ["tape", paperId, kind] });
        });
      }
      // One gesture, one undo — the same rule the lasso's drag follows.
      if (ops.length > 0) pushInkOps(key, batchOps(ops));
    },
    [key, kind, paperId, pushInkOps, qc, updateCache],
  );

  /** 复制 / 剪切 — the same snapshot; 剪切 also removes what it took. */
  const copySelection = useCallback(
    (rows: InkStrokeRow[], tapes: TapeRow[], cut: boolean) => {
      setClipboard({
        strokes: rows.map((r) => ({ points: r.points, color: r.color, width: r.width })),
        tapes: tapes.map((t) => ({
          x: t.x,
          y: t.y,
          w: t.w,
          h: t.h,
          angle: t.angle,
          points: t.points,
          revealed: t.revealed,
        })),
      });
      if (cut) deleteSelection(rows, tapes);
      else setSelection(null);
    },
    [deleteSelection, setClipboard],
  );

  /**
   * 粘贴 — new rows on THIS page, nudged clear of whatever they were copied
   * from, and selected so the obvious next move (drag them somewhere) needs
   * no extra step.
   *
   * Clamped onto the page rather than pasted at the raw offset: a selection
   * copied from the bottom of one page and pasted onto a shorter one would
   * otherwise land past its edge, where it is invisible and unselectable.
   */
  /**
   * Put a text box or a sticky note where the pen was held.
   *
   * Lives here rather than in `NoteLayer` because the gesture that asks for it
   * is a pen press on the ink canvas, and only this layer sees those. The row
   * goes straight into `NoteLayer`'s query cache and its id into
   * `noteFocusId`, which is how the caret ends up in a box created by a
   * component that does not render it.
   */
  const addNoteAt = useCallback(
    async (x: number, y: number, style: PageNoteStyle) => {
      try {
        const row = await api.note.create(paperId, {
          kind,
          page,
          x,
          y,
          w: NEW_NOTE_W,
          h: NEW_NOTE_H,
          style,
          color: noteColor,
        });
        qc.setQueryData<PageNoteRow[]>(["note", paperId, kind], (prev) => [...(prev ?? []), row]);
        setNoteFocus(row.id);
      } catch {
        /* refused — nothing was optimistically added, so nothing to undo */
      }
    },
    [paperId, kind, page, noteColor, qc, setNoteFocus],
  );

  const pasteClipboardAt = useCallback(
    (clip: InkClipboard, atX?: number, atY?: number) => {
      const wet = wetRef.current;
      if (!wet) return;
      const pageWidth = wet.clientWidth / scale;
      const NUDGE = 14;
      const boxes = [
        ...clip.strokes.map((s) => strokeBounds(s.points)),
        ...clip.tapes.map((t) => tapeBox(t as TapeRow)),
      ];
      if (boxes.length === 0) return;
      const b = unionBounds(boxes);
      // Two ways to place a paste, and the difference matters. From the
      // selection bar there is no target, so it lands next to where it came
      // from — a visible copy you can then drag. From the long-press menu
      // there IS a target: the spot the pen was held, which the reader chose
      // precisely so the paste would go THERE, so its centre goes on it.
      const toPoint = atX !== undefined && atY !== undefined;
      let dx = toPoint ? atX - (b.x0 + b.x1) / 2 : NUDGE;
      let dy = toPoint ? atY - (b.y0 + b.y1) / 2 : -NUDGE;
      // Keep the whole paste on the paper.
      dx = Math.min(dx, pageWidth - 4 - b.x1);
      dx = Math.max(dx, 4 - b.x0);
      dy = Math.min(dy, pageHeight - 4 - b.y1);
      dy = Math.max(dy, 4 - b.y0);

      // Optimistic, like every other write here: the copies appear under the
      // pen immediately and the server's rows replace the temps as they land.
      const temps: InkStrokeRow[] = clip.strokes.map((s) => ({
        id: `temp-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
        paper_id: paperId,
        kind,
        page,
        points: translatePoints(s.points, dx, dy),
        color: s.color,
        width: s.width,
        created_at: new Date().toISOString(),
      }));
      if (temps.length > 0) updateCache((prev) => [...prev, ...temps]);
      setSelection({ rows: temps, tapes: [] });

      void (async () => {
        // The undo ops are built HERE, from the rows the server actually
        // returned — never from the temps. An op pushed with a temp id could
        // not be repaired afterwards: `remapInkRow` rewrites the two history
        // stacks, and an op that is not on one yet is not there to rewrite.
        // Undo would then DELETE `temp-1234…`, get a 404, and leave the paste
        // sitting on the page as something the reader could not take back.
        const ops: InkOp[] = [];
        for (const temp of temps) {
          try {
            const row = await api.ink.create(paperId, {
              kind,
              page,
              points: temp.points,
              color: temp.color,
              width: temp.width,
            });
            updateCache((prev) => prev.map((s) => (s.id === temp.id ? row : s)));
            setSelection((sel) =>
              sel ? { ...sel, rows: sel.rows.map((r) => (r.id === temp.id ? row : r)) } : sel,
            );
            ops.push({ kind: "add", stroke: row });
          } catch {
            updateCache((prev) => prev.filter((s) => s.id !== temp.id));
            setSelection((sel) =>
              sel ? { ...sel, rows: sel.rows.filter((r) => r.id !== temp.id) } : sel,
            );
          }
        }
        for (const t of clip.tapes) {
          const moved = translateTape({ ...t }, dx, dy);
          try {
            const row = await api.tape.create(paperId, {
              kind,
              page,
              x: moved.x,
              y: moved.y,
              w: clampTapeSize(moved.w),
              h: clampTapeSize(moved.h),
              angle: moved.angle,
              ...(moved.points ? { points: moved.points } : {}),
            });
            qc.setQueryData<TapeRow[]>(["tape", paperId, kind], (prev) => [...(prev ?? []), row]);
            setSelection((sel) => (sel ? { ...sel, tapes: [...sel.tapes, row] } : sel));
            ops.push({ kind: "tape-add", tape: row });
          } catch {
            /* the server refused this strip; the rest of the paste stands */
          }
        }
        // One paste, one undo — and pushed only once everything that will
        // exist does, so the batch never names a row the server refused.
        if (ops.length > 0) pushInkOps(key, batchOps(ops));
      })();
    },
    [key, kind, page, pageHeight, paperId, pushInkOps, qc, scale, updateCache],
  );

  /* ----------------------------------------------------------------- view */

  /**
   * Does this layer claim the pointer at all?
   *
   * NOT simply "a tool is selected": in 胶带 and 文本 mode the gesture effect
   * above returns immediately, so a live canvas would sit over the page
   * swallowing pointers and suppressing touch scrolling on behalf of code that
   * does nothing. That is the shape of bug that made 锁定画布 look broken, and
   * it is cheap to not have twice.
   */
  const interactive = inkMode !== "off" && inkMode !== "tape" && inkMode !== "text";
  /**
   * Where the command bar sits: centred over the selection's own frame, just
   * above the rotate handle — the placement in the reference, and the one that
   * does not cover the marks the buttons act on. It used to be anchored
   * wherever the lasso happened to stop, which is an arbitrary point on the
   * loop and very often right on top of what was caught.
   *
   * It flips below the frame when there is no room above, so a selection near
   * the top of a page still has reachable buttons.
   */
  const selChrome = useMemo(() => {
    if (!selection || selection.rows.length + selection.tapes.length === 0) return null;
    const box = paddedBox(selectionBox(selection.rows, selection.tapes), scale);
    const left = ((box.x0 + box.x1) / 2) * scale;
    const above = (pageHeight - box.y1) * scale - ROTATE_GAP_PX - 14;
    const below = (pageHeight - box.y0) * scale + 14;
    const flip = above < 8;
    return { left, top: flip ? below : above, flip };
  }, [selection, scale, pageHeight]);
  const selCount = (selection?.rows.length ?? 0) + (selection?.tapes.length ?? 0);

  return (
    <>
      <canvas ref={waterRef} className="ph-ink-water" aria-hidden="true" />
      <canvas ref={wetWaterRef} className="ph-ink-water ph-ink-water--wet" aria-hidden="true" />
      <canvas ref={dryRef} className="ph-ink" aria-hidden="true" />
      <canvas
        ref={wetRef}
        className={
          "ph-ink ph-ink--wet" +
          (interactive ? " ph-ink--live" : "") +
          (inkMode === "erase" ? " ph-ink--erase" : "") +
          (inkMode === "select" ? " ph-ink--select" : "")
        }
        aria-hidden="true"
      />
      {/*
          The long-press menu: 粘贴 / 文本框 / 便利贴.

          Anchored where the pen was held, because that is the whole point of
          the gesture — each of these three needs a location, and none of them
          has a sensible toolbar equivalent ("paste where?"). Dismissed by any
          choice, and by a tap anywhere else.
      */}
      {pressMenu && (
        <>
          <div
            className="ph-ink-press-veil"
            onPointerDown={() => setPressMenu(null)}
            aria-hidden="true"
          />
          <div
            className="ph-ink-press"
            role="menu"
            aria-label="在此处插入"
            style={{ left: pressMenu.left, top: pressMenu.top }}
          >
            <button
              role="menuitem"
              className="ph-ink-press-btn"
              disabled={clipboard === null}
              title={clipboard === null ? "还没有剪切或复制过东西" : "把剪贴板里的内容粘到这里"}
              onClick={() => {
                const at = pressMenu;
                setPressMenu(null);
                if (clipboard) pasteClipboardAt(clipboard, at.x, at.y);
              }}
            >
              粘贴
            </button>
            <button
              role="menuitem"
              className="ph-ink-press-btn"
              onClick={() => {
                const at = pressMenu;
                setPressMenu(null);
                void addNoteAt(at.x, at.y, "text");
              }}
            >
              文本框
            </button>
            <button
              role="menuitem"
              className="ph-ink-press-btn"
              onClick={() => {
                const at = pressMenu;
                setPressMenu(null);
                void addNoteAt(at.x, at.y, "note");
              }}
            >
              便利贴
            </button>
          </div>
        </>
      )}
      {selection && selChrome && (
        <div
          className={`ph-ink-selbar${selChrome.flip ? " is-below" : ""}`}
          role="toolbar"
          aria-label="选中的笔迹"
          style={{ left: selChrome.left, top: selChrome.top }}
          // Buttons are HTML, not canvas: clicks here must not start a lasso.
          onPointerDown={(e) => e.stopPropagation()}
        >
          <button
            className="ph-ink-sel-cmd"
            onClick={() => copySelection(selection.rows, selection.tapes, true)}
          >
            剪切
          </button>
          <button
            className="ph-ink-sel-cmd"
            onClick={() => copySelection(selection.rows, selection.tapes, false)}
          >
            复制
          </button>
          <button
            className="ph-ink-sel-cmd"
            disabled={clipboard === null}
            title={clipboard === null ? "还没有剪切或复制过东西" : "粘贴到这一页"}
            onClick={() => clipboard && pasteClipboardAt(clipboard)}
          >
            粘贴
          </button>
          <button
            className="ph-ink-sel-cmd"
            onClick={() => deleteSelection(selection.rows, selection.tapes)}
          >
            删除
          </button>
          <button
            className={`ph-ink-sel-cmd${styleOpen ? " is-on" : ""}`}
            aria-pressed={styleOpen}
            disabled={selection.rows.length === 0}
            title={
              selection.rows.length === 0
                ? "只选中了胶带，没有可改样式的笔迹"
                : "改颜色和粗细"
            }
            onClick={() => setStyleOpen((v) => !v)}
          >
            更改风格
          </button>
          <span className="ph-ink-sel-n">{selCount}</span>
          <button
            className="ph-ink-sel-btn"
            title="取消选择"
            aria-label="取消选择"
            onClick={() => setSelection(null)}
          >
            <Icons.close size={11} />
          </button>
          {styleOpen && selection.rows.length > 0 && (
            <div className="ph-ink-sel-style" role="group" aria-label="更改风格">
              {selStyleColors.map((c) => (
                <button
                  key={c.key}
                  className={`ph-ink-sel-color${
                    selection.rows.every((r) => r.color === c.key) ? " is-on" : ""
                  }`}
                  style={{ background: `var(--c-ink-${c.key}, var(--c-tx))` }}
                  title={c.label}
                  aria-label={`改为${c.label}`}
                  onClick={() => {
                    const rows = selection.rows;
                    setStyleOpen(false);
                    setSelection(null);
                    commitReplace(
                      rows,
                      rows.map((r) => ({ points: r.points, color: c.key, width: r.width })),
                    );
                  }}
                />
              ))}
              <input
                type="range"
                className="ph-ink-sel-width"
                min={MIN_INK_WIDTH}
                max={MAX_INK_WIDTH}
                step={1}
                defaultValue={Math.round(selection.rows[0]?.width ?? 2)}
                aria-label="笔画粗细"
                // Committed on release, not on every input event: dragging a
                // slider that rewrites every selected row per pixel would post
                // a hundred replacements and a hundred undo entries.
                onPointerUp={(e) => {
                  const width = clampInkWidth(Number((e.target as HTMLInputElement).value));
                  const rows = selection.rows;
                  setStyleOpen(false);
                  setSelection(null);
                  commitReplace(
                    rows,
                    rows.map((r) => ({ points: r.points, color: r.color, width })),
                  );
                }}
              />
            </div>
          )}
        </div>
      )}
    </>
  );
}

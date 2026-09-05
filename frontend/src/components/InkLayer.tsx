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
import type { InkPoint, InkStrokeRow } from "../api/types";
import {
  INK_COLORS,
  isWaterColor,
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
import { isDrawingPointer } from "../lib/pointer";
import { clampInkWidth, useUI, type InkMode } from "../store";
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

const penEraseHeld = (e: PointerEvent): boolean =>
  e.pointerType === "pen" && (e.buttons & PEN_ERASE_BUTTONS) !== 0;

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
const HANDLE_HIT_PX = 18;
const ROTATE_GAP_PX = 24;

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

/** The selection itself: rows plus a toolbar anchor in CSS page pixels. */
interface Selection {
  rows: InkStrokeRow[];
  ax: number;
  ay: number;
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
  const wetRef = useRef<HTMLCanvasElement>(null);

  const inkMode = useUI((s) => s.inkMode);
  const setInkMode = useUI((s) => s.setInkMode);
  const inkColor = useUI((s) => s.inkColor);
  const inkColorUsage = useUI((s) => s.inkColorUsage);
  // The selection recolour bar is a quick action on a floating popover, not
  // a place to browse the whole palette — same quick 4 the draw toolbar
  // leads with (更多颜色 in that toolbar reaches the rest).
  const selColors = useMemo(() => {
    const keys = rankInkColors(INK_COLORS, inkColorUsage, Date.now());
    return keys.map((key) => INK_COLORS.find((c) => c.key === key)).filter((c) => c != null);
  }, [inkColorUsage]);
  const inkWidth = useUI((s) => s.inkWidth);
  const inkFingerDraw = useUI((s) => s.inkFingerDraw);
  const inkEraserSize = useUI((s) => s.inkEraserSize);
  const inkEraseMode = useUI((s) => s.inkEraseMode);
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

  /** Dashed 1.5px-device outline, in PDF space. */
  const dashedRect = useCallback(
    (ctx: CanvasRenderingContext2D, b: { x0: number; y0: number; x1: number; y1: number }, pad: number) => {
      ctx.setLineDash([4 / scale, 3 / scale]);
      ctx.lineWidth = 1.5 / scale;
      ctx.strokeStyle = "rgba(28, 32, 40, 0.9)";
      ctx.strokeRect(b.x0 - pad, b.y0 - pad, b.x1 - b.x0 + pad * 2, b.y1 - b.y0 + pad * 2);
      ctx.setLineDash([]);
    },
    [scale],
  );

  /** The selection marching ants: one padded box per caught stroke. */
  const paintSelection = useCallback(
    (ctx: CanvasRenderingContext2D, rows: InkStrokeRow[]) => {
      for (const stroke of rows) {
        if (stroke.points.length === 0) continue;
        const b = strokeBounds(stroke.points);
        dashedRect(ctx, b, stroke.width / 2 + 2);
      }
    },
    [dashedRect],
  );

  /** The resize corners + rotate handle around the WHOLE selection (a single
   *  combined box, not one per stroke — dragging one handle transforms the
   *  group together). Idle only: a live resize/rotate repaints its own
   *  preview instead (see `paintWet`'s `transform` branch), so the handles
   *  never fight the drag they belong to. */
  const paintSelectionHandles = useCallback(
    (ctx: CanvasRenderingContext2D, rows: InkStrokeRow[]) => {
      if (rows.length === 0) return;
      const box = unionBounds(rows.map((r) => strokeBounds(r.points)));
      const cx = (box.x0 + box.x1) / 2;
      const r = 5 / scale;
      const gap = ROTATE_GAP_PX / scale;
      ctx.strokeStyle = "rgba(28, 32, 40, 0.9)";
      ctx.lineWidth = 1 / scale;
      ctx.beginPath();
      ctx.moveTo(cx, box.y1);
      ctx.lineTo(cx, box.y1 + gap);
      ctx.stroke();
      for (const h of selectionHandles(box, scale)) {
        ctx.beginPath();
        if (h.kind === "rotate") {
          ctx.arc(h.x, h.y, r, 0, Math.PI * 2);
        } else {
          ctx.rect(h.x - r, h.y - r, r * 2, r * 2);
        }
        ctx.fillStyle = "#fff";
        ctx.fill();
        ctx.stroke();
      }
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
   *  order as the temp rows this function mints. */
  const commitReplace = useCallback(
    (removedRows: InkStrokeRow[], specs: { points: InkPoint[]; color: string; width: number }[]) => {
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
      pushInkOps(key, [{ kind: "edit", removed: removedRows, added: temps }]);

      /** Point one settled row at its temp, in the cache and in the undo op. */
      const settle = (tempId: string, row: InkStrokeRow): void => {
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
        }
      })();
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

  /** Empty the wet layer (hover cursor gone, gesture ghost gone). */
  const clearWet = useCallback(() => {
    wetRef.current?.getContext("2d")?.clearRect(0, 0, wetRef.current.width, wetRef.current.height);
  }, []);

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
   *  - draw: the live outline, plus the predicted tail;
   *  - lasso: the dashed loop;
   *  - move: the carried strokes at their live offset, selection ants over them. */
  const paintWet = useCallback(
    (session: Session, predicted: InkPoint[]) => {
      const wet = wetRef.current;
      if (!wet) return;
      const sized = prepare(wet);
      if (!sized) return;
      clearInSpace(wet, sized.w);
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
          // Close the loop visually so the catch area is never a surprise.
          const first = session.points[0]!;
          const last = session.points[session.points.length - 1]!;
          if (session.points.length > 2 && Math.hypot(first.x - last.x, first.y - last.y) > 8 / scale) {
            ctx.lineTo(first.x, first.y);
          }
          ctx.stroke();
          ctx.setLineDash([]);
        }
        return;
      }
      if (session.moving) {
        const rows = (selRef.current?.rows ?? []).filter((r) => session.moving!.ids.has(r.id));
        for (const stroke of rows) {
          paintStroke(
            ctx,
            {
              points: translatePoints(stroke.points, session.moving.dx, session.moving.dy),
              width: stroke.width,
              color: stroke.color,
            },
            colorOf,
          );
        }
        paintSelection(ctx, rows);
        return;
      }
      if (session.transform) {
        const { ids, cx, cy, factor, radians } = session.transform;
        const rows = (selRef.current?.rows ?? []).filter((r) => ids.has(r.id));
        const preview = rows.map((r) => ({
          ...r,
          points:
            session.transform!.kind === "resize"
              ? scalePoints(r.points, cx, cy, factor)
              : rotatePoints(r.points, cx, cy, radians),
          width: session.transform!.kind === "resize" ? r.width * Math.max(0.05, factor) : r.width,
        }));
        for (const stroke of preview) {
          paintStroke(ctx, { points: stroke.points, width: stroke.width, color: stroke.color }, colorOf);
        }
        paintSelection(ctx, preview);
        return;
      }
      if (session.erasing) return;
      // The trail already on screen has to be repainted with the live stroke:
      // this canvas is cleared every batch, so anything not redrawn here
      // blinks out the moment the next sweep starts.
      if (inkMode === "laser") paintLaserMarks(ctx, 1);
      const live = [...session.points, ...predicted];
      const width = inkMode === "laser" ? LASER_WIDTH : inkWidth;
      const outline = strokeOutline(live, width, true);
      if (outline.length >= 3) {
        if (inkMode === "laser") {
          // 炫一点: a 七彩 gradient with a glow, not a flat fill — the pointer
          // is meant to catch the eye for the seconds it lives, unlike ink
          // meant to be read calmly afterward.
          ctx.save();
          ctx.shadowColor = LASER_STOPS[0]!;
          ctx.shadowBlur = 14 / scale;
          paintOutline(ctx, outline, laserGradient(ctx, live));
          ctx.restore();
        } else {
          paintOutline(ctx, outline, colorOf(inkColor));
        }
      }
    },
    [
      prepare,
      clearInSpace,
      colorResolver,
      inkWidth,
      inkColor,
      inkMode,
      scale,
      paintSelection,
      paintLaserMarks,
      laserGradient,
    ],
  );

  /* --------------------------------------------------- selection overlays */

  /** Marching ants follow the zoom; also repaint on selection/stroke changes. */
  useEffect(() => {
    if (!selection || selection.rows.length === 0) return;
    const wet = wetRef.current;
    if (!wet) return;
    const sized = prepare(wet);
    if (!sized) return;
    clearInSpace(wet, sized.w);
    const ctx = wet.getContext("2d")!;
    paintSelection(ctx, selection.rows);
    paintSelectionHandles(ctx, selection.rows);
  }, [selection, prepare, clearInSpace, paintSelection, paintSelectionHandles]);

  /* Clear a selection the tool no longer supports — and when the strokes it
     holds have gone from the cache (another view deleted them). */
  useEffect(() => {
    if (!selection) return;
    if (inkMode !== "select") {
      setSelection(null);
      return;
    }
    const live = new Set(mine.map((s) => s.id));
    if (selection.rows.some((r) => !live.has(r.id))) {
      setSelection(null);
    }
  }, [inkMode, mine, selection]);

  /** Commit the drag: translate the selection by the live delta, swap rows. */
  const commitMove = useCallback(
    (session: Session) => {
      const moving = session.moving;
      if (!moving || (moving.dx === 0 && moving.dy === 0)) {
        movingRef.current = null;
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
      setSelection(null);
      commitReplace(rows, specs);
    },
    [commitReplace, repaintDry],
  );

  /** Commit a resize or rotate: apply the live factor/angle once, swap rows —
   *  same shape as `commitMove`, a different geometry function. */
  const commitTransform = useCallback(
    (session: Session) => {
      const t = session.transform;
      if (!t || (t.factor === 1 && t.radians === 0)) {
        movingRef.current = null;
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
      setSelection(null);
      commitReplace(rows, specs);
    },
    [commitReplace, repaintDry],
  );

  /* ------------------------------------------------- native event handlers */

  useEffect(() => {
    // "tape" is TapeLayer's own tool — nobody should be drawing ink while a
    // strip is being placed, so this layer goes fully inert, same as "off".
    if (inkMode === "off" || inkMode === "tape") return;
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

    /** Does (x, y) in PDF space sit inside any selected stroke's bounds? */
    const overSelection = (x: number, y: number): boolean => {
      const sel = selRef.current;
      if (!sel) return false;
      for (const stroke of sel.rows) {
        if (stroke.points.length === 0) continue;
        const b = strokeBounds(stroke.points);
        const pad = stroke.width / 2 + 4;
        if (x >= b.x0 - pad && x <= b.x1 + pad && y >= b.y0 - pad && y <= b.y1 + pad) return true;
      }
      return false;
    };

    /** Which resize/rotate handle (x, y) is over, checked BEFORE a plain
     *  drag-to-move so a touch near a corner grabs the handle, not the
     *  stroke under it. */
    const handleAt = (x: number, y: number): { kind: HandleKind; x: number; y: number } | null => {
      const sel = selRef.current;
      if (!sel || sel.rows.length === 0) return null;
      const box = unionBounds(sel.rows.map((r) => strokeBounds(r.points)));
      const reach = HANDLE_HIT_PX / scale;
      for (const h of selectionHandles(box, scale)) {
        if (Math.hypot(x - h.x, y - h.y) <= reach) return h;
      }
      return null;
    };

    const startSession = (e: PointerEvent): void => {
      if (strokeRef.current !== null) return; // one gesture at a time
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
          const ids = new Set(sel.rows.map((r) => r.id));
          const box = unionBounds(sel.rows.map((r) => strokeBounds(r.points)));
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
          const ids = new Set(sel.rows.map((r) => r.id));
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
      touchesRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touchesRef.current.size === 2) {
        // Second finger: abandon the uncommitted gesture (wipe its wet
        // ghost) and become a pan.
        strokeRef.current = null;
        movingRef.current = null;
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
      if (e.pointerType !== "pen") return;
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

    const onDown = (e: PointerEvent): void => {
      e.preventDefault();
      if (e.pointerType === "touch") {
        onTouchDown(e);
        return;
      }
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
      const isPen = e.pointerType === "pen";
      if (isPen) {
        syncPenButton(e);
        // `button === 0` is the tip transition; `buttons & 1` is the tip being
        // held. Either will do: a driver that under-reports `buttons` on
        // pointerdown would otherwise stop the pen drawing AT ALL, which is a
        // far worse failure than the side button not being noticed.
        const tipDown = e.button === 0 || (e.buttons & PEN_TIP_BUTTON) !== 0;
        if (!tipDown) return; // a button pressed in mid-air: mode only
        penRef.current = e.pointerId;
      } else if (e.button !== 0) {
        return; // a mouse's right/middle click is not a stroke
      }
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
      if (session === null || session.pointerId !== e.pointerId) {
        // Hover is still the NICEST moment to borrow the eraser (the tool is
        // already switched by the time the tip lands), it just is not the
        // only one any more — `syncPenButton` is called from down/up too.
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
        paintWet(session, []);
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
        paintWet(session, []);
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
        paintWet(session, []);
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
      const predicted = (e.getPredictedEvents?.() ?? []).map((ev) =>
        inPageSpace(ev.clientX, ev.clientY, ev.pressure > 0 ? ev.pressure : 0.5),
      );
      paintWet(session, predicted);
      e.preventDefault();
      if (debug && e.pointerType === "pen") {
        console.debug("[ink] move", batch.length, "pred", predicted.length);
      }
    };

    const finish = (e: PointerEvent, cancelled: boolean): void => {
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
        if (caught.length === 0) {
          setSelection(null);
          clearWet();
          return;
        }
        const rect = wet.getBoundingClientRect();
        const last = session.points[session.points.length - 1]!;
        // Anchor in CSS page pixels, clamped so the toolbar stays on the page.
        const ax = Math.min(Math.max((last.x * scale), 8), Math.max(8, rect.width - 8));
        const ay = Math.min(Math.max((pageHeight - last.y) * scale, 8), Math.max(8, rect.height - 8));
        setSelection({ rows: caught, ax, ay });
        paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null, transform: null }, []);
        // Repaint the ants for the fresh selection.
        const sized = prepare(wet);
        if (sized) {
          const ctx = wet.getContext("2d")!;
          paintSelection(ctx, caught);
          paintSelectionHandles(ctx, caught);
        }
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

    const onUp = (e: PointerEvent): void => {
      finish(e, false);
      // After the gesture is closed out, not before: `syncPenButton` refuses
      // to act while one is in flight, and on pointerup the button bits are
      // already whatever they will be for the next stroke. This is what
      // releases the borrowed eraser when the button was let go DURING a
      // stroke rather than while hovering.
      syncPenButton(e);
    };
    const onCancel = (e: PointerEvent): void => {
      finish(e, true);
      syncPenButton(e);
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
    paintSelection,
    paintSelectionHandles,
    paintLaserMarks,
    clearLaser,
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

  /* ----------------------------------------------------------------- view */

  const interactive = inkMode !== "off";
  return (
    <>
      <canvas ref={waterRef} className="ph-ink-water" aria-hidden="true" />
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
      {selection && selection.rows.length > 0 && (
        <div
          className="ph-ink-selbar"
          role="toolbar"
          aria-label="选中的笔迹"
          style={{ left: selection.ax, top: selection.ay + 10 }}
          // Buttons are HTML, not canvas: clicks here must not start a lasso.
          onPointerDown={(e) => e.stopPropagation()}
        >
          <span className="ph-ink-sel-n">{selection.rows.length}</span>
          {selColors.map((c) => (
            <button
              key={c.key}
              className={`ph-ink-sel-color${selection.rows.every((r) => r.color === c.key) ? " is-on" : ""}`}
              style={{ background: `var(--c-ink-${c.key}, var(--c-tx))` }}
              title={c.label}
              aria-label={`改为${c.label}`}
              onClick={() => {
                const rows = selection.rows;
                setSelection(null);
                commitReplace(
                  rows,
                  rows.map((r) => ({ points: r.points, color: c.key, width: r.width })),
                );
              }}
            />
          ))}
          <button
            className="ph-ink-sel-btn is-danger"
            title="删除选中的笔迹"
            aria-label="删除选中的笔迹"
            onClick={() => {
              const rows = selection.rows;
              setSelection(null);
              const gone = new Set(rows.map((r) => r.id));
              updateCache((prev) => prev.filter((s) => !gone.has(s.id)));
              pushInkOps(key, [{ kind: "remove", strokes: rows }]);
              for (const row of rows) {
                if (row.id.startsWith("temp-")) continue;
                void api.ink.remove(row.id).catch(() => {
                  void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
                });
              }
            }}
          >
            <Icons.trash size={13} />
          </button>
          <button
            className="ph-ink-sel-btn"
            title="取消选择"
            aria-label="取消选择"
            onClick={() => setSelection(null)}
          >
            <Icons.close size={11} />
          </button>
        </div>
      )}
    </>
  );
}

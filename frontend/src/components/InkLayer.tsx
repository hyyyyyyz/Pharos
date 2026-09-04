/**
 * InkLayer — handwritten strokes on one PDF page, captured from a stylus.
 *
 * Two canvases, the note-app architecture:
 *
 * - **dry** (z-index 3): every committed stroke, repainted from the query
 *   cache whenever strokes or zoom change.
 * - **wet** (z-index 4, above dry): the gesture in progress. Redrawn from
 *   scratch on every input batch — cheap, because it only ever holds one
 *   stroke — so the outline can breathe with pressure without tearing the
 *   already-drawn tail. Predicted samples (Chromium's `getPredictedEvents`)
 *   extend the wet outline past the physical pen tip to hide digitiser
 *   latency; they are discarded on the next real sample.
 *
 * Mounted inside `.ph-pc-page` like `HighlightLayer`. Above the text layer
 * (ink sits ON the paper, and with a tool active the canvas — not the text
 * spans — receives the pointer, so writing never starts a selection) and
 * below the highlight toolbar popup (z-index 5 for the wet layer, popups sit
 * at their own level inside the highlight layer's root).
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
 * - **The eraser removes whole strokes** (OneNote's default). A stylus
 *   barrel/eraser button erases in any tool.
 * - A finished stroke is written to the backend the moment the pen lifts; the
 *   gesture lands in the document-level undo stack (`store.inkPast`).
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
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { InkPoint, InkStrokeRow } from "../api/types";
import { paintStroke, pointToPdf, strokeNear, strokeOutline, paintOutline } from "../lib/ink";
import { useUI } from "../store";
import "./InkLayer.css";

/** Eraser reach, in CSS pixels beyond the stroke's own half-width. */
const ERASER_REACH_PX = 10;

/** Server-side ceiling for one stroke's sample count (see services/ink.py). */
const MAX_POINTS = 2000;
/** Start thinning below the ceiling, leaving the server a little headroom. */
const THIN_THRESHOLD = 1900;

/** Gesture state — refs, because it changes at digitiser rate. */
interface Session {
  pointerId: number;
  points: InkPoint[];
  erasing: boolean;
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
  const wetRef = useRef<HTMLCanvasElement>(null);

  const inkMode = useUI((s) => s.inkMode);
  const inkColor = useUI((s) => s.inkColor);
  const inkWidth = useUI((s) => s.inkWidth);
  const inkFingerDraw = useUI((s) => s.inkFingerDraw);
  const pushInkOps = useUI((s) => s.pushInkOps);

  // One fetch per document+rendition; every page instance shares the cache.
  const { data: all } = useQuery({
    queryKey: ["ink", paperId, kind],
    queryFn: ({ signal }) => api.ink.list(paperId, kind, signal),
  });

  const mine = useMemo(
    () => (all ?? []).filter((s) => s.page === page && s.points.length > 0),
    [all, page],
  );

  const key = `${paperId} ${kind}`;

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

  const repaintDry = useCallback(() => {
    const canvas = dryRef.current;
    if (!canvas) return;
    const sized = prepare(canvas);
    if (!sized) return;
    clearInSpace(canvas, sized.w);
    const colorOf = colorResolver(canvas);
    for (const stroke of mine) paintStroke(canvas.getContext("2d")!, stroke, colorOf);
  }, [mine, prepare, clearInSpace, colorResolver]);

  useEffect(() => {
    repaintDry();
  }, [repaintDry]);

  /* ------------------------------------------------------------- gestures */

  const strokeRef = useRef<Session | null>(null);
  /** Pointer id of the active pen, while down — the palm-rejection gate. */
  const penRef = useRef<number | null>(null);
  /** Live touch pointers, for the two-finger pan. */
  const touchesRef = useRef(new Map<number, { x: number; y: number }>());
  const panMidRef = useRef<{ x: number; y: number } | null>(null);
  /** Strokes deleted by the in-flight eraser gesture, for one undo op. */
  const erasedRef = useRef<InkStrokeRow[]>([]);

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

  /** Erase every stored stroke the point touches, optimistically. */
  const eraseAt = useCallback(
    (x: number, y: number) => {
      const done = new Set(erasedRef.current.map((s) => s.id));
      for (const stroke of mine) {
        if (done.has(stroke.id)) continue;
        if (!strokeNear(stroke.points, stroke.width, x, y, ERASER_REACH_PX / scale)) continue;
        erasedRef.current.push(stroke);
        updateCache((prev) => prev.filter((s) => s.id !== stroke.id));
        void api.ink.remove(stroke.id).catch(() => {
          void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
        });
      }
    },
    [mine, scale, updateCache, qc, paperId, kind],
  );

  /** Repaint the wet layer: the live outline, plus (for pens) the predicted
   *  tail that hides digitiser latency. Predicted samples are render-only and
   *  never enter `session.points`. */
  const paintWet = useCallback(
    (session: Session, predicted: InkPoint[]) => {
      const wet = wetRef.current;
      if (!wet) return;
      const sized = prepare(wet);
      if (!sized) return;
      clearInSpace(wet, sized.w);
      if (session.erasing) return;
      const ctx = wet.getContext("2d")!;
      const colorOf = colorResolver(wet);
      const live = [...session.points, ...predicted];
      const outline = strokeOutline(live, inkWidth, true);
      if (outline.length >= 3) {
        paintOutline(
          ctx,
          outline,
          colorOf(inkColor),
        );
      }
    },
    [prepare, clearInSpace, colorResolver, inkWidth, inkColor],
  );

  /* ------------------------------------------------- native event handlers */

  useEffect(() => {
    if (inkMode === "off") return;
    const wet = wetRef.current;
    if (!wet) return;
    const debug = new URLSearchParams(window.location.search).has("inkdebug");

    const inPageSpace = (clientX: number, clientY: number, pressure: number): InkPoint => {
      const origin = wet.getBoundingClientRect();
      return pointToPdf(clientX, clientY, origin, scale, pageHeight, pressure);
    };

    const startSession = (e: PointerEvent): void => {
      if (strokeRef.current !== null) return; // one stroke at a time
      // Barrel-button / eraser-end erases in any tool.
      const eraserButton = e.pointerType === "pen" && (e.buttons & 32) !== 0;
      const erasing = inkMode === "erase" || eraserButton;
      const pressure = e.pressure > 0 ? e.pressure : 0.5;
      strokeRef.current = {
        pointerId: e.pointerId,
        points: [inPageSpace(e.clientX, e.clientY, pressure)],
        erasing,
      };
      erasedRef.current = [];
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
        // Second finger: abandon the uncommitted finger stroke (wipe its wet
        // ghost) and become a pan.
        strokeRef.current = null;
        paintWet({ pointerId: -1, points: [], erasing: true }, []);
        const pts = [...touchesRef.current.values()];
        panMidRef.current = {
          x: (pts[0]!.x + pts[1]!.x) / 2,
          y: (pts[0]!.y + pts[1]!.y) / 2,
        };
      } else if (touchesRef.current.size > 2) {
        return;
      }
      if (touchesRef.current.size === 1 && inkFingerDraw && strokeRef.current === null) {
        // Opted-in single finger writes, like a pen would.
        startSession(e);
      }
    };

    const onDown = (e: PointerEvent): void => {
      e.preventDefault();
      if (e.pointerType === "touch") {
        onTouchDown(e);
        return;
      }
      if (e.pointerType === "pen") penRef.current = e.pointerId;
      // Pen tip is button 0; a pen already drawing must not restart.
      if (e.button !== 0 && !(e.buttons & 32)) return;
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
        }
        return;
      }

      const session = strokeRef.current;
      if (session === null || session.pointerId !== e.pointerId) return;

      if (session.erasing) {
        const origin = wet.getBoundingClientRect();
        eraseAt(
          ((e.clientX - origin.left) / scale),
          (pageHeight - (e.clientY - origin.top) / scale),
        );
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
        // A cancelled stroke was never committed — repaint dry as the truth.
        paintWet({ pointerId: -1, points: [], erasing: true }, []);
        repaintDry();
        return;
      }

      if (session.erasing) {
        if (erasedRef.current.length > 0) {
          pushInkOps(key, [{ kind: "remove", strokes: erasedRef.current }]);
          erasedRef.current = [];
        }
        return;
      }

      let points = session.points;
      if (points.length > MAX_POINTS) {
        const stride = Math.ceil(points.length / THIN_THRESHOLD);
        points = points.filter((_, i) => i % stride === 0 || i === points.length - 1);
      }
      // Clear the wet layer NOW: the dry repaint lands when the POST returns,
      // and a live outline lingering over the committed stroke would double
      // the tail (live outlines have no end taper).
      paintWet({ pointerId: -1, points: [], erasing: true }, []);
      void api.ink
        .create(paperId, { kind, page, points, color: inkColor, width: inkWidth })
        .then((row) => {
          updateCache((prev) => [...prev, row]);
          pushInkOps(key, [{ kind: "add", stroke: row }]);
        })
        .catch(() => {
          // Server truth wins; the just-drawn stroke comes off the canvas.
          void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
        });
    };

    const onUp = (e: PointerEvent): void => finish(e, false);
    const onCancel = (e: PointerEvent): void => finish(e, true);
    // Pen drags emit compatibility mouse events that would bubble to the
    // viewport's pan handler mid-stroke; the wet canvas is the target, so
    // this is where they stop.
    const stopMouse = (e: MouseEvent): void => e.stopPropagation();

    wet.addEventListener("pointerdown", onDown);
    wet.addEventListener("pointermove", onMove);
    wet.addEventListener("pointerup", onUp);
    wet.addEventListener("pointercancel", onCancel);
    wet.addEventListener("mousedown", stopMouse);
    return () => {
      wet.removeEventListener("pointerdown", onDown);
      wet.removeEventListener("pointermove", onMove);
      wet.removeEventListener("pointerup", onUp);
      wet.removeEventListener("pointercancel", onCancel);
      wet.removeEventListener("mousedown", stopMouse);
    };
  }, [
    inkMode,
    inkColor,
    inkWidth,
    inkFingerDraw,
    scale,
    pageHeight,
    mine,
    eraseAt,
    paintWet,
    repaintDry,
    viewportPan,
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
      <canvas ref={dryRef} className="ph-ink" aria-hidden="true" />
      <canvas
        ref={wetRef}
        className={
          "ph-ink ph-ink--wet" +
          (interactive ? " ph-ink--live" : "") +
          (inkMode === "erase" ? " ph-ink--erase" : "")
        }
        aria-hidden="true"
      />
    </>
  );
}

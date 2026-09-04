/**
 * InkLayer — handwritten strokes on one PDF page, captured from a stylus.
 *
 * Mounted inside `.ph-pc-page` like `HighlightLayer`, at z-index 3: above the
 * text layer (ink sits ON the paper, and with a tool active the canvas — not
 * the text spans — receives the pointer, so writing never starts a selection)
 * and below the highlight toolbar (z-index 4), which stays clickable.
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
 *   never committed, so nothing is lost. Pinch-zoom stays out of scope; the
 *   zoom controls remain the zoom.
 * - **The eraser removes whole strokes** (OneNote's default): a partial erase
 *   would have to split a stored row, and deleting half a written word reads
 *   as a bug. A stylus barrel/eraser button erases in any tool.
 * - A finished stroke is written to the backend the moment the pen lifts; the
 *   gesture lands in the document-level undo stack (`store.inkPast`).
 *
 * Coordinates are PDF user space (points, scale 1, bottom-left origin) — the
 * same contract as highlights, so strokes follow their page through zooms and
 * devices. The canvas transform applies scale and the y-flip; `lib/ink` owns
 * the geometry and the rendering in that space.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { InkPoint, InkStrokeRow } from "../api/types";
import { paintStroke, pointToPdf, strokeNear } from "../lib/ink";
import { useUI } from "../store";
import "./InkLayer.css";

/** Eraser reach, in CSS pixels beyond the stroke's own half-width. */
const ERASER_REACH_PX = 10;

/** Server-side ceiling for one stroke's sample count (see services/ink.py). */
const MAX_POINTS = 2000;
/** Start thinning below the ceiling, leaving the server a little headroom. */
const THIN_THRESHOLD = 1900;

/** A pointer actively writing this stroke. */
interface StrokeSession {
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
  const canvasRef = useRef<HTMLCanvasElement>(null);

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

  /**
   * Repaint every stored stroke. `transform` applies scale and the PDF y-flip,
   * so drawing and erasing both work in PDF points and line widths scale with
   * zoom exactly the way ink on paper does.
   */
  const repaint = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    if (w === 0 || h === 0) return;
    if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
    }
    const k = dpr * scale;
    ctx.setTransform(k, 0, 0, -k, 0, pageHeight * k);
    // User-space clear of the page box (the transform flips y, so 0..pageHeight
    // covers the whole canvas).
    ctx.clearRect(0, 0, w / scale, pageHeight);
    // One computed-style read per repaint, not per stroke: 300 strokes would
    // otherwise cost 300 forced style resolutions.
    const style = getComputedStyle(canvas);
    const colorOf = (token: string): string =>
      style.getPropertyValue(`--c-ink-${token}`).trim() ||
      style.getPropertyValue("--c-tx").trim() ||
      "#1e222a";
    for (const stroke of mine) paintStroke(ctx, stroke, colorOf);
  }, [mine, scale, pageHeight]);

  useEffect(() => {
    repaint();
  }, [repaint]);

  /* ------------------------------------------------------------- gestures */

  // Gesture state lives in refs: it changes at pointer-event frequency and
  // nothing about the React render depends on it mid-gesture.
  const strokeRef = useRef<StrokeSession | null>(null);
  /** Pointer id of the active pen, while down — the palm-rejection gate. */
  const penRef = useRef<number | null>(null);
  /** Live touch pointers, for the two-finger pan. */
  const touchesRef = useRef(new Map<number, { x: number; y: number }>());
  const panMidRef = useRef<{ x: number; y: number } | null>(null);
  /** Strokes deleted by the in-flight eraser gesture, for one undo op. */
  const erasedRef = useRef<InkStrokeRow[]>([]);

  /** Functional cache write: safe against mid-gesture staleness, because the
   *  updater always sees the latest committed rows. */
  const updateCache = useCallback(
    (updater: (prev: InkStrokeRow[]) => InkStrokeRow[]) => {
      qc.setQueryData<InkStrokeRow[]>(["ink", paperId, kind], (prev) =>
        updater(prev ?? []),
      );
    },
    [qc, paperId, kind],
  );

  const viewportPan = useCallback((dx: number, dy: number) => {
    const vp = canvasRef.current?.closest(".ph-pc") as HTMLElement | null;
    if (!vp) return;
    vp.scrollLeft += dx;
    vp.scrollTop += dy;
  }, []);

  /** Delete every stored stroke the current point touches, optimistically. */
  const eraseAt = useCallback(
    (clientX: number, clientY: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const origin = canvas.getBoundingClientRect();
      const px = (clientX - origin.left) / scale;
      const py = pageHeight - (clientY - origin.top) / scale;
      const reach = ERASER_REACH_PX / scale;
      const done = new Set(erasedRef.current.map((s) => s.id));
      for (const stroke of mine) {
        if (done.has(stroke.id)) continue;
        if (!strokeNear(stroke.points, stroke.width, px, py, reach)) continue;
        erasedRef.current.push(stroke);
        updateCache((prev) => prev.filter((s) => s.id !== stroke.id));
        void api.ink.remove(stroke.id).catch(() => {
          // The erase failed server-side; the refetch restores the truth.
          void qc.invalidateQueries({ queryKey: ["ink", paperId, kind] });
        });
      }
    },
    [mine, scale, pageHeight, updateCache, qc, paperId, kind],
  );

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const native = e.nativeEvent;

    if (native.pointerType === "touch") {
      // A pen outranks everything: while it is down, a touch pointer is a palm.
      if (penRef.current !== null) return;
      touchesRef.current.set(native.pointerId, { x: native.clientX, y: native.clientY });
      if (touchesRef.current.size === 2) {
        // Second finger lands: abandon any in-progress finger stroke (never
        // committed, so nothing is lost) and become a pan. The stroke's live
        // segments are already on the canvas — repaint to wipe the ghost.
        strokeRef.current = null;
        repaint();
        const pts = [...touchesRef.current.values()];
        panMidRef.current = {
          x: (pts[0]!.x + pts[1]!.x) / 2,
          y: (pts[0]!.y + pts[1]!.y) / 2,
        };
      } else if (touchesRef.current.size > 2) {
        return; // a third finger joins the pan; it starts nothing
      }
      if (touchesRef.current.size !== 1 || !inkFingerDraw || strokeRef.current !== null) {
        e.preventDefault();
        return;
      }
      // Fall through: an opted-in single finger writes, like a pen would.
    } else if (native.pointerType === "pen") {
      penRef.current = native.pointerId;
    }

    if (strokeRef.current !== null) return; // one stroke at a time

    // Barrel-button / eraser-end erases in any tool.
    const eraserButton = native.pointerType === "pen" && (native.buttons & 32) !== 0;
    const erasing = inkMode === "erase" || eraserButton;

    const origin = canvas.getBoundingClientRect();
    const pressure = native.pressure > 0 ? native.pressure : 0.5;
    const first = pointToPdf(native.clientX, native.clientY, origin, scale, pageHeight, pressure);
    strokeRef.current = { pointerId: native.pointerId, points: [first], erasing };
    erasedRef.current = [];
    canvas.setPointerCapture(native.pointerId);
    e.preventDefault();
    e.stopPropagation();
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    const native = e.nativeEvent;
    const canvas = canvasRef.current;
    if (!canvas) return;

    if (native.pointerType === "touch") {
      const touch = touchesRef.current.get(native.pointerId);
      if (touch) {
        touch.x = native.clientX;
        touch.y = native.clientY;
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
      // Not panning: an opted-in finger stroke falls through to the draw path.
    }

    const session = strokeRef.current;
    if (session === null || session.pointerId !== native.pointerId) return;

    const origin = canvas.getBoundingClientRect();
    if (session.erasing) {
      eraseAt(native.clientX, native.clientY);
      e.preventDefault();
      return;
    }

    // Every sample the digitiser produced since the last event — coalesced
    // delivery is what keeps a fast stroke smooth on a high-rate digitiser.
    const events = native.getCoalescedEvents?.() ?? [];
    for (const ev of events.length > 0 ? events : [native]) {
      const pressure = ev.pressure > 0 ? ev.pressure : 0.5;
      session.points.push(
        pointToPdf(ev.clientX, ev.clientY, origin, scale, pageHeight, pressure),
      );
    }

    // Live rendering: straight segments with per-sample width — fast, and
    // good enough while the pen moves. The smoothed curve is painted on lift.
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const k = dpr * scale;
    ctx.setTransform(k, 0, 0, -k, 0, pageHeight * k);
    const style = getComputedStyle(canvas);
    ctx.strokeStyle =
      style.getPropertyValue(`--c-ink-${inkColor}`).trim() ||
      style.getPropertyValue("--c-tx").trim() ||
      "#1e222a";
    const pts = session.points;
    const prev = pts[pts.length - 2];
    const cur = pts[pts.length - 1];
    if (prev && cur) {
      ctx.lineWidth = inkWidth * (0.5 + cur.p);
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(prev.x, prev.y);
      ctx.lineTo(cur.x, cur.y);
      ctx.stroke();
    }
    e.preventDefault();
  };

  const onPointerUp = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    const native = e.nativeEvent;
    if (native.pointerType === "pen" && penRef.current === native.pointerId) {
      penRef.current = null;
    }
    if (native.pointerType === "touch") {
      touchesRef.current.delete(native.pointerId);
      if (touchesRef.current.size < 2) panMidRef.current = null;
    }
    commitStroke(native.pointerId);
  };

  const onPointerCancel = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    const native = e.nativeEvent;
    if (native.pointerType === "pen" && penRef.current === native.pointerId) penRef.current = null;
    if (native.pointerType === "touch") {
      touchesRef.current.delete(native.pointerId);
      if (touchesRef.current.size < 2) panMidRef.current = null;
    }
    // A cancelled stroke was never committed — drop it and repaint the truth.
    strokeRef.current = null;
    repaint();
  };

  /** Finish the stroke owned by `pointerId`: erase gestures record their undo
   *  op, drawn strokes are written to the backend and painted smoothed. */
  const commitStroke = useCallback(
    (pointerId: number) => {
      const canvas = canvasRef.current;
      const session = strokeRef.current;
      if (session === null || session.pointerId !== pointerId) return;
      strokeRef.current = null;
      if (canvas?.hasPointerCapture(pointerId)) canvas.releasePointerCapture(pointerId);

      if (session.erasing) {
        if (erasedRef.current.length > 0) {
          pushInkOps(key, [{ kind: "remove", strokes: erasedRef.current }]);
          erasedRef.current = [];
        }
        return;
      }

      let points = session.points;
      if (points.length > MAX_POINTS) {
        // Downsample rather than fail: a five-minute squiggle must still land.
        const stride = Math.ceil(points.length / THIN_THRESHOLD);
        points = points.filter((_, i) => i % stride === 0 || i === points.length - 1);
      }
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
    },
    [inkColor, inkWidth, key, kind, page, paperId, pushInkOps, updateCache, qc],
  );

  /* ----------------------------------------------------------------- view */

  const interactive = inkMode !== "off";
  return (
    <canvas
      ref={canvasRef}
      className={
        "ph-ink" +
        (interactive ? " ph-ink--live" : "") +
        (inkMode === "erase" ? " ph-ink--erase" : "")
      }
      aria-hidden="true"
      onPointerDown={interactive ? onPointerDown : undefined}
      onPointerMove={interactive ? onPointerMove : undefined}
      onPointerUp={interactive ? onPointerUp : undefined}
      onPointerCancel={interactive ? onPointerCancel : undefined}
      // Pen drags emit compatibility mouse events that would bubble to the
      // viewport's pan handler mid-stroke; the canvas is the target, so this
      // is where they stop.
      onMouseDown={interactive ? (e) => e.stopPropagation() : undefined}
    />
  );
}

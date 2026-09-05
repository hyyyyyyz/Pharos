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
  paintStroke,
  pointToPdf,
  splitStroke,
  strokeBounds,
  strokeCaughtBy,
  strokeNear,
  strokeOutline,
  paintOutline,
  translatePoints,
} from "../lib/ink";
import { Icons } from "../design/icons";
import { useUI } from "../store";
import "./InkLayer.css";

/** Server-side ceiling for one stroke's sample count (see services/ink.py). */
const MAX_POINTS = 2000;
/** Start thinning below the ceiling, leaving the server a little headroom. */
const THIN_THRESHOLD = 1900;

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
}

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
  const wetRef = useRef<HTMLCanvasElement>(null);

  const inkMode = useUI((s) => s.inkMode);
  const inkColor = useUI((s) => s.inkColor);
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
  const repaintDry = useCallback(() => {
    const canvas = dryRef.current;
    if (!canvas) return;
    const sized = prepare(canvas);
    if (!sized) return;
    clearInSpace(canvas, sized.w);
    const colorOf = colorResolver(canvas);
    const carried = movingRef.current?.ids;
    for (const stroke of mineRef.current) {
      if (carried?.has(stroke.id)) continue; // being dragged; the wet layer shows it
      paintStroke(canvas.getContext("2d")!, stroke, colorOf);
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

  /* ------------------------------------------------------------- gestures */

  const strokeRef = useRef<Session | null>(null);
  /** Pointer id of the active pen, while down — the palm-rejection gate. */
  const penRef = useRef<number | null>(null);
  /** Live touch pointers, for the two-finger pan. */
  const touchesRef = useRef(new Map<number, { x: number; y: number }>());
  const panMidRef = useRef<{ x: number; y: number } | null>(null);
  /** Strokes deleted by the in-flight 整笔 eraser gesture, for one undo op. */
  const erasedRef = useRef<InkStrokeRow[]>([]);
  /** 局部 eraser account: rows removed / parts created during one gesture. */
  const pixelRemovedRef = useRef<InkStrokeRow[]>([]);
  const pixelAddedRef = useRef<InkStrokeRow[]>([]);

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
      if (session.erasing) return;
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
    [prepare, clearInSpace, colorResolver, inkWidth, inkColor, scale, paintSelection],
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
    paintSelection(wet.getContext("2d")!, selection.rows);
  }, [selection, prepare, clearInSpace, paintSelection]);

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

  /* ------------------------------------------------- native event handlers */

  useEffect(() => {
    if (inkMode === "off") return;
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

    const startSession = (e: PointerEvent): void => {
      if (strokeRef.current !== null) return; // one gesture at a time
      // Barrel-button / eraser-end erases in any tool.
      const eraserButton = e.pointerType === "pen" && (e.buttons & 32) !== 0;
      const pressure = e.pressure > 0 ? e.pressure : 0.5;
      const pt = inPageSpace(e.clientX, e.clientY, pressure);

      if (inkMode === "select") {
        // Down on the selection carries it; down on fresh paper starts a
        // new lasso (and forgets the old selection — a tap outside clears).
        if (selRef.current && overSelection(pt.x, pt.y)) {
          const ids = new Set(selRef.current.rows.map((r) => r.id));
          strokeRef.current = {
            pointerId: e.pointerId,
            points: [],
            erasing: false,
            lasso: false,
            moving: { ids, dx: 0, dy: 0, startX: pt.x, startY: pt.y },
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
        paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null }, []);
        const pts = [...touchesRef.current.values()];
        panMidRef.current = {
          x: (pts[0]!.x + pts[1]!.x) / 2,
          y: (pts[0]!.y + pts[1]!.y) / 2,
        };
      } else if (touchesRef.current.size > 2) {
        return;
      }
      if (touchesRef.current.size === 1 && strokeRef.current === null) {
        // Erasing and lassoing are deliberate edit gestures: a finger does
        // them whatever the finger-draw preference says — palm rejection
        // exists to stop accidental INK, and there is no ink to lay down in
        // these tools.
        if (inkMode === "erase" || inkMode === "select" || inkFingerDraw) {
          startSession(e);
        }
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
          return;
        }
        // Not panning: fall through — an opted-in finger gesture continues
        // through the shared session path below.
      }

      const session = strokeRef.current;
      if (session === null || session.pointerId !== e.pointerId) {
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
        movingRef.current = null;
        paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null }, []);
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
        paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null }, []);
        // Repaint the ants for the fresh selection.
        const sized = prepare(wet);
        if (sized) paintSelection(wet.getContext("2d")!, caught);
        return;
      }

      if (session.moving) {
        commitMove(session);
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
      paintWet({ pointerId: -1, points: [], erasing: true, lasso: false, moving: null }, []);
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

    const onUp = (e: PointerEvent): void => finish(e, false);
    const onCancel = (e: PointerEvent): void => finish(e, true);
    // Pen drags emit compatibility mouse events that would bubble to the
    // viewport's pan handler mid-stroke; the wet canvas is the target, so
    // this is where they stop.
    const stopMouse = (e: MouseEvent): void => e.stopPropagation();
    // Left the page: the hover preview has no position to sit at.
    const onLeave = (): void => clearWet();

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
        movingRef.current = null;
      }
      // Rebinding (tool change, zoom, data) invalidates any preview painted
      // under the previous settings.
      clearWet();
    };
  }, [
    inkMode,
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
    commitMove,
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
          {INK_COLORS.map((c) => (
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

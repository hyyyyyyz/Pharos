/**
 * TapeLayer — 胶带: a movable strip that covers ink or text until tapped.
 *
 * Mounted inside `.ph-pc-page` alongside `InkLayer`/`HighlightLayer`, above
 * both (z-index 5) — the point of tape is that it can cover a line of
 * handwriting as readily as a line of printed text.
 *
 * Two things can happen to an existing strip, and they are gated on
 * different conditions:
 *
 * - **Tap to reveal/cover** works whenever nothing ELSE owns the pointer —
 *   `inkMode` is "off" (plain reading) or "tape" (the tool itself); with any
 *   other tool active, `InkLayer`'s own wet canvas is what should get the
 *   touch, so each strip goes `pointer-events: none` there (`is-dormant`).
 *   That is also why `InkLayer` treats `inkMode === "tape"` the same as
 *   "off" — nobody should be drawing ink while the tape tool is selected.
 *   Only the STRIPS get this treatment, not the wrap: the wrap covers the
 *   whole page (`inset: 0`), so making it `pointer-events: auto` in "off"
 *   mode too — not just its strips — would swallow every mousedown on the
 *   page, breaking plain text selection even where no tape has ever been
 *   placed. A `pointer-events: none` parent does not stop an `auto` child.
 * - **Resize/straighten/auto-thickness/delete** — the popover — only when
 *   the tape tool is the active one AND a strip is selected. Reading state
 *   ("off") gets the tap-to-reveal affordance and nothing more; there is no
 *   toolbar to show it in.
 * - **Placing a NEW strip** (drag on blank page area) needs the WRAP itself
 *   to own the pointer, which is why it is `pointer-events: auto` in "tape"
 *   mode specifically (`wrapLive` below) — the one condition where covering
 *   the whole page with a hit target is exactly what is wanted.
 *
 * Coordinates: PDF user space, points at scale 1, bottom-left origin, same
 * contract as ink and highlights. Unlike a stroke, a tape mark is a rotated
 * rectangle (centre, length, thickness, angle) rather than a sampled path —
 * see `lib/tape` — so it renders as one positioned+rotated `<div>`, not a
 * canvas paint.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { TapeRow } from "../api/types";
import { clampTapeSize, tapeAlongAxis, type TapeAxis, type TapeRect } from "../lib/tape";
import { isDrawingPointer } from "../lib/pointer";
import { Icons } from "../design/icons";
import { useUI } from "../store";
import "./TapeLayer.css";

/** Default thickness for a strip the auto-thickness toggle does not size —
 *  and the fallback when no text line can be measured under the drag. */
const DEFAULT_THICKNESS = 14;

/** One strip's live drag. Just the rectangle: 随手 (freehand) is gone — see
 *  the component's own note — so a strip in flight is only ever a box. */
interface TapeDraft {
  rect: TapeRect;
}

/** The nearest text line's own rendered height under a page point, in CSS
 *  pixels — `null` if nothing is there to measure. Reads the SAME
 *  `.ph-pc-tl` spans HighlightLayer's own hit-testing already relies on. */
function textLineHeightAt(pageEl: Element, clientX: number, clientY: number): number | null {
  const spans = pageEl.querySelectorAll(".ph-pc-tl span");
  for (const span of Array.from(spans)) {
    const r = span.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) {
      return r.height;
    }
  }
  return null;
}

export function TapeLayer({
  paperId,
  kind,
  page,
  scale,
  pageHeight,
}: {
  paperId: string;
  kind: "original" | "mono" | "dual";
  page: number;
  scale: number;
  pageHeight: number;
}): JSX.Element {
  const qc = useQueryClient();
  const wrapRef = useRef<HTMLDivElement>(null);

  const inkMode = useUI((s) => s.inkMode);
  const tapeAutoThickness = useUI((s) => s.tapeAutoThickness);
  const inkFingerDraw = useUI((s) => s.inkFingerDraw);
  const setInkTray = useUI((s) => s.setInkTray);
  /** Strips the lasso is carrying: painted on the ink layer's wet canvas
   *  right now, so this layer must not also draw them where they were. */
  const carried = useUI((s) => s.inkCarried);
  const pushInkOps = useUI((s) => s.pushInkOps);
  /** 胶带 joins the reader's ONE undo stack, keyed the same way the ink
   *  ops are — see `InkOp`'s tape variants. */
  const opsKey = `${paperId} ${kind}`;

  // Same staleTime: Infinity reasoning as InkLayer's own query — every write
  // patches the cache directly, so a background refetch only risks a race
  // (a stale GET landing after a local edit and reviving it), never buys
  // freshness back.
  const { data: all } = useQuery({
    queryKey: ["tape", paperId, kind],
    queryFn: ({ signal }) => api.tape.list(paperId, kind, signal),
    staleTime: Infinity,
  });
  const mine = useMemo(
    () => (all ?? []).filter((t) => t.page === page && !carried.includes(t.id)),
    [all, page, carried],
  );

  const updateCache = useCallback(
    (updater: (prev: TapeRow[]) => TapeRow[]) => {
      qc.setQueryData<TapeRow[]>(["tape", paperId, kind], (prev) => updater(prev ?? []));
    },
    [qc, paperId, kind],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  useEffect(() => {
    if (selectedId && !mine.some((t) => t.id === selectedId)) setSelectedId(null);
  }, [mine, selectedId]);
  // The tool that was on when a strip got selected is the only one it stays
  // selected for — switching away (or off) must not leave a stale popover.
  useEffect(() => {
    if (inkMode !== "tape") setSelectedId(null);
  }, [inkMode]);

  const [dragPreview, setDragPreview] = useState<TapeDraft | null>(null);

  /** Existing strips stay tappable (reveal/cover) in "off" (plain reading)
   *  and "tape" (the tool itself); every other tool's wet canvas needs the
   *  touch more. This is deliberately NOT the wrap's own interactivity (see
   *  `wrapLive` below): the wrap covers the WHOLE page with `inset: 0`, so
   *  making IT `pointer-events: auto` in "off" mode — not just the strips —
   *  would swallow every mousedown on the page and break plain text
   *  selection (the "解释" flow) even where no tape has ever been placed. A
   *  CSS `pointer-events: none` on the wrap does not stop an individual
   *  child from being `auto` — that per-strip opt-in is exactly the point. */
  const stripsInteractive = inkMode === "off" || inkMode === "tape";
  /** The wrap itself — the create-new-strip drag surface — only needs to
   *  own the pointer while the tape tool is actually selected. */
  const wrapLive = inkMode === "tape";

  const toPage = useCallback(
    (clientX: number, clientY: number): { x: number; y: number } => {
      const el = wrapRef.current;
      const origin = el!.getBoundingClientRect();
      return {
        x: (clientX - origin.left) / scale,
        y: pageHeight - (clientY - origin.top) / scale,
      };
    },
    [scale, pageHeight],
  );

  const toggleRevealed = useCallback(
    (t: TapeRow) => {
      const next = !t.revealed;
      updateCache((prev) => prev.map((s) => (s.id === t.id ? { ...s, revealed: next } : s)));
      void api.tape.update(t.id, { revealed: next }).catch(() => {
        void qc.invalidateQueries({ queryKey: ["tape", paperId, kind] });
      });
    },
    [updateCache, qc, paperId, kind],
  );

  const patchTape = useCallback(
    (t: TapeRow, patch: Partial<Pick<TapeRow, "w" | "h" | "angle">>) => {
      // Only the fields this patch actually touches go into the op, so an
      // undo of "made it wider" does not also revert an unrelated straighten.
      const before: Partial<Pick<TapeRow, "w" | "h" | "angle">> = {};
      for (const field of Object.keys(patch) as (keyof typeof patch)[]) {
        before[field] = t[field];
      }
      pushInkOps(opsKey, [{ kind: "tape-edit", id: t.id, before, after: { ...patch } }]);
      updateCache((prev) => prev.map((s) => (s.id === t.id ? { ...s, ...patch } : s)));
      void api.tape.update(t.id, patch).catch(() => {
        void qc.invalidateQueries({ queryKey: ["tape", paperId, kind] });
      });
    },
    [updateCache, qc, paperId, kind, pushInkOps, opsKey],
  );

  const deleteTape = useCallback(
    (t: TapeRow) => {
      pushInkOps(opsKey, [{ kind: "tape-remove", tape: t }]);
      updateCache((prev) => prev.filter((s) => s.id !== t.id));
      setSelectedId(null);
      void api.tape.remove(t.id).catch(() => {
        void qc.invalidateQueries({ queryKey: ["tape", paperId, kind] });
      });
    },
    [updateCache, qc, paperId, kind, pushInkOps, opsKey],
  );

  /* ------------------------------------------------------------- create */

  useEffect(() => {
    if (inkMode !== "tape") return;
    const el = wrapRef.current;
    if (!el) return;

    let start: { x: number; y: number; clientX: number; clientY: number } | null = null;
    let moved = false;
    /** The axis the strip is currently pinned to, carried across samples.
     *  This ref IS the lock — see `tapeAlongAxis`. */
    let axis: TapeAxis = null;
    /** The strip as of the last frame, so pen-up commits exactly what the
     *  reader was looking at. Recomputing it from the final point would throw
     *  the axis away and go back to "首尾相连". */
    let live: TapeRect | null = null;
    const rafRef = { current: 0 };

    /** The thickness a strip laid down right here should get: the text line's
     *  own height when 自动粗细 is on and there IS a line under the pen, the
     *  fixed default otherwise. Clamped into the server's own size range —
     *  a small line measured at high zoom really can come out under
     *  `MIN_TAPE_SIZE`, and the POST that follows is a 422. */
    const thicknessAt = (clientX: number, clientY: number): number => {
      if (!tapeAutoThickness) return DEFAULT_THICKNESS;
      const measured = textLineHeightAt(el.parentElement!, clientX, clientY);
      return clampTapeSize(measured ? measured / scale : DEFAULT_THICKNESS);
    };

    const onDown = (e: PointerEvent) => {
      if ((e.target as HTMLElement).closest(".ph-tape-strip, .ph-tape-pop") !== null) return;
      // 防手指: a strip is drawn with the pen, like every other mark on the
      // page. A finger here is a palm resting while writing, or a scroll that
      // the viewport should have got instead.
      if (!isDrawingPointer(e, inkFingerDraw)) return;
      setInkTray(null); // a tray open over the page has had its turn

      const pt = toPage(e.clientX, e.clientY);
      start = { ...pt, clientX: e.clientX, clientY: e.clientY };
      moved = false;
      axis = null;
      live = null;
      el.setPointerCapture(e.pointerId);
    };

    const onMove = (e: PointerEvent) => {
      if (!start) return;
      const dx = e.clientX - start.clientX;
      const dy = e.clientY - start.clientY;
      if (Math.hypot(dx, dy) > 4) moved = true;
      e.preventDefault();
      if (!moved) return;
      // One preview per FRAME, not one per sample: `setDragPreview` re-renders
      // this whole layer — every strip on the page — and a stylus delivers
      // samples several times faster than the display can show them.
      if (rafRef.current !== 0) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0;
        const s = start;
        if (!s) return;
        const pt = toPage(e.clientX, e.clientY);
        const thickness = thicknessAt(e.clientX, e.clientY);
        // 边写边改: the strip is rebuilt against the pen on every frame, and
        // the axis it snapped to is carried forward rather than re-derived —
        // that continuity is the whole of "一直保持其方向".
        const next = tapeAlongAxis(s.x, s.y, pt.x, pt.y, thickness, axis);
        axis = next.axis;
        live = next.rect;
        setDragPreview({ rect: next.rect });
      });
    };

    const onUp = async (e: PointerEvent) => {
      const s = start;
      if (!s) return;
      start = null;
      if (rafRef.current !== 0) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
      if (el.hasPointerCapture(e.pointerId)) el.releasePointerCapture(e.pointerId);
      setDragPreview(null);
      if (!moved) return;
      // Commit what was on screen. If the last frame never ran (a very short
      // drag), derive it once here through the same function, so there is no
      // second definition of what a strip is.
      const pt = toPage(e.clientX, e.clientY);
      const rect =
        live ?? tapeAlongAxis(s.x, s.y, pt.x, pt.y, thicknessAt(e.clientX, e.clientY), axis).rect;
      live = null;
      axis = null;
      try {
        const row = await api.tape.create(paperId, { kind, page, ...rect });
        updateCache((prev) => [...prev, row]);
        pushInkOps(opsKey, [{ kind: "tape-add", tape: row }]);
      } catch {
        /* refused (hostile geometry, or the paper is not this user's) —
           nothing was optimistically added, so nothing to roll back. */
      }
    };

    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      if (rafRef.current !== 0) cancelAnimationFrame(rafRef.current);
    };
  }, [
    inkMode,
    toPage,
    tapeAutoThickness,
    inkFingerDraw,
    setInkTray,
    pushInkOps,
    opsKey,
    scale,
    paperId,
    kind,
    page,
    updateCache,
  ]);

  const selected = mine.find((t) => t.id === selectedId) ?? null;

  return (
    <div
      ref={wrapRef}
      className={`ph-tape${wrapLive ? " ph-tape--live" : ""}`}
      aria-hidden="true"
    >
      {/* The surface a new strip is dragged out on, present only while the
          tool is. First child, so every existing strip stays tappable on top
          of it. See `.ph-tape-catch` for why the wrap cannot carry this
          itself any more. */}
      {wrapLive && <div className="ph-tape-catch" />}
      {/* Every strip is one rotated box. 随手 (freehand) has been removed at
          the reader's request — "删掉随手胶条吧，把直胶条优化好" — and with it
          the whole second rendering path. Rows stored by an older build may
          still carry a `points` path; it is ignored, so such a strip shows up
          as the straight run its own centre/length/thickness describe. */}
      {mine.map((t) => (
        <TapeStrip
          key={t.id}
          tape={t}
          scale={scale}
          pageHeight={pageHeight}
          selected={inkMode === "tape" && selectedId === t.id}
          dormant={!stripsInteractive}
          onTap={() => {
            if (!stripsInteractive) return;
            toggleRevealed(t);
            if (inkMode === "tape") setSelectedId(t.id);
          }}
        />
      ))}
      {dragPreview && (
        <TapeStrip
          tape={{ ...dragPreview.rect, id: "preview", revealed: false } as unknown as TapeRow}
          scale={scale}
          pageHeight={pageHeight}
          selected={false}
          preview
          onTap={() => undefined}
        />
      )}
      {selected && inkMode === "tape" && (
        <div
          className="ph-tape-pop"
          style={{
            left: selected.x * scale,
            top: (pageHeight - selected.y) * scale + (selected.h / 2) * scale + 10,
          }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <button
            className="ph-tape-pop-btn"
            title={selected.revealed ? "盖住" : "显示"}
            onClick={() => toggleRevealed(selected)}
          >
            {selected.revealed ? <Icons.eyeOff /> : <Icons.eye />}
          </button>
          <span className="ph-tape-pop-sep" />
          <button
            className="ph-tape-pop-btn"
            title="变短"
            onClick={() => patchTape(selected, { w: Math.max(4, selected.w - 8) })}
          >
            −长
          </button>
          <button
            className="ph-tape-pop-btn"
            title="变长"
            onClick={() => patchTape(selected, { w: Math.min(2000, selected.w + 8) })}
          >
            +长
          </button>
          <button
            className="ph-tape-pop-btn"
            title="变窄"
            onClick={() => patchTape(selected, { h: Math.max(4, selected.h - 2) })}
          >
            −宽
          </button>
          <button
            className="ph-tape-pop-btn"
            title="变宽"
            onClick={() => patchTape(selected, { h: Math.min(2000, selected.h + 2) })}
          >
            +宽
          </button>
          <span className="ph-tape-pop-sep" />
          <button
            className="ph-tape-pop-btn"
            title="拉直线条：角度归零"
            disabled={selected.angle === 0}
            onClick={() => patchTape(selected, { angle: 0 })}
          >
            拉直
          </button>
          <span className="ph-tape-pop-sep" />
          <button className="ph-tape-pop-btn is-danger" title="删除" onClick={() => deleteTape(selected)}>
            <Icons.trash size={13} />
          </button>
        </div>
      )}
    </div>
  );
}

function TapeStrip({
  tape,
  scale,
  pageHeight,
  selected,
  preview = false,
  dormant = false,
  onTap,
}: {
  tape: TapeRow;
  scale: number;
  pageHeight: number;
  selected: boolean;
  preview?: boolean;
  /** Some OTHER ink tool is active: this strip stops accepting the pointer
   *  (its own `pointer-events: none`) so a pen/eraser/etc. gesture that
   *  merely crosses over it is not swallowed. */
  dormant?: boolean;
  onTap: () => void;
}): JSX.Element {
  const cssX = tape.x * scale;
  const cssY = (pageHeight - tape.y) * scale;
  const w = tape.w * scale;
  const h = tape.h * scale;

  const downRef = useRef<{ x: number; y: number } | null>(null);

  return (
    <div
      className={`ph-tape-strip${tape.revealed ? " is-revealed" : ""}${selected ? " is-selected" : ""}${preview ? " is-preview" : ""}${dormant ? " is-dormant" : ""}`}
      style={{
        left: cssX,
        top: cssY,
        width: w,
        height: h,
        transform: `translate(-50%, -50%) rotate(${-tape.angle}deg)`,
      }}
      onPointerDown={(e) => {
        downRef.current = { x: e.clientX, y: e.clientY };
      }}
      onPointerUp={(e) => {
        const down = downRef.current;
        downRef.current = null;
        if (!down) return;
        if (Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6) return; // a drag, not a tap
        onTap();
      }}
    />
  );
}


import { useCallback, useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import "./PdfCanvas.css";

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 4;
/** Padding of the page column, both sides — used for the fit-width maths. */
const GUTTER = 40;

const clampZoom = (v: number): number => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, v));

const errMsg = (e: unknown): string =>
  e instanceof Error ? e.message : typeof e === "string" ? e : "未知错误";

interface PageSize {
  w: number;
  h: number;
}

export interface PdfCanvasProps {
  doc: PDFDocumentProxy | null;
  /** 1 = 100%. */
  zoom: number;
  /** Zoom via ctrl/⌘+wheel; the viewer reports the new value up. */
  onZoom: (next: number) => void;
  /** Reports the scale that fits the page width, whenever it changes. */
  onFitScale: (scale: number) => void;
  /** Set to a 1-based page to scroll there; the viewer clears it via onJumped. */
  jumpTo: number | null;
  onJumped: () => void;
  onCurrentPage: (page: number) => void;
}

/**
 * Renders every page of a pdf.js document to its own canvas at
 * `zoom × devicePixelRatio`, so the text stays crisp at any zoom (CSS-scaling
 * one bitmap — the prototype's trick — would blur a real PDF).
 */
export function PdfCanvas({
  doc,
  zoom,
  onZoom,
  onFitScale,
  jumpTo,
  onJumped,
  onCurrentPage,
}: PdfCanvasProps): JSX.Element {
  const viewportRef = useRef<HTMLDivElement>(null);
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([]);
  /** Live render task per page, so a re-render never collides with an in-flight one. */
  const tasksRef = useRef(new Map<number, { task: RenderTask; settled: Promise<void> }>());
  const [pages, setPages] = useState<PageSize[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [grabbing, setGrabbing] = useState(false);

  // Props that event listeners need without re-subscribing.
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const onZoomRef = useRef(onZoom);
  onZoomRef.current = onZoom;
  const onFitScaleRef = useRef(onFitScale);
  onFitScaleRef.current = onFitScale;
  const onCurrentPageRef = useRef(onCurrentPage);
  onCurrentPageRef.current = onCurrentPage;

  /* --------------------------------------------------- page sizes (scale 1) */
  useEffect(() => {
    setError(null);
    if (!doc) {
      setPages([]);
      return;
    }
    let cancelled = false;
    (async () => {
      const sizes: PageSize[] = [];
      for (let n = 1; n <= doc.numPages; n++) {
        const page = await doc.getPage(n);
        if (cancelled) return;
        const v = page.getViewport({ scale: 1 });
        sizes.push({ w: v.width, h: v.height });
      }
      if (!cancelled) setPages(sizes);
    })().catch((e: unknown) => {
      if (!cancelled) setError(errMsg(e));
    });
    return () => {
      cancelled = true;
    };
  }, [doc]);

  /* ------------------------------------------------------------ fit width */
  useEffect(() => {
    const el = viewportRef.current;
    const first = pages[0];
    if (!el || !first) return;
    const report = () => {
      const avail = el.clientWidth - GUTTER;
      if (avail > 0) onFitScaleRef.current(clampZoom(avail / first.w));
    };
    report();
    const ro = new ResizeObserver(report);
    ro.observe(el);
    return () => ro.disconnect();
  }, [pages]);

  /* ------------------------------------------------------------- rendering */
  useEffect(() => {
    if (!doc || pages.length === 0) return;
    let cancelled = false;
    const tasks = tasksRef.current;
    // Very large canvases blow up memory; trade a little sharpness at high zoom.
    const dpr = Math.min(window.devicePixelRatio || 1, Math.max(1, 2.5 / zoom));

    (async () => {
      for (let n = 1; n <= pages.length; n++) {
        if (cancelled) return;
        const canvas = canvasRefs.current[n - 1];
        if (!canvas) continue;

        // Never start a second render() on a canvas whose task hasn't settled.
        const prev = tasks.get(n);
        if (prev) {
          prev.task.cancel();
          await prev.settled;
          if (cancelled) return;
        }

        const page = await doc.getPage(n);
        if (cancelled) return;
        const viewport = page.getViewport({ scale: zoom });
        const ctx = canvas.getContext("2d");
        if (!ctx) continue;
        canvas.width = Math.floor(viewport.width * dpr);
        canvas.height = Math.floor(viewport.height * dpr);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;

        const task = page.render({
          canvasContext: ctx,
          viewport,
          transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
        });
        // Never rejects: a cancelled task is an expected outcome, not an error.
        const settled = task.promise.then(
          () => undefined,
          () => undefined,
        );
        tasks.set(n, { task, settled });
        await settled;
        if (tasks.get(n)?.task === task) tasks.delete(n);
      }
    })().catch((e: unknown) => {
      if (!cancelled) setError(errMsg(e));
    });

    return () => {
      cancelled = true;
      tasks.forEach((t) => t.task.cancel());
    };
  }, [doc, pages, zoom]);

  /* --------------------------------------------------- current page report */
  useEffect(() => {
    const el = viewportRef.current;
    if (!el || pages.length === 0) return;
    const visible = new Set<number>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const n = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) visible.add(n);
          else visible.delete(n);
        }
        if (visible.size) onCurrentPageRef.current(Math.min(...visible));
      },
      { root: el, threshold: 0.01 },
    );
    canvasRefs.current.slice(0, pages.length).forEach((c) => c && io.observe(c));
    return () => io.disconnect();
  }, [pages]);

  /* ------------------------------------------------------------- jump to page */
  useEffect(() => {
    if (jumpTo == null) return;
    const el = viewportRef.current;
    const canvas = canvasRefs.current[jumpTo - 1];
    if (el && canvas) el.scrollTo({ top: Math.max(0, canvas.offsetTop - 8), behavior: "smooth" });
    onJumped();
  }, [jumpTo, onJumped]);

  /* ----------------------------------------------------- ctrl/⌘ + wheel zoom */
  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      onZoomRef.current(clampZoom(zoomRef.current - e.deltaY * 0.0016));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  /* -------------------------------------------------------------- drag-pan */
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    const el = viewportRef.current;
    if (!el || e.button !== 0) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startL = el.scrollLeft;
    const startT = el.scrollTop;
    setGrabbing(true);
    // On document, so a fast drag that leaves the box doesn't get stuck.
    const move = (ev: MouseEvent) => {
      el.scrollLeft = startL - (ev.clientX - startX);
      el.scrollTop = startT - (ev.clientY - startY);
    };
    const up = () => {
      setGrabbing(false);
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }, []);

  return (
    <div
      ref={viewportRef}
      className={`ph-pc ph-scroll${grabbing ? " is-grabbing" : ""}`}
      onMouseDown={onMouseDown}
    >
      <div className="ph-pc-pages">
        {pages.map((p, i) => (
          <canvas
            key={i}
            data-page={i + 1}
            className="ph-pc-page"
            ref={(el) => {
              canvasRefs.current[i] = el;
            }}
            style={{ width: Math.floor(p.w * zoom), height: Math.floor(p.h * zoom) }}
          />
        ))}
      </div>
      {!doc && !error && <div className="ph-pc-msg">正在加载 PDF…</div>}
      {error && <div className="ph-pc-msg is-err">加载失败：{error}</div>}
    </div>
  );
}

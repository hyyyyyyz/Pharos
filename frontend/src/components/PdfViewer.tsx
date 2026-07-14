import { useCallback, useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PDFDocumentProxy } from "pdfjs-dist";
import "./PdfViewer.css";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const MIN = 0.4;
const MAX = 4;

/** A zoomable, pannable PDF viewer built on pdf.js (renders every page to canvas). */
export function PdfViewer({ url }: { url: string }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const pagesRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const baseWidthRef = useRef<number>(600);
  const [scale, setScale] = useState(1.2);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errMsg, setErrMsg] = useState("");

  // load the document whenever the URL changes
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    docRef.current = null;
    const task = pdfjs.getDocument({ url });
    task.promise
      .then(async (pdf) => {
        if (cancelled) return;
        docRef.current = pdf;
        const first = await pdf.getPage(1);
        baseWidthRef.current = first.getViewport({ scale: 1 }).width;
        // fit to width on first load
        const avail = (scrollRef.current?.clientWidth ?? 800) - 40;
        setScale(Math.min(2, Math.max(MIN, avail / baseWidthRef.current)));
        setStatus("ready");
      })
      .catch((e) => {
        if (cancelled) return;
        setErrMsg(String(e?.message ?? e));
        setStatus("error");
      });
    return () => {
      cancelled = true;
      task.destroy?.();
    };
  }, [url]);

  // (re)render all pages whenever the doc or scale changes
  useEffect(() => {
    const doc = docRef.current;
    const host = pagesRef.current;
    if (!doc || !host || status !== "ready") return;
    let cancelled = false;
    const dpr = window.devicePixelRatio || 1;
    (async () => {
      host.replaceChildren();
      for (let n = 1; n <= doc.numPages; n++) {
        if (cancelled) return;
        const page = await doc.getPage(n);
        const viewport = page.getViewport({ scale });
        const canvas = document.createElement("canvas");
        canvas.className = "pdf-page";
        canvas.width = Math.floor(viewport.width * dpr);
        canvas.height = Math.floor(viewport.height * dpr);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        const ctx = canvas.getContext("2d");
        if (!ctx) continue;
        host.appendChild(canvas);
        await page.render({
          canvasContext: ctx,
          viewport,
          transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
        }).promise;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [scale, status]);

  const zoom = useCallback(
    (delta: number) => setScale((s) => Math.min(MAX, Math.max(MIN, +(s + delta).toFixed(2)))),
    [],
  );
  const fitWidth = useCallback(() => {
    const avail = (scrollRef.current?.clientWidth ?? 800) - 40;
    setScale(Math.min(MAX, Math.max(MIN, avail / baseWidthRef.current)));
  }, []);

  // ctrl/⌘ + wheel to zoom
  const onWheel = (e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      zoom(e.deltaY < 0 ? 0.12 : -0.12);
    }
  };

  // drag to pan
  const pan = useRef<{ x: number; y: number; sl: number; st: number } | null>(null);
  const onDown = (e: React.MouseEvent) => {
    const el = scrollRef.current;
    if (!el) return;
    pan.current = { x: e.clientX, y: e.clientY, sl: el.scrollLeft, st: el.scrollTop };
    el.classList.add("grabbing");
  };
  const onMove = (e: React.MouseEvent) => {
    const el = scrollRef.current;
    const p = pan.current;
    if (!el || !p) return;
    el.scrollLeft = p.sl - (e.clientX - p.x);
    el.scrollTop = p.st - (e.clientY - p.y);
  };
  const endPan = () => {
    pan.current = null;
    scrollRef.current?.classList.remove("grabbing");
  };

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <button className="pdf-zbtn" onClick={() => zoom(-0.15)} title="缩小" aria-label="缩小">－</button>
        <span className="pdf-zoom" onClick={fitWidth} title="点击：适应宽度">{Math.round(scale * 100)}%</span>
        <button className="pdf-zbtn" onClick={() => zoom(0.15)} title="放大" aria-label="放大">＋</button>
        <button className="pdf-zbtn wide" onClick={fitWidth}>适应宽度</button>
      </div>
      <div
        className="pdf-scroll"
        ref={scrollRef}
        onWheel={onWheel}
        onMouseDown={onDown}
        onMouseMove={onMove}
        onMouseUp={endPan}
        onMouseLeave={endPan}
      >
        <div className="pdf-pages" ref={pagesRef} />
        {status === "loading" && <div className="pdf-msg">正在加载 PDF…</div>}
        {status === "error" && <div className="pdf-msg pdf-err">加载失败：{errMsg}</div>}
      </div>
    </div>
  );
}

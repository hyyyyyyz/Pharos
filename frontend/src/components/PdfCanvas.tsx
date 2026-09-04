import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";
import { Icons } from "../design/icons";
import { HighlightLayer, type HighlightKind } from "./HighlightLayer";
import { InkLayer } from "./InkLayer";
import "./PdfCanvas.css";

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 4;
/** Padding of the page column, both sides — used for the fit-width maths. */
const GUTTER = 40;
/**
 * A one-letter query in a long paper can match tens of thousands of times, and
 * every hit costs a Range + getClientRects (forced layout). Cap the work so a
 * stray keystroke cannot freeze the reader; real queries never come close.
 */
const MAX_MATCHES = 2000;

const clampZoom = (v: number): number => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, v));

const errMsg = (e: unknown): string =>
  e instanceof Error ? e.message : typeof e === "string" ? e : "未知错误";

interface PageSize {
  w: number;
  h: number;
}

/** A highlight box in CSS pixels, relative to the page's content-box origin. */
interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Where a normalised character came from: text div index + offset inside it. */
interface CharPos {
  d: number;
  o: number;
}

/** The searchable form of one page, plus the map back into the live DOM. */
interface PageIndex {
  divs: HTMLElement[];
  text: string;
  map: CharPos[];
}

interface Match {
  /** 1-based. */
  page: number;
  rects: Rect[];
}

/* ------------------------------------------------------------ normalisation */

/**
 * Ligatures are single glyphs in the PDF but several letters to a reader, so a
 * search for "efficient" has to survive the "ﬃ" the typesetter actually emitted.
 */
const LIGATURES: Record<string, string> = {
  "ﬀ": "ff",
  "ﬁ": "fi",
  "ﬂ": "fl",
  "ﬃ": "ffi",
  "ﬄ": "ffl",
  "ﬅ": "st",
  "ﬆ": "st",
};

/** Typographic characters folded to their ASCII equivalent before matching. */
const FOLD: Record<string, string> = {
  "‘": "'",
  "’": "'",
  "‚": "'",
  "‛": "'",
  "“": '"',
  "”": '"',
  "„": '"',
  "–": "-",
  "—": "-",
  "−": "-",
  "‐": "-",
  "‑": "-",
};

const HYPHENS = new Set(["-", "‐", "‑", "–", "—", "−"]);

const isSpace = (c: string): boolean => /\s/.test(c);

/**
 * Fold raw page text into a matchable string, keeping a per-character map back
 * to the DOM so a hit can be turned into a Range.
 *
 * The interesting part is what gets *dropped*: pdf.js emits one span per text
 * run, so a phrase is routinely split across runs and lines. Collapsing every
 * whitespace run (including the newlines we synthesise at `<br>`s) to a single
 * space makes "neural network" match whether pdf.js emitted it as one run, two
 * runs, or two lines; and swallowing a hyphen that sits immediately before a
 * line break rejoins "trans-\nlation" into "translation".
 */
function normalize(raw: string, pos: CharPos[]): { text: string; map: CharPos[] } {
  const out: string[] = [];
  const map: CharPos[] = [];
  // Index by UTF-16 unit, not code point, so offsets line up with indexOf().
  const emit = (s: string, p: CharPos): void => {
    for (let k = 0; k < s.length; k++) {
      out.push(s[k]!);
      map.push(p);
    }
  };

  let i = 0;
  while (i < raw.length) {
    const ch = raw[i]!;
    const at = pos[i] ?? { d: 0, o: 0 };

    const lig = LIGATURES[ch];
    if (lig !== undefined) {
      emit(lig, at);
      i++;
      continue;
    }
    // A soft hyphen is a rendering hint, never part of the word.
    if (ch === "­") {
      i++;
      continue;
    }
    if (HYPHENS.has(ch)) {
      let j = i + 1;
      while (j < raw.length && (raw[j] === " " || raw[j] === "\t")) j++;
      if (raw[j] === "\n") {
        i = j + 1; // hyphenated across a line break — drop hyphen and break
        continue;
      }
      emit("-", at);
      i++;
      continue;
    }
    if (isSpace(ch)) {
      let j = i;
      while (j < raw.length && isSpace(raw[j]!)) j++;
      if (out.length > 0) emit(" ", at); // collapse, but never lead with a space
      i = j;
      continue;
    }
    emit((FOLD[ch] ?? ch).toLowerCase(), at);
    i++;
  }
  return { text: out.join(""), map };
}

const normalizeQuery = (q: string): string =>
  normalize(
    q,
    Array.from({ length: q.length }, () => ({ d: 0, o: 0 })),
  ).text;

/**
 * Build the search index for one rendered text layer.
 *
 * Only divs pdf.js actually put in the document are indexed: `textDivs` also
 * holds the empty runs it built and then discarded, and a Range touching a
 * detached node throws.
 */
function buildIndex(textDivs: HTMLElement[]): PageIndex {
  const divs = textDivs.filter((d) => d.parentNode !== null && d.firstChild !== null);
  let raw = "";
  const pos: CharPos[] = [];
  divs.forEach((div, d) => {
    const s = div.textContent ?? "";
    for (let o = 0; o < s.length; o++) pos.push({ d, o });
    raw += s;
    // pdf.js marks an end-of-line by appending a <br> after the run's span.
    if (div.nextSibling?.nodeName === "BR") {
      pos.push({ d, o: s.length });
      raw += "\n";
    }
  });
  const { text, map } = normalize(raw, pos);
  return { divs, text, map };
}

/** Offset of `el` inside the scroll container, across the offsetParent chain. */
function offsetIn(el: HTMLElement, root: HTMLElement): { top: number; left: number } {
  let top = 0;
  let left = 0;
  let cur: HTMLElement | null = el;
  while (cur && cur !== root) {
    top += cur.offsetTop;
    left += cur.offsetLeft;
    cur = cur.offsetParent as HTMLElement | null;
  }
  return { top, left };
}

export interface PdfCanvasProps {
  doc: PDFDocumentProxy | null;
  /** Identifies the highlight set; omit `kind` to render without a highlight layer. */
  paperId: string;
  kind: HighlightKind | null;
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
 * one bitmap — the prototype's trick — would blur a real PDF), and lays pdf.js's
 * own text layer over each canvas so the paper can be selected, copied and
 * searched.
 */
export function PdfCanvas({
  doc,
  paperId,
  kind,
  zoom,
  onZoom,
  onFitScale,
  jumpTo,
  onJumped,
  onCurrentPage,
}: PdfCanvasProps): JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef<(HTMLDivElement | null)[]>([]);
  const canvasRefs = useRef<(HTMLCanvasElement | null)[]>([]);
  const textRefs = useRef<(HTMLDivElement | null)[]>([]);
  const findInputRef = useRef<HTMLInputElement>(null);

  /** Live render task per page, so a re-render never collides with an in-flight one. */
  const tasksRef = useRef(new Map<number, { task: RenderTask; settled: Promise<void> }>());
  /** Live text layer per page, kept so zoom can re-layout instead of rebuilding. */
  const layersRef = useRef(new Map<number, { layer: pdfjs.TextLayer; page: PDFPageProxy }>());
  const indexRef = useRef(new Map<number, PageIndex>());

  const [pages, setPages] = useState<PageSize[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [grabbing, setGrabbing] = useState(false);
  /** Space held: force pan mode even over text. */
  const [panLock, setPanLock] = useState(false);
  /** Bumped when text layers finish, to re-run the search against fresh DOM. */
  const [layerVersion, setLayerVersion] = useState(0);

  const [findOpen, setFindOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<Match[]>([]);
  const [current, setCurrent] = useState(-1);
  const matchesRef = useRef<Match[]>([]);
  matchesRef.current = matches;

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

  /* ------------------------------------------------------- canvas rendering */
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

  /* ----------------------------------------------------------- text layers */
  /**
   * Built once per document, NOT per zoom: pdf.js positions its spans in
   * `calc(var(--scale-factor) * …)`, so a zoom is a CSS variable write plus a
   * cheap re-layout, and rebuilding would mean re-fetching every page's text.
   */
  useEffect(() => {
    if (!doc || pages.length === 0) return;
    let cancelled = false;
    const layers = layersRef.current;
    const index = indexRef.current;

    (async () => {
      for (let n = 1; n <= pages.length; n++) {
        if (cancelled) return;
        const container = textRefs.current[n - 1];
        if (!container) continue;

        const page = await doc.getPage(n);
        const source = await page.getTextContent();
        if (cancelled) return;

        // Tear the old layer down before building the next, or a fast document
        // switch stacks two sets of spans on one page.
        layers.get(n)?.layer.cancel();
        container.textContent = "";

        const scale = zoomRef.current;
        container.style.setProperty("--scale-factor", String(scale));
        const layer = new pdfjs.TextLayer({
          textContentSource: source,
          container,
          viewport: page.getViewport({ scale }),
        });
        layers.set(n, { layer, page });
        // Rejects when cancelled — an expected outcome, not an error.
        const ok = await layer.render().then(
          () => true,
          () => false,
        );
        if (cancelled || !ok) return;

        // A zoom may have landed while this page's text was in flight.
        if (zoomRef.current !== scale) {
          container.style.setProperty("--scale-factor", String(zoomRef.current));
          layer.update({ viewport: page.getViewport({ scale: zoomRef.current }) });
        }
        index.set(n, buildIndex(layer.textDivs));
        setLayerVersion((v) => v + 1);
      }
    })().catch((e: unknown) => {
      if (!cancelled) setError(errMsg(e));
    });

    return () => {
      cancelled = true;
      layers.forEach(({ layer }) => layer.cancel());
      layers.clear();
      index.clear();
      textRefs.current.forEach((el) => {
        if (el) el.textContent = "";
      });
      // Drops pdf.js's hidden font-measuring canvases once nothing is pending.
      pdfjs.TextLayer.cleanup();
    };
  }, [doc, pages]);

  /* ------------------------------------------------- text layers follow zoom */
  // Declared before the search effect so hit rectangles are measured after the
  // spans have moved.
  useEffect(() => {
    layersRef.current.forEach(({ layer, page }, n) => {
      const container = textRefs.current[n - 1];
      if (container) container.style.setProperty("--scale-factor", String(zoom));
      layer.update({ viewport: page.getViewport({ scale: zoom }) });
    });
  }, [zoom, layerVersion]);

  /* ------------------------------------------------------------ find matches */
  const lastQueryRef = useRef("");
  useEffect(() => {
    const needle = normalizeQuery(query);
    if (!needle) {
      setMatches([]);
      setCurrent(-1);
      lastQueryRef.current = needle;
      return;
    }

    const found: Match[] = [];
    const range = document.createRange();
    outer: for (let n = 1; n <= pages.length; n++) {
      const idx = indexRef.current.get(n);
      const origin = textRefs.current[n - 1]?.getBoundingClientRect();
      if (!idx || !origin) continue;

      let from = 0;
      for (;;) {
        const k = idx.text.indexOf(needle, from);
        if (k === -1) break;
        from = k + needle.length;

        const s = idx.map[k];
        const e = idx.map[k + needle.length - 1];
        if (!s || !e) break;
        const startNode = idx.divs[s.d]?.firstChild;
        const endNode = idx.divs[e.d]?.firstChild;
        if (!startNode || !endNode) continue;

        try {
          range.setStart(startNode, Math.min(s.o, startNode.textContent?.length ?? 0));
          range.setEnd(endNode, Math.min(e.o + 1, endNode.textContent?.length ?? 0));
        } catch {
          continue; // a run that moved out from under us — skip this hit
        }
        const rects: Rect[] = [];
        for (const r of Array.from(range.getClientRects())) {
          if (r.width <= 0 || r.height <= 0) continue;
          rects.push({ x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height });
        }
        if (rects.length > 0) found.push({ page: n, rects });
        if (found.length >= MAX_MATCHES) break outer;
      }
    }

    setMatches(found);
    // A new query restarts at the first hit; a re-measure (zoom) keeps its place.
    const changed = lastQueryRef.current !== needle;
    lastQueryRef.current = needle;
    setCurrent((c) => {
      if (found.length === 0) return -1;
      if (changed || c < 0) return 0;
      return Math.min(c, found.length - 1);
    });
  }, [query, layerVersion, zoom, pages.length]);

  /* ------------------------------------------- scroll the current hit in view */
  useEffect(() => {
    const vp = viewportRef.current;
    const match = matches[current];
    if (!vp || !match) return;
    const pageEl = pageRefs.current[match.page - 1];
    const rect = match.rects[0];
    if (!pageEl || !rect) return;
    const off = offsetIn(pageEl, vp);
    const top = Math.max(0, off.top + rect.y - vp.clientHeight / 2 + rect.h / 2);
    const opts: ScrollToOptions = { top, behavior: "smooth" };
    // Only chase horizontally when the page is actually wider than the viewport.
    if (vp.scrollWidth > vp.clientWidth) {
      opts.left = Math.max(0, off.left + rect.x - vp.clientWidth / 2 + rect.w / 2);
    }
    vp.scrollTo(opts);
  }, [current, matches]);

  /** Highlight boxes grouped per page, with the active hit flagged. */
  const hlByPage = useMemo(() => {
    const byPage = new Map<number, { r: Rect; on: boolean }[]>();
    matches.forEach((m, i) => {
      const arr = byPage.get(m.page) ?? [];
      for (const r of m.rects) arr.push({ r, on: i === current });
      byPage.set(m.page, arr);
    });
    return byPage;
  }, [matches, current]);

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
    pageRefs.current.slice(0, pages.length).forEach((p) => p && io.observe(p));
    return () => io.disconnect();
  }, [pages]);

  /* ------------------------------------------------------------ jump to page */
  useEffect(() => {
    if (jumpTo == null) return;
    const el = viewportRef.current;
    const pageEl = pageRefs.current[jumpTo - 1];
    if (el && pageEl) {
      el.scrollTo({ top: Math.max(0, offsetIn(pageEl, el).top - 8), behavior: "smooth" });
    }
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

  /* --------------------------------------------------------- select vs. pan */
  /**
   * Both gestures are a left-drag, so the target decides: a drag that starts on
   * a glyph selects (the text layer is the only thing under the cursor there),
   * a drag on blank paper or the margin pans. Middle-drag and space-held always
   * pan, so text-dense pages are never trapped without a pan gesture.
   */
  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const el = viewportRef.current;
      if (!el) return;
      const target = e.target as HTMLElement;
      // The highlight toolbar is painted inside the pan surface, so a press on
      // it would otherwise start a pan — and, worse, the preventDefault below
      // would stop its note textarea from ever taking focus on click. Chrome is
      // not paper: leave it entirely alone, panlock included.
      if (target.closest(".ph-hl-pop") !== null) return;
      // Same for the ink canvas: a pen (or mouse, with the tool on) drawing a
      // stroke must not pan the page under it. The ink layer stops its own
      // pointer events; this catches the compatibility mouse events pen input
      // also synthesises, which bypass pointer handlers entirely.
      if (target.closest(".ph-ink") !== null) return;
      const onText = !panLock && e.button === 0 && target.closest(".ph-pc-tl") !== null;
      if (onText) return; // let the browser run its own selection drag
      if (e.button !== 0 && e.button !== 1) return;

      e.preventDefault(); // suppresses the stray selection a pan would start
      // preventDefault also stops the browser collapsing the *existing*
      // selection, so a click on blank paper would otherwise leave the previous
      // selection alive — and the highlight toolbar, which re-claims it on
      // mouseup, could never be dismissed by clicking away. Middle-drag is
      // exempt: it is the "pan without disturbing anything" escape hatch.
      if (e.button === 0) window.getSelection()?.removeAllRanges();
      el.focus({ preventScroll: true });
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
    },
    [panLock],
  );

  /* --------------------------------------------------------------- find bar */
  const openFind = useCallback(() => {
    setFindOpen(true);
    // After paint, so the input exists.
    requestAnimationFrame(() => {
      findInputRef.current?.focus();
      findInputRef.current?.select();
    });
  }, []);

  const closeFind = useCallback(() => {
    setFindOpen(false);
    setQuery("");
    viewportRef.current?.focus({ preventScroll: true });
  }, []);

  const step = useCallback((delta: number) => {
    const total = matchesRef.current.length;
    if (total === 0) return;
    setCurrent((c) => (c + delta + total) % total);
  }, []);

  /* ------------------------------------------------------ keyboard shortcuts */
  useEffect(() => {
    const inReader = (): boolean =>
      wrapRef.current?.contains(document.activeElement) ?? false;
    const editable = (t: EventTarget | null): boolean => {
      const el = t as HTMLElement | null;
      return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (!inReader()) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault(); // take over the browser's find, which cannot see our pages
        openFind();
        return;
      }
      if (e.key === "Escape" && findOpen) {
        e.preventDefault();
        closeFind();
        return;
      }
      if (e.key === " " && !editable(e.target)) {
        e.preventDefault(); // stop the page-down the space bar would otherwise do
        setPanLock(true);
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.key === " ") setPanLock(false);
    };
    // A key-up delivered to another window would strand the lock on.
    const release = () => setPanLock(false);

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", release);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", release);
    };
  }, [openFind, closeFind, findOpen]);

  const onFindKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        step(e.shiftKey ? -1 : 1);
      }
    },
    [step],
  );

  const hasQuery = normalizeQuery(query).length > 0;
  const cls = ["ph-pc", "ph-scroll", grabbing && "is-grabbing", panLock && "is-panlock"]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="ph-pc-wrap" ref={wrapRef}>
      <div ref={viewportRef} className={cls} tabIndex={0} onMouseDown={onMouseDown}>
        <div className="ph-pc-pages">
          {pages.map((p, i) => (
            <div
              key={i}
              data-page={i + 1}
              className="ph-pc-page"
              ref={(el) => {
                pageRefs.current[i] = el;
              }}
              style={{ width: Math.floor(p.w * zoom), height: Math.floor(p.h * zoom) }}
            >
              <canvas
                className="ph-pc-cv"
                ref={(el) => {
                  canvasRefs.current[i] = el;
                }}
              />
              <div
                className="ph-pc-tl"
                ref={(el) => {
                  textRefs.current[i] = el;
                }}
              />
              <div className="ph-pc-hl" aria-hidden="true">
                {(hlByPage.get(i + 1) ?? []).map((h, j) => (
                  <span
                    key={j}
                    className={`ph-pc-hit${h.on ? " is-on" : ""}`}
                    style={{ left: h.r.x, top: h.r.y, width: h.r.w, height: h.r.h }}
                  />
                ))}
              </div>
              {kind && (
                <HighlightLayer
                  paperId={paperId}
                  kind={kind}
                  page={i + 1}
                  /* The raw zoom, NOT the floored page width: the text layer
                     positions glyphs from the unfloored scale, so flooring here
                     would drift a mark off the words it covers. */
                  scale={zoom}
                  pageHeight={p.h}
                />
              )}
              {kind && (
                <InkLayer
                  paperId={paperId}
                  kind={kind}
                  page={i + 1}
                  scale={zoom}
                  pageHeight={p.h}
                />
              )}
            </div>
          ))}
        </div>
        {!doc && !error && <div className="ph-pc-msg">正在加载 PDF…</div>}
        {error && <div className="ph-pc-msg is-err">加载失败：{error}</div>}
      </div>

      {doc &&
        (findOpen ? (
          <div className="ph-pc-find" role="search">
            <span className="ph-pc-find-ico">
              <Icons.search size={13} />
            </span>
            <input
              ref={findInputRef}
              className="ph-pc-find-in"
              type="text"
              value={query}
              placeholder="在文档中查找"
              aria-label="在文档中查找"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onFindKeyDown}
            />
            <span className={`ph-pc-find-n${hasQuery && matches.length === 0 ? " is-none" : ""}`}>
              {!hasQuery ? "" : matches.length === 0 ? "无结果" : `${current + 1} / ${matches.length}`}
            </span>
            <button
              className="ph-pc-find-btn is-prev"
              title="上一个 (Shift+Enter)"
              disabled={matches.length === 0}
              onClick={() => step(-1)}
            >
              <Icons.caretD size={13} />
            </button>
            <button
              className="ph-pc-find-btn"
              title="下一个 (Enter)"
              disabled={matches.length === 0}
              onClick={() => step(1)}
            >
              <Icons.caretD size={13} />
            </button>
            <button className="ph-pc-find-btn" title="关闭 (Esc)" onClick={closeFind}>
              <Icons.close size={11} />
            </button>
          </div>
        ) : (
          <button className="ph-pc-find-open" title="查找 (Ctrl/⌘+F)" onClick={openFind}>
            <Icons.search size={14} />
          </button>
        ))}
    </div>
  );
}

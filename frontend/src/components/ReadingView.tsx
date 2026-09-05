import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { api } from "../api/client";
import type { InkStrokeRow, Paper, PdfKind } from "../api/types";
import { Icons } from "../design/icons";
import { INK_COLORS, WATER_COLORS, rankInkColors } from "../lib/ink";
import { InkToolPopover } from "./InkToolPopover";

/** Eraser presets: radius in CSS pixels. The on-page preview circle and the
 *  erase reach both read this, so the button, the circle and the effect can
 *  never disagree. */
const ERASER_SIZES = [
  { size: 8, label: "小" },
  { size: 16, label: "中" },
  { size: 28, label: "大" },
];

import type { DocumentRef } from "../lib/paperChat";
import { TRANSLATE_STAGES, dash, isJobActive, stageIndex, toVM } from "../lib/model";
import {
  capHistory,
  isAiOpen,
  pdfTranslationEnabled,
  useSession,
  useUI,
  type InkMode,
  type ReadMode,
} from "../store";
import { AiPanel } from "./AiPanel";
import { OutlinePanel, type OutlineEntry } from "./OutlinePanel";
import { PdfCanvas } from "./PdfCanvas";
import "./ReadingView.css";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 4;
const THUMB_WIDTH = 120;
/** Backend errors can be a whole stack trace; the panel shows the gist. */
const ERROR_MAX = 200;

const clampZoom = (v: number): number => Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, v));

const errMsg = (e: unknown): string =>
  e instanceof Error ? e.message : typeof e === "string" ? e : "未知错误";

/** A pdf.js indirect object reference (what an explicit destination points at). */
interface PdfRef {
  num: number;
  gen: number;
}
interface RawOutlineItem {
  title: string;
  dest: string | unknown[] | null;
  items?: RawOutlineItem[];
}

/** Resolve an outline destination to a 1-based page; never throws. */
async function destToPage(doc: PDFDocumentProxy, dest: RawOutlineItem["dest"]): Promise<number | null> {
  try {
    const explicit = typeof dest === "string" ? await doc.getDestination(dest) : dest;
    if (!Array.isArray(explicit) || explicit.length === 0) return null;
    const target = explicit[0];
    if (typeof target === "number") return target + 1; // already a page index
    return (await doc.getPageIndex(target as PdfRef)) + 1;
  } catch {
    return null;
  }
}

async function flattenOutline(doc: PDFDocumentProxy): Promise<OutlineEntry[]> {
  const raw = (await doc.getOutline()) as RawOutlineItem[] | null;
  if (!raw) return [];
  const out: OutlineEntry[] = [];
  const walk = async (items: RawOutlineItem[], depth: number): Promise<void> => {
    for (const item of items) {
      out.push({ title: item.title, page: await destToPage(doc, item.dest), depth });
      if (item.items?.length) await walk(item.items, depth + 1);
    }
  };
  await walk(raw, 0);
  return out;
}

export function ReadingView({ paperId }: { paperId: string }): JSX.Element {
  const qc = useQueryClient();
  const readMode = useUI((s) => s.readMode);
  const setReadMode = useUI((s) => s.setReadMode);
  const outlineOpen = useUI((s) => s.outlineOpen);
  const toggleOutline = useUI((s) => s.toggleOutline);
  const outlineMode = useUI((s) => s.outlineMode);
  const aiOpen = useUI(isAiOpen);
  const toggleAI = useUI((s) => s.toggleAI);
  const openSettings = useUI((s) => s.openSettings);
  const pdfTx = useSession(pdfTranslationEnabled);

  /* --------------------------------------------------------- the paper */
  const { data: paper } = useQuery({
    queryKey: ["paper", paperId],
    queryFn: () => api.getPaper(paperId),
    refetchInterval: (q) =>
      isJobActive((q.state.data as Paper | undefined)?.latest_job) ? 1500 : false,
  });
  const vm = useMemo(() => (paper ? toVM(paper) : null), [paper]);

  const translate = useMutation({
    mutationFn: () => api.translate(paperId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["papers"] });
      void qc.invalidateQueries({ queryKey: ["paper", paperId] });
    },
  });

  // Refresh the library list once when a job settles — guarded, because an
  // unconditional invalidate in an effect used to loop forever.
  const jobStatus = paper?.latest_job?.status ?? null;
  const prevJobStatus = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevJobStatus.current;
    prevJobStatus.current = jobStatus;
    if (prev === null || prev === jobStatus) return;
    if (jobStatus === "done" || jobStatus === "error") {
      void qc.invalidateQueries({ queryKey: ["papers"] });
    }
  }, [jobStatus, qc]);

  /* ------------------------------------------------------ which state shows */
  const status = vm?.status ?? "untranslated";
  const isTranslated = status === "translated";

  /**
   * The mode actually rendered.
   *
   * With translation on this is just `readMode` — the on-path is untouched.
   * With it off, a paper that has no Chinese PDF has no other mode to be in, so
   * it collapses to 原文 and the reader opens straight into the source. That is
   * what makes the 未译/翻译中/失败 screens below unreachable rather than merely
   * hidden: `showPdf` is then always true.
   *
   * A paper translated BEFORE the setting was turned off keeps `readMode`. The
   * setting governs whether Pharos *spends* on new translations, not whether it
   * shows work already paid for and sitting on disk — hiding that would be
   * taking something away, which is not what "don't translate my PDFs" asks for.
   */
  const effMode: ReadMode = pdfTx || isTranslated ? readMode : "original";

  /* The 中文/中英/原文 group. Off + untranslated leaves 原文 as the only member,
     and a one-button segmented control is noise pretending to be a choice — so
     the group goes entirely, per "the apparatus must genuinely disappear". */
  const showModes = pdfTx || isTranslated;

  // 原文 always wins: it is the only path to the source PDF before/after a
  // failed translation.
  const showPdf = effMode === "original" || isTranslated;

  /* Which PDF to show, as a plain string. Deliberately NOT the PdfSource object:
     this memo recomputes whenever `vm.job` gets a new identity (every /papers
     refetch), and returning a fresh object each time would re-run the loader
     effect below and tear down a perfectly good document. A string compares
     equal, so the reload only happens when the file actually changes. */
  const pdfKind = useMemo<PdfKind | null>(() => {
    if (!showPdf) return null;
    let kind: PdfKind = effMode === "zh" ? "mono" : effMode === "bilingual" ? "dual" : "original";
    // The backend may produce a mono-only result; don't request a 404.
    if (kind === "dual" && vm?.job && !vm.job.has_dual) kind = "mono";
    return kind;
  }, [showPdf, effMode, vm?.job]);

  /* -------------------------------------------------------- the pdf.js doc */
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [docError, setDocError] = useState<string | null>(null);

  useEffect(() => {
    setDoc(null);
    setDocError(null);
    if (!pdfKind) return;
    let cancelled = false;
    // pdfSource(), not pdfUrl(): /papers/{id}/pdf/{kind} requires a bearer token
    // like every other endpoint, and a bare URL cannot carry one. pdf.js issues
    // the requests itself, so httpHeaders rides along on the initial fetch and
    // on every range request. Built here rather than in the memo so the token
    // read is as late as possible.
    const task = pdfjs.getDocument(api.pdfSource(paperId, pdfKind));
    task.promise.then(
      (d) => {
        if (cancelled) return;
        setDoc(d);
      },
      (e: unknown) => {
        if (!cancelled) setDocError(errMsg(e));
      },
    );
    return () => {
      cancelled = true;
      // Destroying the loading task destroys the document it produced.
      void task.destroy().catch(() => undefined);
    };
  }, [pdfKind, paperId]);

  /* ------------------------------------------------------------ zoom / fit */
  const [zoom, setZoom] = useState(1);
  const [fitScale, setFitScale] = useState(1);
  const [fitMode, setFitMode] = useState(true);
  const fitModeRef = useRef(fitMode);
  fitModeRef.current = fitMode;
  // 锁定画布: per-view, like zoom/fitMode — not worth persisting across a
  // reload, and doing so would risk leaving a reader stuck locked with no
  // visible reason why the page stopped responding to a finger.
  const [canvasLocked, setCanvasLocked] = useState(false);

  // Fit width on load and whenever the document changes.
  useEffect(() => {
    setFitMode(true);
  }, [pdfKind, paperId]);

  const handleFitScale = useCallback((scale: number) => {
    setFitScale(scale);
    if (fitModeRef.current) setZoom(scale);
  }, []);
  const handleZoom = useCallback((next: number) => {
    setFitMode(false);
    setZoom(clampZoom(next));
  }, []);
  const zoomStep = useCallback((d: number) => {
    setFitMode(false);
    setZoom((z) => clampZoom(z + d));
  }, []);
  const fitWidth = useCallback(() => {
    setFitMode(true);
    setZoom(fitScale);
  }, [fitScale]);

  /* -------------------------------------------------------- outline + pages */
  const [outline, setOutline] = useState<OutlineEntry[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const [jumpTo, setJumpTo] = useState<number | null>(null);

  useEffect(() => {
    setOutline([]);
    setCurrentPage(1);
    if (!doc) return;
    let cancelled = false;
    flattenOutline(doc)
      .then((entries) => {
        if (!cancelled) setOutline(entries);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [doc]);

  const onJump = useCallback((page: number) => setJumpTo(page), []);
  const onJumped = useCallback(() => setJumpTo(null), []);
  const onCurrentPage = useCallback((page: number) => setCurrentPage(page), []);

  /* ------------------------------------------------------------- stylus ink */

  const inkMode = useUI((s) => s.inkMode);
  const setInkMode = useUI((s) => s.setInkMode);
  const inkColor = useUI((s) => s.inkColor);
  const setInkColor = useUI((s) => s.setInkColor);
  const inkColorUsage = useUI((s) => s.inkColorUsage);
  const inkWidth = useUI((s) => s.inkWidth);
  const setInkWidth = useUI((s) => s.setInkWidth);
  const inkFingerDraw = useUI((s) => s.inkFingerDraw);
  const toggleInkFingerDraw = useUI((s) => s.toggleInkFingerDraw);
  const inkEraserSize = useUI((s) => s.inkEraserSize);
  const setInkEraserSize = useUI((s) => s.setInkEraserSize);
  const inkEraseMode = useUI((s) => s.inkEraseMode);
  const setInkEraseMode = useUI((s) => s.setInkEraseMode);
  const inkPast = useUI((s) => s.inkPast);
  const inkFuture = useUI((s) => s.inkFuture);
  const inkOpsKey = useUI((s) => s.inkOpsKey);
  const resetInkOps = useUI((s) => s.resetInkOps);

  // Quick bar: 墨黑 pinned, then the top 3 of the rest by recency-weighted
  // usage (`rankInkColors`); everything else lives in the 更多颜色 panel.
  // Recomputed only when usage actually changes — a colour just picked
  // should not visibly reshuffle the bar the reader is still looking at.
  const quickColorKeys = useMemo(
    () => rankInkColors(INK_COLORS, inkColorUsage, Date.now()),
    [inkColorUsage],
  );
  const quickColors = useMemo(
    () => quickColorKeys.map((key) => INK_COLORS.find((c) => c.key === key)).filter((c) => c != null),
    [quickColorKeys],
  );
  /**
   * The collapsible tray under the active tool's popover — 调色盘 or 粗细,
   * at most one at a time ("每次只启用一个，不然会重叠"). Lives in the store
   * so a stroke starting down in `InkLayer` can shut it (see `inkTray`).
   */
  const openTray = useUI((s) => s.inkTray);
  const setOpenTray = useUI((s) => s.setInkTray);
  const toggleTray = useCallback(
    (tray: "color" | "width") => setOpenTray(openTray === tray ? null : tray),
    [openTray, setOpenTray],
  );

  /**
   * Pick a tool from the subbar: a second tap on the lit one puts the pen
   * away, and ANY change shuts the tray — so switching pen → 水彩笔 never
   * leaves the pen's palette hanging open over the new tool's own.
   */
  const pickTool = useCallback(
    (mode: InkMode) => {
      setOpenTray(null);
      setInkMode(inkMode === mode ? "off" : mode);
    },
    [inkMode, setInkMode, setOpenTray],
  );

  const inkKey = pdfKind ? `${paperId} ${pdfKind}` : "";

  // Stacks belong to one document+rendition; switching either wipes them, so
  // an undo can never replay the previous paper's ops against this one's API.
  const inkUndoable = inkOpsKey === inkKey && inkPast.length > 0;
  const inkRedoable = inkOpsKey === inkKey && inkFuture.length > 0;
  useEffect(() => resetInkOps, [resetInkOps]);

  /**
   * Undo/redo as inverse-application: every op that lands on the *other*
   * stack is rewritten with the rows the network actually returned, so ids
   * stay live through add→undo→redo→undo chains. A stale id in a delete op
   * would 404 and strand the row as un-undoable.
   *
   * The protocol every op follows: `added` rows (and a single add's stroke)
   * must carry *live* ids whenever the op sits on top of a stack, because the
   * inverse deletes them; `removed` rows are payloads the inverse re-creates
   * with fresh ids. That is why an edit undone now stores the freshly
   * recreated rows under `removed` while `added` keeps only its payloads.
   */
  const recreateStrokes = useCallback(
    async (strokes: InkStrokeRow[]): Promise<InkStrokeRow[]> => {
      const remade: InkStrokeRow[] = [];
      for (const stroke of strokes) {
        try {
          remade.push(
            await api.ink.create(paperId, {
              kind: stroke.kind,
              page: stroke.page,
              points: stroke.points,
              color: stroke.color,
              width: stroke.width,
            }),
          );
        } catch {
          /* a stroke the server refused is simply lost from the redo */
        }
      }
      return remade;
    },
    [paperId],
  );

  const undoInk = useCallback(async () => {
    if (!pdfKind) return;
    const s = useUI.getState();
    if (s.inkOpsKey !== inkKey || s.inkPast.length === 0) return;
    const op = s.inkPast[s.inkPast.length - 1];
    useUI.setState((st) => ({ inkPast: st.inkPast.slice(0, -1) }));
    let inverse: typeof op;
    if (op.kind === "add") {
      await api.ink.remove(op.stroke.id).catch(() => undefined);
      // Redo re-creates from the payload; the id it carries is stale but only
      // a payload — the redo path replaces it with the fresh row.
      inverse = { kind: "add", stroke: op.stroke };
    } else if (op.kind === "remove") {
      inverse = { kind: "remove", strokes: await recreateStrokes(op.strokes) };
    } else {
      for (const row of op.added) await api.ink.remove(row.id).catch(() => undefined);
      inverse = {
        kind: "edit",
        removed: await recreateStrokes(op.removed),
        added: op.added,
      };
    }
    useUI.setState((st) => ({ inkFuture: capHistory([...st.inkFuture, inverse]) }));
    void qc.invalidateQueries({ queryKey: ["ink", paperId, pdfKind] });
  }, [inkKey, paperId, pdfKind, qc, recreateStrokes]);

  const redoInk = useCallback(async () => {
    if (!pdfKind) return;
    const s = useUI.getState();
    if (s.inkOpsKey !== inkKey || s.inkFuture.length === 0) return;
    const op = s.inkFuture[s.inkFuture.length - 1];
    useUI.setState((st) => ({ inkFuture: st.inkFuture.slice(0, -1) }));
    let reapplied: typeof op;
    if (op.kind === "add") {
      try {
        const row = await api.ink.create(paperId, {
          kind: op.stroke.kind,
          page: op.stroke.page,
          points: op.stroke.points,
          color: op.stroke.color,
          width: op.stroke.width,
        });
        reapplied = { kind: "add", stroke: row };
      } catch {
        void qc.invalidateQueries({ queryKey: ["ink", paperId, pdfKind] });
        return;
      }
    } else if (op.kind === "remove") {
      for (const stroke of op.strokes) {
        await api.ink.remove(stroke.id).catch(() => undefined);
      }
      reapplied = op;
    } else {
      for (const row of op.removed) await api.ink.remove(row.id).catch(() => undefined);
      const added = await recreateStrokes(op.added);
      reapplied = { kind: "edit", removed: op.removed, added };
      if (added.length !== op.added.length) {
        // Part of the redo was refused: the cache no longer matches this op,
        // so keep it off the stacks and let the refetch settle the truth.
        void qc.invalidateQueries({ queryKey: ["ink", paperId, pdfKind] });
        return;
      }
    }
    useUI.setState((st) => ({ inkPast: capHistory([...st.inkPast, reapplied]) }));
    void qc.invalidateQueries({ queryKey: ["ink", paperId, pdfKind] });
  }, [inkKey, paperId, pdfKind, qc, recreateStrokes]);

  /* ----------------------------------------------------------- thumbnails */
  const [thumbs, setThumbs] = useState<(string | null)[]>([]);
  const thumbsRef = useRef<(string | null)[]>([]);
  thumbsRef.current = thumbs;
  const wantThumbs = outlineOpen && outlineMode === "thumbs";

  useEffect(() => {
    setThumbs(doc ? new Array<string | null>(doc.numPages).fill(null) : []);
  }, [doc]);

  useEffect(() => {
    if (!doc || !wantThumbs) return;
    let cancelled = false;
    (async () => {
      for (let n = 1; n <= doc.numPages; n++) {
        if (cancelled) return;
        if (thumbsRef.current[n - 1]) continue;
        try {
          const page = await doc.getPage(n);
          if (cancelled) return;
          const base = page.getViewport({ scale: 1 });
          const viewport = page.getViewport({ scale: THUMB_WIDTH / base.width });
          const canvas = document.createElement("canvas");
          canvas.width = Math.ceil(viewport.width);
          canvas.height = Math.ceil(viewport.height);
          const ctx = canvas.getContext("2d");
          if (!ctx) continue;
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          await page.render({ canvasContext: ctx, viewport }).promise;
          if (cancelled) return;
          const url = canvas.toDataURL("image/jpeg", 0.7);
          setThumbs((prev) => {
            const next = prev.slice();
            next[n - 1] = url;
            return next;
          });
        } catch {
          /* a page that won't rasterise just stays blank */
        }
        // Yield so thumbnailing never blocks the main viewer.
        await new Promise((r) => setTimeout(r, 0));
      }
    })().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [doc, wantThumbs]);

  /* ------------------------------------------------------------------ view */
  const pageCount = doc?.numPages ?? vm?.pages ?? 0;
  const displayFilename = vm?.file ?? "";
  const jobError = (vm?.job?.error ?? "").trim();
  const failMessage = jobError
    ? jobError.length > ERROR_MAX
      ? `${jobError.slice(0, ERROR_MAX)}…`
      : jobError
    : "翻译任务失败，未返回错误详情。";

  const MODES: { key: ReadMode; label: string }[] = [
    { key: "zh", label: "中文" },
    { key: "bilingual", label: "中英" },
    { key: "original", label: "原文" },
  ];
  const stage = stageIndex(vm?.job ?? null);
  const documentRef = useMemo<DocumentRef>(
    () => ({
      key: `paper:${encodeURIComponent(paperId)}`,
      kind: "paper",
      title: vm?.title?.trim() || displayFilename || "未命名论文",
      paperId,
    }),
    [displayFilename, paperId, vm?.title],
  );

  return (
    <div className="ph-rv">
      {outlineOpen && (
        <OutlinePanel
          entries={outline}
          pageCount={pageCount}
          currentPage={currentPage}
          thumbs={thumbs}
          onJump={onJump}
        />
      )}

      <section className="ph-rv-main">
        <div className="ph-rv-bar">
          {!outlineOpen && (
            <button className="ph-rv-outline-btn" title="展开大纲" onClick={toggleOutline}>
              <Icons.panelL />
            </button>
          )}
          <div className="ph-rv-file" title={displayFilename}>{displayFilename}</div>
          <div className="ph-rv-spacer" />
          {showModes && (
            <div className="ph-rv-seg">
              {MODES.map((m) => {
                // Without a translation there is no 中文/中英 rendition — but 原文
                // must stay reachable, so the group keeps all three and locks
                // the two that have no file behind them. Unreachable while the
                // setting is off: `showModes` only lets the group render then
                // for an already-translated paper, where nothing is locked.
                const locked = m.key !== "original" && !isTranslated;
                return (
                  <button
                    key={m.key}
                    className={`ph-rv-seg-btn${effMode === m.key ? " is-on" : ""}`}
                    disabled={locked}
                    title={locked ? "尚未译出" : undefined}
                    onClick={() => setReadMode(m.key)}
                  >
                    {m.label}
                  </button>
                );
              })}
            </div>
          )}
          <button
            className={`ph-rv-ai-btn${aiOpen ? " is-on" : ""}`}
            title="AI 对话"
            onClick={toggleAI}
          >
            <span className="ph-rv-ico">
              <Icons.spark />
            </span>
            AI 对话
          </button>
        </div>

        <div className="ph-rv-body">
          {showPdf ? (
            <div className="ph-rv-pdf">
              <div className="ph-rv-subbar">
                <div className="ph-rv-zoom">
                  <button className="ph-rv-zoom-btn" title="缩小" onClick={() => zoomStep(-0.1)}>
                    −
                  </button>
                  <span className="ph-rv-zoom-val">{Math.round(zoom * 100)}%</span>
                  <button
                    className="ph-rv-zoom-btn is-plus"
                    title="放大"
                    onClick={() => zoomStep(0.1)}
                  >
                    +
                  </button>
                </div>
                <button className="ph-rv-fit" title="适应宽度" onClick={fitWidth}>
                  适应宽度
                </button>
                <button
                  className={`ph-rv-ink-btn${canvasLocked ? " is-on" : ""}`}
                  title={
                    canvasLocked
                      ? "画布已锁定：双指拖动平移，单指无效，解锁后恢复单指拖动/双指缩放"
                      : "锁定画布：防止误触缩放和平移"
                  }
                  aria-pressed={canvasLocked}
                  onClick={() => setCanvasLocked((v) => !v)}
                >
                  <Icons.lock size={14} />
                </button>
                <span className="ph-rv-subbar-sep" />
                {/* 手写: pen writes, eraser removes (whole strokes or, in
                    局部, splits them), lasso selects strokes for moving and
                    recolouring, and the tools share one popover row for the
                    options that matter to each.
                    Ink mode must be reachable on desktop too — a mouse can
                    draw — but the popover only matters while a tool is on. */}
                <div className="ph-rv-inkwrap">
                  <button
                    className={`ph-rv-ink-btn${inkMode === "draw" ? " is-on" : ""}`}
                    title="手写笔"
                    aria-pressed={inkMode === "draw"}
                    onClick={() => pickTool("draw")}
                  >
                    <Icons.pen size={15} />
                  </button>
                  <button
                    className={`ph-rv-ink-btn${inkMode === "water" ? " is-on" : ""}`}
                    title="水彩笔：色底不遮字"
                    aria-pressed={inkMode === "water"}
                    onClick={() => pickTool("water")}
                  >
                    <Icons.droplet size={14} />
                  </button>
                  <button
                    className={`ph-rv-ink-btn${inkMode === "erase" ? " is-on" : ""}`}
                    title="橡皮"
                    aria-pressed={inkMode === "erase"}
                    onClick={() => pickTool("erase")}
                  >
                    <Icons.eraser size={15} />
                  </button>
                  <button
                    className={`ph-rv-ink-btn${inkMode === "select" ? " is-on" : ""}`}
                    title="套索选择"
                    aria-pressed={inkMode === "select"}
                    onClick={() => pickTool("select")}
                  >
                    <Icons.lasso />
                  </button>
                  <button
                    className={`ph-rv-ink-btn${inkMode === "laser" ? " is-on" : ""}`}
                    title="激光笔：指一下，自动消失"
                    aria-pressed={inkMode === "laser"}
                    onClick={() => pickTool("laser")}
                  >
                    <Icons.laser />
                  </button>
                  <button
                    className={`ph-rv-ink-btn${inkMode === "style" ? " is-on" : ""}`}
                    title="改笔迹粗细与颜色：笔碰到已写的字即可改样式"
                    aria-pressed={inkMode === "style"}
                    onClick={() => pickTool("style")}
                  >
                    <Icons.styleBrush />
                  </button>
                  <button
                    className={`ph-rv-ink-btn${inkMode === "tape" ? " is-on" : ""}`}
                    title="胶带：拖出一条，盖住/显示切换靠点它"
                    aria-pressed={inkMode === "tape"}
                    onClick={() => pickTool("tape")}
                  >
                    <Icons.tape />
                  </button>
                  {/* Each tool gets its own popover: the pen carries colour,
                      width and the finger-draw switch; the eraser carries its
                      reach and its 整笔/局部 manner; the lasso explains the
                      moves it enables. Colours are meaningless to an eraser,
                      which is why the three never mix. */}
                  {inkMode === "draw" && (
                    <InkToolPopover
                      label="手写工具"
                      palette={INK_COLORS}
                      quick={quickColors}
                      color={inkColor}
                      onColor={setInkColor}
                      width={inkWidth}
                      onWidth={setInkWidth}
                      tray={openTray}
                      onTray={toggleTray}
                    >
                      <button
                        className={`ph-rv-ink-finger${inkFingerDraw ? " is-on" : ""}`}
                        title="手指书写：默认只认笔，打开后手指也能画"
                        aria-pressed={inkFingerDraw}
                        onClick={toggleInkFingerDraw}
                      >
                        手指书写
                      </button>
                    </InkToolPopover>
                  )}
                  {inkMode === "water" && (
                    <InkToolPopover
                      label="水彩笔工具"
                      palette={WATER_COLORS}
                      quick={WATER_COLORS}
                      color={inkColor}
                      onColor={setInkColor}
                      width={inkWidth}
                      onWidth={setInkWidth}
                      tray={openTray}
                      onTray={toggleTray}
                      note="色底不遮字"
                    />
                  )}
                  {inkMode === "erase" && (
                    <div className="ph-rv-inkbar" role="toolbar" aria-label="橡皮工具">
                      <div className="ph-rv-ink-row">
                        <div className="ph-rv-ink-seg">
                          <button
                            className={`ph-rv-ink-seg-btn${inkEraseMode === "stroke" ? " is-on" : ""}`}
                            aria-pressed={inkEraseMode === "stroke"}
                            onClick={() => setInkEraseMode("stroke")}
                          >
                            整笔
                          </button>
                          <button
                            className={`ph-rv-ink-seg-btn${inkEraseMode === "pixel" ? " is-on" : ""}`}
                            aria-pressed={inkEraseMode === "pixel"}
                            onClick={() => setInkEraseMode("pixel")}
                          >
                            局部
                          </button>
                        </div>
                        <span className="ph-rv-ink-sep" />
                        {ERASER_SIZES.map((s) => (
                          <button
                            key={s.size}
                            className={`ph-rv-ink-width${inkEraserSize === s.size ? " is-on" : ""}`}
                            title={`${s.label}（${s.size}px）`}
                            aria-label={`橡皮 ${s.label}`}
                            aria-pressed={inkEraserSize === s.size}
                            onClick={() => setInkEraserSize(s.size)}
                          >
                            <span
                              style={{ width: s.size, height: s.size }}
                            />
                          </button>
                        ))}
                        <span className="ph-rv-ink-sep" />
                        <span className="ph-rv-ink-note">
                          {inkEraseMode === "stroke"
                            ? "拖动擦除整笔 · 圈内即擦除范围"
                            : "擦过处笔迹断开 · 可撤销"}
                        </span>
                      </div>
                    </div>
                  )}
                  {inkMode === "select" && (
                    <div className="ph-rv-inkbar" role="toolbar" aria-label="套索工具">
                      <div className="ph-rv-ink-row">
                        <span className="ph-rv-ink-note">
                          圈选笔迹：拖动移动 · 换色 · 删除，不碰论文文字
                        </span>
                      </div>
                    </div>
                  )}
                  {inkMode === "style" && (
                    <InkToolPopover
                      label="改样式工具"
                      /* Both palettes: this brush restyles what is already on
                         the page, and what is already there may be a wash. */
                      palette={[...INK_COLORS, ...WATER_COLORS]}
                      quick={quickColors}
                      color={inkColor}
                      onColor={setInkColor}
                      width={inkWidth}
                      onWidth={setInkWidth}
                      tray={openTray}
                      onTray={toggleTray}
                      note="笔碰到字即改样式"
                    />
                  )}
                </div>
                <button
                  className="ph-rv-ink-btn"
                  title="撤销笔画"
                  disabled={!inkUndoable}
                  onClick={() => void undoInk()}
                >
                  <Icons.undo size={15} />
                </button>
                <button
                  className="ph-rv-ink-btn"
                  title="重做笔画"
                  disabled={!inkRedoable}
                  onClick={() => void redoInk()}
                >
                  <Icons.redo size={15} />
                </button>
                <div className="ph-rv-spacer" />
                <span className="ph-rv-hint">Ctrl+滚轮 / 双指缩放 · 拖动平移</span>
              </div>
              {docError ? (
                <div className="ph-rv-pdf-msg is-err">加载失败：{docError}</div>
              ) : (
                <PdfCanvas
                  doc={doc}
                  paperId={paperId}
                  /* Highlights are stored per rendition: a mark drawn on the
                     bilingual PDF has no meaning on the original's page 3. */
                  kind={pdfKind}
                  zoom={zoom}
                  onZoom={handleZoom}
                  onFitScale={handleFitScale}
                  jumpTo={jumpTo}
                  onJumped={onJumped}
                  onCurrentPage={onCurrentPage}
                  locked={canvasLocked}
                />
              )}
            </div>
          ) : status === "translating" ? (
            <div className="ph-rv-state ph-scroll">
              <div className="ph-rv-translating">
                <div className="ph-rv-trans-head">
                  <div className="ph-rv-spinner" />
                  <div className="ph-rv-trans-title">正在翻译并重排版面</div>
                  <div className="ph-rv-trans-sub">{vm?.title ?? ""}</div>
                </div>
                <div className="ph-rv-prog">
                  <span
                    className="ph-rv-prog-fill"
                    style={{ width: `${Math.max(0, Math.min(100, vm?.progress ?? 0))}%` }}
                  />
                </div>
                <div className="ph-rv-steps">
                  {TRANSLATE_STAGES.map((label, i) => {
                    const state = i < stage ? "is-done" : i === stage ? "is-active" : "is-todo";
                    return (
                      <div className="ph-rv-step" key={label}>
                        <span className={`ph-rv-dot ${state}`}>{i < stage ? "✓" : i + 1}</span>
                        <span className={`ph-rv-step-label ${state}`}>{label}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : status === "failed" ? (
            <div className="ph-rv-state ph-scroll">
              <div className="ph-rv-failed">
                <div className="ph-rv-err-icon">
                  <Icons.alert />
                </div>
                <div className="ph-rv-err-title">翻译失败</div>
                <div className="ph-rv-err-msg">{failMessage}</div>
                <div className="ph-rv-btn-row">
                  <button
                    className="ph-rv-btn-primary"
                    disabled={translate.isPending}
                    onClick={() => translate.mutate()}
                  >
                    重新翻译
                  </button>
                  <button className="ph-rv-btn-outline" onClick={() => setReadMode("original")}>
                    查看原文
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="ph-rv-state ph-scroll">
              <div className="ph-rv-untrans">
                <div className="ph-rv-mock">
                  <div className="ph-rv-mock-inner">
                    <div className="ph-rv-mock-title" />
                    <div className="ph-rv-mock-cols">
                      <div className="ph-rv-mock-col">
                        <div className="ph-rv-mock-line" />
                        <div className="ph-rv-mock-line w88" />
                        <div className="ph-rv-mock-line" />
                      </div>
                      <div className="ph-rv-mock-col">
                        <div className="ph-rv-mock-line" />
                        <div className="ph-rv-mock-line w80" />
                      </div>
                    </div>
                  </div>
                  <div className="ph-rv-mock-fade" />
                </div>
                <div className="ph-rv-title">{vm?.title ?? ""}</div>
                <div className="ph-rv-meta">
                  {dash(vm?.authors)} · {dash(vm?.pages)} 页 · 尚未翻译
                </div>
                <button
                  className="ph-rv-cta"
                  disabled={translate.isPending}
                  onClick={() => translate.mutate()}
                >
                  <span className="ph-rv-ico">
                    <Icons.spark />
                  </span>
                  翻译此篇 · 保留排版
                </button>
                <div className="ph-rv-note">分栏、公式、图表将全部原位保留</div>
                <button className="ph-rv-ghost" onClick={() => setReadMode("original")}>
                  先看原文
                </button>
              </div>
            </div>
          )}
        </div>
      </section>

      {aiOpen && (
        <AiPanel
          documentRef={documentRef}
          documentTitle={documentRef.title}
          onOpenSettings={() => openSettings("ai")}
        />
      )}
    </div>
  );
}

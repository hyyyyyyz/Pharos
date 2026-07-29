import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { api } from "../api/client";
import type { Paper, PdfKind } from "../api/types";
import { Icons } from "../design/icons";
import type { DocumentContext, DocumentRef } from "../lib/paperChat";
import {
  isLocalZoteroPaperId,
  localZotero,
} from "../lib/localZotero";
import { isZoteroPdfAttachment, parseZoteroItemId, zotero } from "../lib/zotero";
import {
  TRANSLATE_STAGES,
  dash,
  isJobActive,
  localToVM,
  stageIndex,
  toVM,
  zoteroDetailToVM,
} from "../lib/model";
import { isAiOpen, pdfTranslationEnabled, useSession, useUI, type ReadMode } from "../store";
import { AiPanel } from "./AiPanel";
import { OutlinePanel, type OutlineEntry } from "./OutlinePanel";
import { PdfCanvas } from "./PdfCanvas";
import "./ReadingView.css";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

const MIN_ZOOM = 0.4;
const MAX_ZOOM = 4;
const THUMB_WIDTH = 120;
const AI_CONTEXT_MAX_CHARS = 180_000;
/** Backend errors can be a whole stack trace; the panel shows the gist. */
const ERROR_MAX = 200;

interface LocalReadableAttachment {
  id: string;
  filename: string;
  available: boolean;
}

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

const textItem = (item: unknown): string => {
  if (item === null || typeof item !== "object" || !("str" in item)) return "";
  const value = (item as { str?: unknown }).str;
  return typeof value === "string" ? value : "";
};

async function extractPaperText(doc: PDFDocumentProxy): Promise<string> {
  const pages: string[] = [];
  let chars = 0;
  for (let pageNumber = 1; pageNumber <= doc.numPages; pageNumber += 1) {
    const page = await doc.getPage(pageNumber);
    const content = await page.getTextContent();
    const text = content.items.map(textItem).filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
    const section = `\n\n--- 第 ${pageNumber} 页 ---\n${text}`;
    const remaining = AI_CONTEXT_MAX_CHARS - chars;
    if (remaining <= 0) break;
    pages.push(section.slice(0, remaining));
    chars += Math.min(section.length, remaining);
    if (chars >= AI_CONTEXT_MAX_CHARS) break;
    if (pageNumber % 3 === 0) await new Promise((resolve) => setTimeout(resolve, 0));
  }
  return pages.join("").trim();
}

export function ReadingView({
  paperId,
  initialLocalAttachmentId,
}: {
  paperId: string;
  initialLocalAttachmentId?: string;
}): JSX.Element {
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
  const mirrorRef = parseZoteroItemId(paperId);
  const isLegacyLocal = isLocalZoteroPaperId(paperId);
  const isMirrorLocal = mirrorRef !== null;
  const isLocal = isLegacyLocal || isMirrorLocal;

  /* --------------------------------------------------------- the paper */
  const { data: paper } = useQuery({
    queryKey: ["paper", paperId],
    queryFn: () => api.getPaper(paperId),
    enabled: !isLocal,
    refetchInterval: (q) =>
      isJobActive((q.state.data as Paper | undefined)?.latest_job) ? 1500 : false,
  });
  const { data: localPaper } = useQuery({
    queryKey: ["zotero-local", "paper", paperId],
    queryFn: () => localZotero.get(paperId),
    enabled: isLegacyLocal,
  });
  const { data: mirrorDetail } = useQuery({
    queryKey: ["zotero-mirror", "item", mirrorRef?.sourceId, mirrorRef?.libraryId, mirrorRef?.itemKey],
    queryFn: () => zotero.item(mirrorRef!),
    enabled: isMirrorLocal,
  });
  const localAttachments = useMemo<LocalReadableAttachment[]>(() => {
    if (isLegacyLocal) {
      return (localPaper?.pdfAttachments ?? []).map((attachment) => ({
        id: attachment.id,
        filename: attachment.filename,
        available: attachment.available,
      }));
    }
    if (!isMirrorLocal) return [];
    return (mirrorDetail?.attachments ?? [])
      .filter(isZoteroPdfAttachment)
      .map((attachment) => ({
        id: attachment.publicId,
        filename: attachment.filename ?? `Zotero PDF ${attachment.key}`,
        available: attachment.available,
      }));
  }, [isLegacyLocal, isMirrorLocal, localPaper, mirrorDetail]);
  const [localAttachmentId, setLocalAttachmentId] = useState<string | null>(
    initialLocalAttachmentId ?? null,
  );
  useEffect(() => {
    if (!isLocal || (isLegacyLocal && !localPaper) || (isMirrorLocal && !mirrorDetail)) return;
    setLocalAttachmentId((current) => {
      if (current && localAttachments.some((attachment) => attachment.id === current)) {
        return current;
      }
      if (
        initialLocalAttachmentId &&
        localAttachments.some((attachment) => attachment.id === initialLocalAttachmentId)
      ) {
        return initialLocalAttachmentId;
      }
      if (isLegacyLocal && localPaper?.pdfAttachmentId) return localPaper.pdfAttachmentId;
      if (isMirrorLocal) return null;
      return (
        localAttachments.find((attachment) => attachment.available)?.id ??
        localAttachments[0]?.id ??
        null
      );
    });
  }, [initialLocalAttachmentId, isLegacyLocal, isLocal, isMirrorLocal, localAttachments, localPaper, mirrorDetail]);
  const localAttachment = useMemo(
    () => localAttachments.find((attachment) => attachment.id === localAttachmentId) ?? null,
    [localAttachmentId, localAttachments],
  );
  const vm = useMemo(
    () =>
      isLegacyLocal
        ? localPaper
          ? localToVM(localPaper)
          : null
        : isMirrorLocal
          ? mirrorDetail
            ? zoteroDetailToVM(mirrorDetail)
            : null
          : paper
            ? toVM(paper)
            : null,
    [isLegacyLocal, isMirrorLocal, localPaper, mirrorDetail, paper],
  );

  const translate = useMutation({
    mutationFn: () => {
      if (isLocal) throw new Error("请先把本地 PDF 导入 Pharos，再发起翻译。");
      return api.translate(paperId);
    },
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
  const effMode: ReadMode = isLocal ? "original" : pdfTx || isTranslated ? readMode : "original";

  /* The 中文/中英/原文 group. Off + untranslated leaves 原文 as the only member,
     and a one-button segmented control is noise pretending to be a choice — so
     the group goes entirely, per "the apparatus must genuinely disappear". */
  const showModes = !isLocal && (pdfTx || isTranslated);

  // 原文 always wins: it is the only path to the source PDF before/after a
  // failed translation.
  const showPdf = isLocal
    ? localAttachment?.available === true
    : effMode === "original" || isTranslated;

  /* Which PDF to show, as a plain string. Deliberately NOT the PdfSource object:
     this memo recomputes whenever `vm.job` gets a new identity (every /papers
     refetch), and returning a fresh object each time would re-run the loader
     effect below and tear down a perfectly good document. A string compares
     equal, so the reload only happens when the file actually changes. */
  const pdfKind = useMemo<PdfKind | null>(() => {
    if (!showPdf) return null;
    if (isLocal) return "original";
    let kind: PdfKind = effMode === "zh" ? "mono" : effMode === "bilingual" ? "dual" : "original";
    // The backend may produce a mono-only result; don't request a 404.
    if (kind === "dual" && vm?.job && !vm.job.has_dual) kind = "mono";
    return kind;
  }, [showPdf, isLocal, effMode, vm?.job]);

  const localPdfQuery = useQuery({
    queryKey: ["zotero-local", isMirrorLocal ? "mirror-pdf" : "legacy-pdf", localAttachment?.id],
    queryFn: () =>
      isMirrorLocal
        ? zotero.attachmentSource(localAttachment!.id)
        : localZotero.pdfSource(localAttachment!.id),
    enabled: isLocal && localAttachment?.available === true,
  });
  const localPdfUrl = localPdfQuery.data?.url ?? null;

  /* -------------------------------------------------------- the pdf.js doc */
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [docError, setDocError] = useState<string | null>(null);
  const effectiveDocError =
    docError ?? (localPdfQuery.isError ? errMsg(localPdfQuery.error) : null);

  useEffect(() => {
    setDoc(null);
    setDocError(null);
    if (!pdfKind || (isLocal && !localPdfUrl)) return;
    let cancelled = false;
    // pdfSource(), not pdfUrl(): /papers/{id}/pdf/{kind} requires a bearer token
    // like every other endpoint, and a bare URL cannot carry one. pdf.js issues
    // the requests itself, so httpHeaders rides along on the initial fetch and
    // on every range request. Built here rather than in the memo so the token
    // read is as late as possible.
    const task = pdfjs.getDocument(
      isLocal ? { url: localPdfUrl!, httpHeaders: {} } : api.pdfSource(paperId, pdfKind),
    );
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
  }, [pdfKind, paperId, isLocal, localPdfUrl]);

  /* ------------------------------------------------------------ zoom / fit */
  const [zoom, setZoom] = useState(1);
  const [fitScale, setFitScale] = useState(1);
  const [fitMode, setFitMode] = useState(true);
  const fitModeRef = useRef(fitMode);
  fitModeRef.current = fitMode;

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
  const displayFilename = isLocal ? (localAttachment?.filename ?? vm?.file ?? "") : (vm?.file ?? "");
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
  const documentRef = useMemo<DocumentRef>(() => {
    const title = vm?.title?.trim() || displayFilename || "未命名论文";
    if (isMirrorLocal && mirrorRef) {
      return {
        key: [
          "zotero",
          mirrorRef.sourceId,
          mirrorRef.libraryId,
          mirrorRef.itemKey,
          localAttachmentId ?? "entry",
        ].map(encodeURIComponent).join(":"),
        kind: "zotero",
        title,
        sourceId: mirrorRef.sourceId,
        libraryId: mirrorRef.libraryId,
        itemKey: mirrorRef.itemKey,
        attachmentId: localAttachmentId,
      };
    }
    if (isLegacyLocal) {
      return {
        key: ["zotero-local", paperId, localAttachmentId ?? "entry"].map(encodeURIComponent).join(":"),
        kind: "zotero",
        title,
        libraryId: localPaper?.libraryId ?? null,
        itemKey: localPaper?.itemKey ?? null,
        attachmentId: localAttachmentId,
      };
    }
    return {
      key: `paper:${encodeURIComponent(paperId)}`,
      kind: "paper",
      title,
      paperId,
    };
  }, [
    displayFilename,
    isLegacyLocal,
    isMirrorLocal,
    localAttachmentId,
    localPaper?.itemKey,
    localPaper?.libraryId,
    mirrorRef,
    paperId,
    vm?.title,
  ]);

  const getAiContext = useCallback(async (): Promise<DocumentContext> => {
    let contextDoc = doc;
    let ownTask: ReturnType<typeof pdfjs.getDocument> | null = null;
    if (!isLocal && (!contextDoc || pdfKind !== "original")) {
      ownTask = pdfjs.getDocument(api.pdfSource(paperId, "original"));
      contextDoc = await ownTask.promise;
    }
    if (!contextDoc) throw new Error("PDF 尚未加载完成，暂时无法建立 AI 对话上下文。");
    try {
      return {
        title: vm?.title ?? displayFilename,
        authors: vm?.authors.join(", ") ?? "",
        abstractText: vm?.abstract ?? "",
        fullText: await extractPaperText(contextDoc),
        currentPage,
        pageCount: contextDoc.numPages,
      };
    } finally {
      if (ownTask) await ownTask.destroy().catch(() => undefined);
    }
  }, [currentPage, displayFilename, doc, isLocal, paperId, pdfKind, vm?.abstract, vm?.authors, vm?.title]);

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
          {isLocal && localAttachments.length > 1 && (
            <label className="ph-rv-local-attachment">
              <span>附件</span>
              <select
                value={localAttachmentId ?? ""}
                onChange={(event) => setLocalAttachmentId(event.target.value)}
                aria-label="选择本地 Zotero PDF"
              >
                {localAttachments.map((attachment) => (
                  <option key={attachment.id} value={attachment.id}>
                    {attachment.filename}{attachment.available ? "" : "（未下载）"}
                  </option>
                ))}
              </select>
            </label>
          )}
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
                <div className="ph-rv-spacer" />
                <span className="ph-rv-hint">Ctrl+滚轮缩放 · 拖动平移</span>
              </div>
              {effectiveDocError ? (
                <div className="ph-rv-pdf-msg is-err">加载失败：{effectiveDocError}</div>
              ) : (
                <PdfCanvas
                  doc={doc}
                  paperId={paperId}
                  /* Highlights are stored per rendition: a mark drawn on the
                     bilingual PDF has no meaning on the original's page 3. */
                  kind={isLocal ? null : pdfKind}
                  zoom={zoom}
                  onZoom={handleZoom}
                  onFitScale={handleFitScale}
                  jumpTo={jumpTo}
                  onJumped={onJumped}
                  onCurrentPage={onCurrentPage}
                />
              )}
            </div>
          ) : isLocal ? (
            <div className="ph-rv-state ph-scroll">
              <div className="ph-rv-failed">
                <div className="ph-rv-err-icon">
                  <Icons.file />
                </div>
                <div className="ph-rv-err-title">本地 PDF 暂不可用</div>
                <div className="ph-rv-err-msg">
                  {localPdfQuery.isError
                    ? String(localPdfQuery.error)
                    : localAttachment
                      ? `“${localAttachment.filename}”尚未下载到这台 Mac。请在 Zotero 中下载或打开附件，然后返回文库重新同步。缓存中的书目信息不会被删除。`
                      : isMirrorLocal && initialLocalAttachmentId
                        ? "所选 PDF 附件已移动或删除。请返回文库重新同步，再从条目下选择具体附件。"
                        : isMirrorLocal && localAttachments.length > 0
                          ? "请返回文库，展开 Zotero 条目并双击具体 PDF 附件。"
                          : "这个 Zotero 条目没有 PDF 附件。书目信息仍会保留在本地文库中。"}
                </div>
              </div>
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
          contextReady={!isLocal || Boolean(doc)}
          getContext={getAiContext}
          onOpenSettings={() => openSettings("ai")}
        />
      )}
    </div>
  );
}

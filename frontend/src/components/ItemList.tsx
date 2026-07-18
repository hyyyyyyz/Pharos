/**
 * 文库 centre pane — the item list.
 *
 * Ported from the design prototype (Pharos.dc.html lines 96-152 for markup,
 * renderVals lines 600-604 for the column/row styling, line 566 for the
 * status pill). Sorting/filtering mirrors visiblePapers() (lines 506-514),
 * narrowed to the metadata the backend actually supplies.
 */
import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { Icons } from "../design/icons";
import { dash, isJobActive, statusMeta, toVM, type PaperVM } from "../lib/model";
import { useUI, type SortCol } from "../store";
import "./ItemList.css";

const COLS: { k: SortCol; label: string; cls: string }[] = [
  { k: "title", label: "标题", cls: "ph-il-c-title" },
  { k: "authors", label: "作者", cls: "ph-il-c-authors" },
  { k: "year", label: "年份", cls: "ph-il-c-year" },
  { k: "pages", label: "页数", cls: "ph-il-c-pages" },
  { k: "status", label: "状态", cls: "ph-il-c-status" },
];

/** The toolbar magnifier. The prototype does NOT reuse its `I.search` glyph
 *  here — it inlines a smaller one (circle r=4.6 at 8.5,8.5; handle 12.5→15.5)
 *  at 14px. Kept verbatim so the toolbar is pixel-identical. */
function SearchGlyph(): JSX.Element {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12.5 12.5l3 3" />
      <circle cx={8.5} cy={8.5} r={4.6} />
    </svg>
  );
}

/** Sort key for a column. `authors`/`year` are always null on real papers, so
 *  those comparisons tie and Array#sort keeps the list stable. */
function sortKey(p: PaperVM, col: SortCol): string | number {
  switch (col) {
    case "title":
      return p.title;
    case "authors":
      return p.authors ?? "";
    case "pages":
      return p.pages ?? 0;
    case "status":
      return p.status;
    default:
      return p.year ?? 0;
  }
}

export function ItemList(): JSX.Element {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const query = useUI((s) => s.query);
  const setQuery = useUI((s) => s.setQuery);
  const arxivInput = useUI((s) => s.arxivInput);
  const setArxivInput = useUI((s) => s.setArxivInput);
  const selectedCol = useUI((s) => s.selectedCol);
  const selectedTags = useUI((s) => s.selectedTags);
  const sortCol = useUI((s) => s.sortCol);
  const sortDir = useUI((s) => s.sortDir);
  const setSort = useUI((s) => s.setSort);
  const selectedIds = useUI((s) => s.selectedIds);
  const selectedPaperId = useUI((s) => s.selectedPaperId);
  const selectRow = useUI((s) => s.selectRow);
  const openPaper = useUI((s) => s.openPaper);
  const openSettings = useUI((s) => s.openSettings);

  const papersQuery = useQuery({
    queryKey: ["papers"],
    queryFn: api.listPapers,
    // Keep progress bars moving while any translation job is running.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((p) => isJobActive(p.latest_job)) ? 1500 : false,
  });

  const upload = useMutation({
    mutationFn: (f: File) => api.upload(f),
    onSuccess: (paper) => {
      void qc.invalidateQueries({ queryKey: ["papers"] });
      selectRow(paper.id, [paper.id], { meta: false, shift: false });
    },
  });

  const papers = useMemo(() => (papersQuery.data ?? []).map(toVM), [papersQuery.data]);

  const visible = useMemo(() => {
    // The backend has no collections yet; only 回收站 is a real (always empty) view.
    let list = selectedCol === "trash" ? [] : papers;
    const q = query.trim().toLowerCase();
    if (q) list = list.filter((p) => p.title.toLowerCase().includes(q));
    if (selectedTags.length)
      list = list.filter((p) => selectedTags.every((t) => p.tags.includes(t)));
    const dir = sortDir === "asc" ? 1 : -1;
    return [...list].sort((a, b) => {
      const x = sortKey(a, sortCol);
      const y = sortKey(b, sortCol);
      return x < y ? -1 * dir : x > y ? dir : 0;
    });
  }, [papers, selectedCol, query, selectedTags, sortCol, sortDir]);

  const order = useMemo(() => visible.map((p) => p.id), [visible]);

  const sendFile = (f: File | null | undefined) => {
    if (!f || !f.name.toLowerCase().endsWith(".pdf")) return;
    setNote(null);
    upload.mutate(f);
  };

  const importArxiv = () => {
    if (!arxivInput.trim()) return;
    // TODO: swap for `api.importArxiv(arxivInput)` once the endpoint exists.
    setNote("arXiv 导入尚未接入后端");
  };

  const arrow = sortDir === "asc" ? " ↑" : " ↓";
  const uploadError = upload.error instanceof Error ? upload.error.message : null;

  const showFirstUse = !papersQuery.isPending && !papersQuery.isError && papers.length === 0;
  const showEmptyList =
    !papersQuery.isPending && !papersQuery.isError && papers.length > 0 && visible.length === 0;
  const hasRows = visible.length > 0;

  return (
    <section className="ph-il">
      <div className="ph-il-bar">
        <div className="ph-il-searchwrap">
          <span className="ph-il-searchicon">
            <SearchGlyph />
          </span>
          <input
            className="ph-il-search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索文库…"
          />
        </div>
        <div className="ph-il-spacer" />
        {selectedIds.length > 1 && <span className="ph-il-selcount">已选 {selectedIds.length}</span>}
        <div className="ph-il-actions">
          <input
            className="ph-il-arxiv"
            value={arxivInput}
            onChange={(e) => setArxivInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") importArxiv();
            }}
            placeholder="arXiv 链接 / ID"
          />
          <button className="ph-il-btn-ghost" title="导入 arXiv" onClick={importArxiv}>
            导入
          </button>
          <button
            className="ph-il-btn-primary"
            title="上传 PDF"
            onClick={() => fileRef.current?.click()}
          >
            <span>
              <Icons.plus />
            </span>
            PDF
          </button>
        </div>
        <input
          ref={fileRef}
          className="ph-il-file"
          type="file"
          accept=".pdf"
          onChange={(e) => {
            sendFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>

      {note && <div className="ph-il-note">{note}</div>}
      {uploadError && <div className="ph-il-note is-error">上传失败：{uploadError}</div>}

      <div
        className="ph-il-body ph-scroll"
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
        }}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          sendFile(e.dataTransfer.files[0]);
        }}
      >
        {papersQuery.isPending && (
          <div className="ph-il-empty">
            <div className="ph-il-empty-text">载入中…</div>
          </div>
        )}

        {papersQuery.isError && (
          <div className="ph-il-empty">
            <div className="ph-il-empty-text is-error">
              无法连接到后端服务。
              <br />
              请确认 Pharos 服务已启动后重试。
            </div>
          </div>
        )}

        {showFirstUse && (
          <div className="ph-il-firstuse">
            <div className="ph-il-firstuse-inner">
              <div className="ph-il-firstuse-mark">
                <Icons.brand size={28} sw={1.2} />
              </div>
              <div className="ph-il-firstuse-title">欢迎使用 Pharos</div>
              <div className="ph-il-firstuse-desc">
                把英文论文翻译成中文，完整保留原始排版。
                <br />
                拖入 PDF、粘贴 arXiv 链接，或连接 Zotero 开始。
              </div>
              <div className="ph-il-firstuse-cta">
                <button className="ph-il-cta-primary" onClick={() => fileRef.current?.click()}>
                  上传 PDF
                </button>
                <button className="ph-il-cta-ghost" onClick={() => openSettings("account")}>
                  连接 Zotero
                </button>
              </div>
            </div>
          </div>
        )}

        {showEmptyList && (
          <div className="ph-il-empty">
            <div className="ph-il-empty-text">
              {selectedCol === "trash" ? "回收站是空的" : "该分类下暂无条目"}
            </div>
          </div>
        )}

        {hasRows && (
          <>
            <div className="ph-il-head">
              {COLS.map((c) => (
                <span key={c.k} className={c.cls} onClick={() => setSort(c.k)}>
                  {c.label}
                  <span style={{ opacity: sortCol === c.k ? 1 : 0 }}>{arrow}</span>
                </span>
              ))}
            </div>
            {visible.map((p) => {
              const sel = selectedIds.includes(p.id);
              const meta = statusMeta(p.status);
              return (
                <div
                  key={p.id}
                  className={
                    "ph-il-row" +
                    (sel ? " is-selected" : "") +
                    (p.id === selectedPaperId ? " is-primary" : "")
                  }
                  onClick={(e) =>
                    selectRow(p.id, order, { meta: e.metaKey || e.ctrlKey, shift: e.shiftKey })
                  }
                  onDoubleClick={() => openPaper(p.id)}
                >
                  <span className="ph-il-c-title">
                    {p.isZotero && (
                      <span className="ph-il-zotero" title="来自 Zotero">
                        <Icons.cloud size={13} />
                      </span>
                    )}
                    <span className="ph-il-title">{p.title}</span>
                  </span>
                  <span className="ph-il-c-authors">{dash(p.authors)}</span>
                  <span className="ph-il-c-year">{dash(p.year)}</span>
                  <span className="ph-il-c-pages">{dash(p.pages)}</span>
                  <span className="ph-il-c-status">
                    <span className={`ph-il-pill ${meta.cls}`}>{meta.label}</span>
                    {p.status === "translating" && (
                      <span className="ph-il-prog">
                        <span style={{ width: `${p.progress}%` }} />
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
          </>
        )}

        {dragOver && <div className="ph-il-drop">松开以导入 PDF</div>}
      </div>
    </section>
  );
}

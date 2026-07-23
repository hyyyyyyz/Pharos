/**
 * 文库 centre pane — the item list.
 *
 * Ported from the design prototype (Pharos.dc.html lines 96-152 for markup,
 * renderVals lines 600-604 for the column/row styling, line 566 for the
 * status pill).
 *
 * The search box is no longer a client-side title filter: it calls
 * `GET /api/search`, debounced, and renders the server's ranked hits with the
 * snippet it returns. Clearing the box returns to the plain owned-papers list.
 *
 * Two views are still filtered client-side, and both want a server-side filter
 * instead (see the handover report): a folder intersects the library against
 * `GET /api/collections/{id}/papers`, and 未分类 subtracts the union of every
 * folder's ids. Neither can be expressed as a query parameter on `GET /papers`
 * today. Tag chips are shown in the rail but do not filter for the same reason
 * — there is no tag→papers endpoint at all.
 */
import { Fragment, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { CollectionNode, SearchField, SearchHit } from "../api/types";
import { Icons } from "../design/icons";
import { compactAuthors, dash, isJobActive, statusMeta, toVM, type PaperVM } from "../lib/model";
import { pdfTranslationEnabled, useSession, useUI, type SortCol } from "../store";
import { PAPER_DRAG_MIME } from "./CollectionTree";
import "./ItemList.css";

const COLS: { k: SortCol; label: string; cls: string }[] = [
  { k: "title", label: "标题", cls: "ph-il-c-title" },
  { k: "authors", label: "作者", cls: "ph-il-c-authors" },
  { k: "year", label: "年份", cls: "ph-il-c-year" },
  { k: "pages", label: "页数", cls: "ph-il-c-pages" },
  { k: "status", label: "状态", cls: "ph-il-c-status" },
];

/** 状态 reports the translation pipeline, so it goes with the pipeline. The row
 *  is flexbox and `.ph-il-c-title` is `flex: 1`, so the title reclaims the 92px
 *  without a CSS change. */
const COLS_NO_TX = COLS.filter((c) => c.k !== "status");

/** How long the box stays quiet before it asks the server. Long enough that
 *  ordinary typing produces one request rather than one per letter, short
 *  enough to still feel like it is keeping up. */
const SEARCH_DEBOUNCE_MS = 250;

/** More than the eye can use at once, but enough that the count in the footer
 *  is usually the whole truth rather than a first page. */
const SEARCH_LIMIT = 50;

/** Which column matched, in the reader's language. */
const FIELD_LABEL: Record<SearchField, string> = {
  title: "标题",
  abstract: "摘要",
  authors: "作者",
  full_text: "全文",
};

/** The built-in rail entries — anything else in `selectedCol` is a folder id. */
const BUILTIN_COLS = new Set(["lib", "uncat", "trash"]);

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

/** The five entities `html.escape(quote=True)` produces, and nothing else. */
const ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#x27;": "'",
};

/** Undo the backend's escaping. One pass, deliberately: a second pass over the
 *  output would turn the literal text `&amp;lt;` into `<`, re-creating the
 *  markup the escaping existed to neutralise. */
function decodeEntities(s: string): string {
  return s.replace(/&(?:amp|lt|gt|quot|#x27);/g, (m) => ENTITIES[m] ?? m);
}

/**
 * Render a snippet, emphasising the matched terms.
 *
 * The backend documents `snippet` as safe to drop into innerHTML, and it is —
 * it escapes the text and only then swaps control-character sentinels for
 * `<mark>`. This still parses the two known tags out and returns text nodes
 * instead. `dangerouslySetInnerHTML` here would mean every future change to the
 * snippet builder is one escaping slip away from stored XSS delivered through
 * search results, on text extracted from arbitrary uploaded PDFs. Costing a few
 * lines to make that class of bug unreachable from the client is worth it.
 */
function renderSnippet(snippet: string): JSX.Element[] {
  return snippet.split(/(<mark>|<\/mark>)/).reduce<{ out: JSX.Element[]; on: boolean }>(
    (acc, part, i) => {
      if (part === "<mark>") return { ...acc, on: true };
      if (part === "</mark>") return { ...acc, on: false };
      if (part === "") return acc;
      const text = decodeEntities(part);
      acc.out.push(
        acc.on ? (
          <mark key={i} className="ph-il-mark">
            {text}
          </mark>
        ) : (
          <span key={i}>{text}</span>
        ),
      );
      return acc;
    },
    { out: [], on: false },
  ).out;
}

/** Sort key for a column, or null when this paper has no value for it. Null is
 *  kept distinct from `""`/`0` so the comparator can sink those rows to the
 *  bottom instead of letting them masquerade as the smallest real value. */
function sortKey(p: PaperVM, col: SortCol): string | number | null {
  switch (col) {
    case "title":
      return p.title;
    case "authors":
      // Sorts by first-author surname, matching what the column displays.
      return compactAuthors(p.authors);
    case "pages":
      return p.pages;
    case "status":
      return p.status;
    default:
      return p.year;
  }
}

/** Compare two rows on the active column. Rows missing the value sort last in
 *  BOTH directions — flipping the arrow must not promote “—” above real data. */
function compareBy(a: Row, b: Row, col: SortCol, dir: 1 | -1): number {
  // A hit whose paper is not in the library listing has no sortable metadata at
  // all; it sinks, for the same reason a missing single field does.
  if (a.vm === null || b.vm === null) return a.vm === b.vm ? 0 : a.vm === null ? 1 : -1;
  const x = sortKey(a.vm, col);
  const y = sortKey(b.vm, col);
  if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
  return x < y ? -dir : x > y ? dir : 0;
}

/**
 * One rendered line.
 *
 * `vm` is null only for a search hit whose paper is absent from the library
 * listing. That is a real, if rare, state — the two queries are fetched
 * separately and can disagree for a moment — and it renders as the title the
 * search returned plus “—” everywhere else, rather than being dropped. Silently
 * discarding a hit would tell the user their paper does not match when it does.
 */
interface Row {
  id: string;
  vm: PaperVM | null;
  hit: SearchHit | null;
  /** Title to display: the paper's when known, else the one search returned. */
  title: string;
}

/** Every folder id in the tree, flattened. */
function allCollectionIds(nodes: CollectionNode[]): string[] {
  return nodes.flatMap((n) => [n.id, ...allCollectionIds(n.children)]);
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
  const sortCol = useUI((s) => s.sortCol);
  const sortDir = useUI((s) => s.sortDir);
  const setSort = useUI((s) => s.setSort);
  const selectedIds = useUI((s) => s.selectedIds);
  const selectedPaperId = useUI((s) => s.selectedPaperId);
  const selectRow = useUI((s) => s.selectRow);
  const openPaper = useUI((s) => s.openPaper);
  const openSettings = useUI((s) => s.openSettings);
  const pdfTx = useSession(pdfTranslationEnabled);

  const cols = pdfTx ? COLS : COLS_NO_TX;

  /**
   * `sortCol` is persisted, so it can already be `"status"` when the setting is
   * switched off — the user clicked that header while translation was on. The
   * header would vanish while `compareBy` kept ordering by an invisible key and
   * the arrow had nothing to sit next to, so fall back to a column that is
   * actually on screen. Not written back to the store: turning translation on
   * again should restore the sort the user chose.
   */
  const effSortCol: SortCol = !pdfTx && sortCol === "status" ? "year" : sortCol;

  /* ------------------------------------------------------------- searching */

  const [debounced, setDebounced] = useState("");
  /**
   * Has the user chosen a sort *since this search started*?
   *
   * Search results arrive ranked, and the stored default sort is year-desc, so
   * applying it unconditionally would throw away the ranking on every query.
   * Ranked order holds until the user clicks a column header, and a new search
   * hands ranking back. Local state, because "the sort was chosen deliberately"
   * is a fact about this pane's session, not about the app.
   */
  const [sortTouched, setSortTouched] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    setSortTouched(false);
  }, [debounced]);

  const searching = debounced !== "";

  const searchQuery = useQuery({
    queryKey: ["search", debounced],
    // The signal is React Query's: a superseded request is aborted rather than
    // left to land late and overwrite fresher results.
    queryFn: ({ signal }) => api.search(debounced, { limit: SEARCH_LIMIT, signal }),
    enabled: searching,
    // Hold the previous hits while the next query is in flight, so the list
    // does not blink through an empty state on every keystroke.
    placeholderData: (prev) => prev,
  });

  /* ---------------------------------------------------------------- library */

  const papersQuery = useQuery({
    queryKey: ["papers"],
    queryFn: api.listPapers,
    // Keep progress bars moving while any translation job is running.
    refetchInterval: (q) =>
      (q.state.data ?? []).some((p) => isJobActive(p.latest_job)) ? 1500 : false,
  });

  const trashQuery = useQuery({
    queryKey: ["papers", "trash"],
    queryFn: api.listTrash,
    enabled: selectedCol === "trash",
  });

  const collectionsQuery = useQuery({ queryKey: ["collections"], queryFn: api.collections.list });

  const inFolder = selectedCol !== "" && !BUILTIN_COLS.has(selectedCol);

  const folderQuery = useQuery({
    queryKey: ["collection", selectedCol, "papers"],
    queryFn: () => api.collections.paperIds(selectedCol),
    enabled: inFolder,
  });

  /**
   * 未分类 = every paper minus the union of every folder's contents.
   *
   * Computed from one id-list request per folder because no endpoint answers
   * "papers in no collection" — the rail's badge comes from the server, but the
   * list behind it does not exist as a query. The requests only fire while this
   * view is open, and they share cache keys with folder browsing, so the cost
   * is bounded by the folder count and usually already paid. A
   * `?uncategorised=true` on `GET /papers` would replace all of this.
   */
  const folderIds = useMemo(
    () => allCollectionIds(collectionsQuery.data?.collections ?? []),
    [collectionsQuery.data],
  );

  const membershipQueries = useQueries({
    queries: folderIds.map((id) => ({
      queryKey: ["collection", id, "papers"],
      queryFn: () => api.collections.paperIds(id),
      enabled: selectedCol === "uncat",
    })),
  });

  // `useQueries` hands back a fresh array object every render, so memoising on
  // it would recompute — and hand a new Set identity to every dependent memo —
  // on each one. `dataUpdatedAt` is a number that changes only when a result
  // actually changes, which is the real dependency.
  const membershipStamp = membershipQueries.map((q) => q.dataUpdatedAt).join(",");

  const categorised = useMemo(() => {
    const set = new Set<string>();
    for (const q of membershipQueries) for (const id of q.data?.paper_ids ?? []) set.add(id);
    return set;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see membershipStamp
  }, [membershipStamp]);

  const uncatPending = selectedCol === "uncat" && membershipQueries.some((q) => q.isPending);

  /* ------------------------------------------------------------------ rows */

  const upload = useMutation({
    mutationFn: (f: File) => api.upload(f),
    onSuccess: (paper) => {
      void qc.invalidateQueries({ queryKey: ["papers"] });
      void qc.invalidateQueries({ queryKey: ["collections"] });
      selectRow(paper.id, [paper.id], { meta: false, shift: false });
    },
  });

  const papers = useMemo(() => (papersQuery.data ?? []).map(toVM), [papersQuery.data]);
  const trashPapers = useMemo(() => (trashQuery.data ?? []).map(toVM), [trashQuery.data]);

  const byId = useMemo(() => new Map(papers.map((p) => [p.id, p])), [papers]);

  const rows = useMemo<Row[]>(() => {
    // Search replaces the browse view entirely: it spans the whole library, so
    // intersecting it with the selected folder would quietly answer a different
    // question than the one the box asks.
    if (searching) {
      const hits = searchQuery.data?.hits ?? [];
      return hits.map((hit) => {
        const vm = byId.get(hit.paper_id) ?? null;
        return { id: hit.paper_id, vm, hit, title: vm?.title ?? hit.title };
      });
    }

    let list: PaperVM[];
    if (selectedCol === "trash") list = trashPapers;
    else if (selectedCol === "uncat") list = papers.filter((p) => !categorised.has(p.id));
    else if (inFolder) {
      const ids = new Set(folderQuery.data?.paper_ids ?? []);
      list = papers.filter((p) => ids.has(p.id));
    } else list = papers;

    return list.map((vm) => ({ id: vm.id, vm, hit: null, title: vm.title }));
  }, [
    searching,
    searchQuery.data,
    byId,
    selectedCol,
    inFolder,
    folderQuery.data,
    papers,
    trashPapers,
    categorised,
  ]);

  const visible = useMemo(() => {
    // Ranked order is the server's answer to "what matches best"; only an
    // explicit header click overrides it.
    if (searching && !sortTouched) return rows;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => compareBy(a, b, effSortCol, dir));
  }, [rows, searching, sortTouched, effSortCol, sortDir]);

  const order = useMemo(() => visible.map((r) => r.id), [visible]);

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

  /** Begin dragging papers onto a folder in the rail.
   *
   * A drag that starts on an unselected row selects it first, so the payload is
   * always exactly what is highlighted. A drag that starts on a selected row
   * leaves the selection alone and carries all of it — which is the only reason
   * ⌘/⇧ multi-select and drag-to-file can coexist. `dragstart` needs the button
   * held and the pointer moved, so it never pre-empts a plain click. */
  const onRowDragStart = (e: DragEvent, id: string) => {
    let ids = selectedIds;
    if (!ids.includes(id)) {
      selectRow(id, order, { meta: false, shift: false });
      ids = [id];
    }
    e.dataTransfer.setData(PAPER_DRAG_MIME, JSON.stringify(ids));
    e.dataTransfer.effectAllowed = "copy";
  };

  const arrow = sortDir === "asc" ? " ↑" : " ↓";
  const uploadError = upload.error instanceof Error ? upload.error.message : null;
  const searchError = searchQuery.isError;

  const listPending =
    (searching && searchQuery.isPending) ||
    (!searching && papersQuery.isPending) ||
    (selectedCol === "trash" && trashQuery.isPending) ||
    (inFolder && folderQuery.isPending) ||
    uncatPending;

  const showFirstUse =
    !searching &&
    selectedCol === "lib" &&
    !papersQuery.isPending &&
    !papersQuery.isError &&
    papers.length === 0;

  const showEmptyList = !listPending && !papersQuery.isError && !showFirstUse && visible.length === 0;

  const emptyText = searching
    ? searchError
      ? "搜索失败，请重试"
      : `没有找到与“${debounced}”匹配的内容`
    : selectedCol === "trash"
      ? "回收站是空的"
      : "该分类下暂无条目";

  const hasRows = visible.length > 0;

  const total = searchQuery.data?.total ?? 0;
  const truncated = searching && total > visible.length;

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
            onKeyDown={(e) => {
              // Escape clears, which is the fastest way back to the browse view.
              if (e.key === "Escape") setQuery("");
            }}
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
      {/* The backend reports which engine answered. "like" means FTS5 is not
          available on this deployment and ranking is crude — worth saying once,
          quietly, rather than leaving the user to wonder why. */}
      {searching && searchQuery.data?.engine === "like" && (
        <div className="ph-il-note">当前部署未启用全文索引，搜索结果按简单匹配排序</div>
      )}

      <div
        className="ph-il-body ph-scroll"
        onDragOver={(e) => {
          // Only a file drag is an upload. Without this, dragging a row over the
          // list would raise the "松开以导入 PDF" overlay.
          if (!e.dataTransfer.types.includes("Files")) return;
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragOver(false);
        }}
        onDrop={(e) => {
          if (!e.dataTransfer.types.includes("Files")) return;
          e.preventDefault();
          setDragOver(false);
          sendFile(e.dataTransfer.files[0]);
        }}
      >
        {listPending && (
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
            <div className={searchError ? "ph-il-empty-text is-error" : "ph-il-empty-text"}>
              {emptyText}
            </div>
          </div>
        )}

        {hasRows && (
          <>
            <div className="ph-il-head">
              {cols.map((c) => (
                <span
                  key={c.k}
                  className={c.cls}
                  onClick={() => {
                    setSortTouched(true);
                    setSort(c.k);
                  }}
                >
                  {c.label}
                  <span
                    style={{ opacity: effSortCol === c.k && (!searching || sortTouched) ? 1 : 0 }}
                  >
                    {arrow}
                  </span>
                </span>
              ))}
            </div>
            {visible.map((r) => {
              const sel = selectedIds.includes(r.id);
              const meta = r.vm === null || !pdfTx ? null : statusMeta(r.vm.status);
              const row = (
                <div
                  className={
                    "ph-il-row" +
                    (sel ? " is-selected" : "") +
                    (r.id === selectedPaperId ? " is-primary" : "")
                  }
                  draggable
                  onDragStart={(e) => onRowDragStart(e, r.id)}
                  onClick={(e) =>
                    selectRow(r.id, order, { meta: e.metaKey || e.ctrlKey, shift: e.shiftKey })
                  }
                  onDoubleClick={() => openPaper(r.id)}
                >
                  <span className="ph-il-c-title">
                    {r.vm?.isZotero === true && (
                      <span className="ph-il-zotero" title="来自 Zotero">
                        <Icons.cloud size={13} />
                      </span>
                    )}
                    <span className="ph-il-title">{r.title}</span>
                  </span>
                  <span
                    className="ph-il-c-authors"
                    title={
                      r.vm !== null && r.vm.authors.length > 0 ? r.vm.authors.join(", ") : undefined
                    }
                  >
                    {r.vm === null ? "—" : dash(compactAuthors(r.vm.authors))}
                  </span>
                  <span className="ph-il-c-year">{dash(r.vm?.year ?? null)}</span>
                  <span className="ph-il-c-pages">{dash(r.vm?.pages ?? null)}</span>
                  {pdfTx && (
                    <span className="ph-il-c-status">
                      {meta !== null && (
                        <span className={`ph-il-pill ${meta.cls}`}>{meta.label}</span>
                      )}
                      {r.vm?.status === "translating" && (
                        <span className="ph-il-prog">
                          <span style={{ width: `${r.vm.progress}%` }} />
                        </span>
                      )}
                    </span>
                  )}
                </div>
              );

              if (r.hit === null) return <Fragment key={r.id}>{row}</Fragment>;

              // A hit is the row plus a second line. The wrapper carries the
              // rule between results so the 28px row itself is untouched.
              return (
                <div key={r.id} className="ph-il-hit">
                  {row}
                  <div className="ph-il-snip">
                    <span className="ph-il-snip-field">{FIELD_LABEL[r.hit.field]}</span>
                    <span className="ph-il-snip-text">{renderSnippet(r.hit.snippet)}</span>
                  </div>
                </div>
              );
            })}
            {truncated && (
              <div className="ph-il-more">
                共 {total} 条结果，已显示前 {visible.length} 条
              </div>
            )}
          </>
        )}

        {dragOver && <div className="ph-il-drop">松开以导入 PDF</div>}
      </div>
    </section>
  );
}

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { authHeaders } from "../api/client";
import { Icons } from "../design/icons";
import { useUI } from "../store";
import "./HighlightLayer.css";

/**
 * Base URL for the API.
 *
 * Duplicated from `api/client.ts` rather than imported because that module does
 * not export it and is not part of this slice. It is the one thing here that
 * can drift: fold these calls into `api` (as `api.highlights.*`) the next time
 * that file is opened, and delete this constant.
 */
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

/** Must not exceed `annotate.MAX_RECTS`, or the POST is rejected outright. */
const MAX_RECTS = 400;

/**
 * Two client rects join into one when the horizontal gap between them is at
 * most this fraction of the line height.
 *
 * `getClientRects()` returns one rect per *text run*, and pdf.js emits a run per
 * change of font or position — so a single highlighted line routinely arrives as
 * a dozen tiles with hairline seams between them. Merging is therefore both
 * cosmetic (one continuous bar, as a highlighter draws) and structural: it is
 * what keeps a long selection under MAX_RECTS.
 *
 * The threshold is expressed in line heights rather than pixels so it means the
 * same thing at every zoom. A word space is roughly 0.25em, so 0.6 comfortably
 * closes the gaps *inside* a line while leaving a column gutter — several em
 * wide — as the separate box it visually is.
 */
const JOIN_GAP = 0.6;

/** Two rects are on the same line when they overlap vertically by this much. */
const LINE_OVERLAP = 0.5;

/** Assumed popup width, used only to keep it from hanging off the page edge. */
const POPUP_W = 232;

export type HighlightKind = "original" | "mono" | "dual";

/** The palette, as token names. The CSS resolves each to a `--c-hl-*` variable. */
const COLORS = [
  { key: "amber", label: "琥珀" },
  { key: "green", label: "青绿" },
  { key: "blue", label: "湖蓝" },
  { key: "pink", label: "绯红" },
  { key: "purple", label: "紫罗兰" },
] as const;

/**
 * A rectangle in PDF user space: points at scale 1, origin at the page's
 * BOTTOM-left, `x`/`y` naming the box's lower-left corner.
 *
 * This is the wire format and the storage format, and it is the one convention
 * both ends of this feature have to agree on — see `toPdf`/`toCss` below.
 */
interface PdfRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface ApiHighlight {
  id: string;
  paper_id: string;
  kind: HighlightKind;
  page: number;
  rects: PdfRect[];
  text: string | null;
  color: string;
  note: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface HighlightLayerProps {
  paperId: string;
  kind: HighlightKind;
  /** 1-based. */
  page: number;
  /** PDF points -> CSS pixels for the current zoom. */
  scale: number;
  /** Page height in PDF points, for flipping PDF's origin to CSS's. */
  pageHeight: number;
}

/* ------------------------------------------------------------------ transport */

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  for (const [k, v] of Object.entries(authHeaders())) headers.set(k, v);
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const jsonBody = (data: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

/* ---------------------------------------------------------------- coordinates */

/**
 * Browser client rect -> PDF user space. The exact inverse of `toCss`.
 *
 * Three conversions happen at once and getting any one of them wrong produces a
 * highlight that looks right until something changes:
 *
 * 1. **Client -> page-relative.** `origin` is the page box's own client rect, so
 *    the result is independent of scroll position and of where the page sits in
 *    the window. Skip this and highlights drift as the reader scrolls.
 * 2. **CSS pixels -> points.** Divide by the live zoom. Skip this and a
 *    highlight is correct only at the zoom it was drawn at.
 * 3. **Top-left -> bottom-left origin.** CSS measures y downward from the top,
 *    PDF measures it upward from the bottom, so `y` becomes
 *    `pageHeight - (top + height)` — the box's *lower* edge. Skip this and every
 *    highlight is mirrored about the page's horizontal centre line, which looks
 *    plausible near the middle of the page and obviously wrong at the margins.
 */
export function toPdf(r: DOMRect, origin: DOMRect, scale: number, pageHeight: number): PdfRect {
  const top = (r.top - origin.top) / scale;
  const h = r.height / scale;
  return {
    x: (r.left - origin.left) / scale,
    y: pageHeight - (top + h),
    w: r.width / scale,
    h,
  };
}

/** PDF user space -> CSS pixels within the page box. The exact inverse of `toPdf`. */
export function toCss(
  r: PdfRect,
  scale: number,
  pageHeight: number,
): { left: number; top: number; width: number; height: number } {
  return {
    left: r.x * scale,
    top: (pageHeight - (r.y + r.h)) * scale,
    width: r.w * scale,
    height: r.h * scale,
  };
}

/**
 * Collapse per-run client rects into one box per visual line.
 *
 * Rects are grouped into lines by vertical overlap rather than by equal `top`,
 * because a run containing a superscript or a different font sits a pixel or two
 * off its neighbours and an equality test would split the line in two.
 */
export function mergeRects(rects: DOMRect[]): DOMRect[] {
  const usable = rects.filter((r) => r.width > 0 && r.height > 0);
  if (usable.length === 0) return [];

  const lines: DOMRect[][] = [];
  for (const r of [...usable].sort((a, b) => a.top - b.top || a.left - b.left)) {
    const line = lines[lines.length - 1];
    const prev = line?.[0];
    if (prev) {
      const overlap = Math.min(prev.bottom, r.bottom) - Math.max(prev.top, r.top);
      if (overlap >= LINE_OVERLAP * Math.min(prev.height, r.height)) {
        line.push(r);
        continue;
      }
    }
    lines.push([r]);
  }

  const out: DOMRect[] = [];
  for (const line of lines) {
    line.sort((a, b) => a.left - b.left);
    let cur: { l: number; t: number; r: number; b: number } | null = null;
    for (const r of line) {
      if (cur && r.left - cur.r <= JOIN_GAP * (cur.b - cur.t)) {
        cur.r = Math.max(cur.r, r.right);
        cur.t = Math.min(cur.t, r.top);
        cur.b = Math.max(cur.b, r.bottom);
        continue;
      }
      if (cur) out.push(new DOMRect(cur.l, cur.t, cur.r - cur.l, cur.b - cur.t));
      cur = { l: r.left, t: r.top, r: r.right, b: r.bottom };
    }
    if (cur) out.push(new DOMRect(cur.l, cur.t, cur.r - cur.l, cur.b - cur.t));
  }
  return out;
}

/**
 * The portion of `range` that lies inside `pageEl`, or null if none does.
 *
 * A selection that runs across a page break belongs to two pages, and a
 * `Highlight` row carries exactly one page number — so the selection has to be
 * cut at the boundary and stored as one highlight per page. Clamping the Range
 * itself (rather than filtering the rendered rectangles by position) is what
 * makes the *text* come out right too: `clamped.toString()` is this page's share
 * of the passage, not the whole of it repeated on both rows.
 */
function clampToPage(range: Range, pageEl: HTMLElement): Range | null {
  /*
   * Clamped to the *text layer*, not to the page element.
   *
   * The page box also holds this layer's own painted marks and its toolbar, and
   * `Range.getClientRects()` reports a rect for every element it fully contains
   * — so clamping to the page would fold each existing mark's box into the new
   * selection's geometry, and `toString()` would pick up the toolbar's button
   * labels ("添加笔记") as if they were part of the paper. Neither shows up on a
   * clean page, which is exactly why it would ship: it only misfires once the
   * user has highlighted the page they are highlighting again.
   */
  const bounds = document.createRange();
  const textLayer = pageEl.querySelector(".ph-pc-tl");
  if (textLayer) bounds.selectNodeContents(textLayer);
  else bounds.selectNodeContents(pageEl);
  if (!range.intersectsNode(bounds.startContainer)) return null;
  const out = range.cloneRange();
  if (out.compareBoundaryPoints(Range.START_TO_START, bounds) < 0) {
    out.setStart(bounds.startContainer, bounds.startOffset);
  }
  if (out.compareBoundaryPoints(Range.END_TO_END, bounds) > 0) {
    out.setEnd(bounds.endContainer, bounds.endOffset);
  }
  return out.collapsed ? null : out;
}

/* ------------------------------------------------------- cross-page committing */

/**
 * Pending selections, keyed by document then page.
 *
 * Each `HighlightLayer` only ever sees its own page, but a selection can span
 * several. Rather than have every page pop its own toolbar, the page where the
 * selection *starts* shows one and commits the whole set through this registry —
 * so "highlight this sentence" stays one gesture even when the sentence
 * continues overleaf.
 *
 * Module-level state is the right shape here precisely because the layers have
 * no common React ancestor that belongs to this slice: `PdfCanvas` owns the page
 * elements and is not ours to edit.
 */
interface Claim {
  commit: (color: string) => Promise<ApiHighlight>;
  /** Forget this page's draft. Broadcast to every page once the set is stored. */
  reset: () => void;
}
const pending = new Map<string, Map<number, Claim>>();

const groupKey = (paperId: string, kind: string): string => `${paperId} ${kind}`;

function offerClaim(key: string, page: number, claim: Claim | null): void {
  const group = pending.get(key) ?? new Map<number, Claim>();
  if (claim === null) group.delete(page);
  else group.set(page, claim);
  if (group.size === 0) pending.delete(key);
  else pending.set(key, group);
}

/**
 * Commit every page's share of the current selection, first page first, and
 * return what was created.
 *
 * Returning the rows rather than just writing them is what lets the caller
 * attach a note to the highlight it just made without refetching the list and
 * guessing which entry is the new one.
 *
 * Every page is reset afterwards, including the ones showing no toolbar. Only
 * the starting page can dismiss itself — the continuation pages have no UI to
 * click — so without this broadcast they would sit holding an already-committed
 * draft until the user's next click happened to clear it.
 */
async function commitAll(key: string, color: string): Promise<ApiHighlight[]> {
  const group = pending.get(key);
  if (!group) return [];
  const parts = [...group.entries()].sort((a, b) => a[0] - b[0]);
  const made: ApiHighlight[] = [];
  for (const [, claim] of parts) made.push(await claim.commit(color));
  for (const [, claim] of parts) claim.reset();
  return made;
}

/* -------------------------------------------------------------------- component */

/** What this page has claimed from the live selection, ready to be stored. */
interface Draft {
  rects: PdfRect[];
  text: string;
  /** True when the selection began on this page — only that page pops a toolbar. */
  primary: boolean;
}

/**
 * Highlight capture and painting for one PDF page.
 *
 * Mounted as a child of `PdfCanvas`'s `.ph-pc-page`, whose box it covers exactly.
 * Two roots rather than one, because they need to sit on opposite sides of the
 * text layer: the painted marks go *under* it (z-index 0) so the glyphs above
 * stay selectable and copyable, while the toolbar goes *over* it (z-index 4) so
 * its buttons are clickable rather than swallowed by the text spans.
 *
 * That split is also why input is handled by a document-level listener instead
 * of by `onClick` on the marks themselves: nothing painted below the text layer
 * can ever receive a click, so the marks are hit-tested geometrically rather
 * than pretending to be interactive elements.
 */
export function HighlightLayer({
  paperId,
  kind,
  page,
  scale,
  pageHeight,
}: HighlightLayerProps): JSX.Element {
  const qc = useQueryClient();
  const rootRef = useRef<HTMLDivElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteText, setNoteText] = useState("");
  /**
   * The last save that failed to reach the server, shown honestly inside the
   * toolbar. Silence here was a data-loss bug: a create that died (offline,
   * expired token, server hiccup) left no mark and no message — the popup
   * just closed as if the tap had never happened.
   */
  const [saveErr, setSaveErr] = useState<string | null>(null);
  /**
   * Where the toolbar sits, in CSS pixels relative to the page box.
   *
   * Its own state rather than a field on `draft`, because the toolbar outlives
   * the draft: choosing 添加笔记 stores the highlight (clearing the draft) and
   * keeps the same popup open to type into. Anchored to the draft only until the
   * highlight exists, after which `activeAnchor` recomputes it from the stored
   * rectangles so it tracks zoom.
   */
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null);
  /**
   * The page box's width in CSS pixels, captured at the same instant as the
   * anchor and used only to keep the toolbar from hanging off the page edge.
   *
   * State rather than a `getBoundingClientRect()` read during render: measuring
   * layout while rendering is both a forced reflow and a lie, since the value is
   * from the *previous* paint and nothing re-renders when it changes.
   */
  const [pageWidth, setPageWidth] = useState(0);

  const key = groupKey(paperId, kind);

  /* One request per document, not per page: every page instance shares this
     query key, so react-query collapses them into a single fetch. */
  const { data: all } = useQuery({
    queryKey: ["highlights", paperId, kind],
    queryFn: () => req<ApiHighlight[]>(`/papers/${paperId}/highlights?kind=${kind}`),
  });

  const mine = useMemo(
    () => (all ?? []).filter((h) => h.page === page && h.rects.length > 0),
    [all, page],
  );

  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["highlights", paperId, kind] });
  }, [qc, paperId, kind]);

  const create = useMutation({
    mutationFn: (h: { color: string; rects: PdfRect[]; text: string }) =>
      req<ApiHighlight>(`/papers/${paperId}/highlights`, {
        method: "POST",
        ...jsonBody({
          kind,
          page,
          rects: h.rects,
          text: h.text || null,
          color: h.color,
        }),
      }),
    onSuccess: refresh,
    onError: () => setSaveErr("保存失败，请重试"),
  });

  const update = useMutation({
    mutationFn: (p: { id: string; color?: string; note?: string | null }) =>
      req<ApiHighlight>(`/highlights/${p.id}`, {
        method: "PATCH",
        ...jsonBody(p.color !== undefined ? { color: p.color } : { note: p.note }),
      }),
    onSuccess: refresh,
    onError: () => setSaveErr("保存失败，请重试"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => req<void>(`/highlights/${id}`, { method: "DELETE" }),
    onSuccess: refresh,
    onError: () => setSaveErr("删除失败，请重试"),
  });

  /* ------------------------------------------------------------------ closing */

  const dismiss = useCallback(() => {
    setDraft(null);
    setActiveId(null);
    setAnchor(null);
    setNoteOpen(false);
    setSaveErr(null);
    offerClaim(key, page, null);
  }, [key, page]);

  /* --------------------------------------------------- register the committer */
  /**
   * Republished on every render that changes the draft, because the closure has
   * to capture the *current* rects — a stale committer would store the previous
   * selection under the colour the user just picked for this one.
   */
  useEffect(() => {
    if (draft === null) {
      offerClaim(key, page, null);
      return;
    }
    offerClaim(key, page, {
      commit: (color: string) =>
        create.mutateAsync({ color, rects: draft.rects, text: draft.text }),
      reset: () => setDraft(null),
    });
    return () => offerClaim(key, page, null);
    // `create` is a stable mutation object; including it would re-register on
    // every mutation state transition for no benefit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, key, page]);

  useEffect(() => () => offerClaim(key, page, null), [key, page]);

  /* ------------------------------------------------------------ mouse handling */

  useEffect(() => {
    const onMouseUp = (event: MouseEvent): void => {
      const root = rootRef.current;
      if (!root) return;
      // A click inside our own toolbar is the toolbar's business.
      if (popRef.current?.contains(event.target as Node)) return;

      const pageEl = root.parentElement;
      const selection = window.getSelection();

      /*
       * Cheap early-out before any measuring.
       *
       * This listener is on `document` and there is one instance per page, so a
       * single click in a 300-page document would otherwise run 300
       * `getBoundingClientRect()` calls — 300 forced layouts — to discover that
       * 299 of the pages have nothing to do. A click with no selection, on a
       * page holding no draft and no open toolbar, cannot possibly concern this
       * layer unless it landed inside it.
       */
      if (
        (!selection || selection.isCollapsed) &&
        draft === null &&
        activeId === null &&
        !pageEl?.contains(event.target as Node)
      ) {
        return;
      }

      const origin = root.getBoundingClientRect();
      setPageWidth(origin.width);

      /* --- a live selection: claim this page's share of it ------------------ */
      if (pageEl && selection && !selection.isCollapsed && selection.rangeCount > 0) {
        const range = selection.getRangeAt(0);
        const clamped = clampToPage(range, pageEl);
        if (clamped) {
          const boxes = mergeRects(Array.from(clamped.getClientRects()));
          if (boxes.length > 0) {
            const last = boxes[boxes.length - 1]!;
            setActiveId(null);
            setNoteOpen(false);
            setAnchor({
              x: last.left + last.width / 2 - origin.left,
              y: last.bottom - origin.top,
            });
            setDraft({
              // Sliced as a last resort: after merging, a page cannot plausibly
              // hold this many lines, but the request must never be rejected
              // for size after the user has already chosen a colour.
              rects: boxes.slice(0, MAX_RECTS).map((r) => toPdf(r, origin, scale, pageHeight)),
              text: clamped.toString().trim().slice(0, 20000),
              primary: clamped.compareBoundaryPoints(Range.START_TO_START, range) === 0,
            });
            return;
          }
        }
        // The selection exists but is not on this page: drop anything we held.
        if (draft !== null || activeId !== null) dismiss();
        return;
      }

      /* --- no selection: did the click land on a painted mark? -------------- */
      const px = (event.clientX - origin.left) / scale;
      // Clicks arrive in CSS pixels from the top; the marks are in PDF points
      // from the bottom. Convert the *point* rather than the rectangles — one
      // conversion instead of one per mark, and it reuses the same maths.
      const py = pageHeight - (event.clientY - origin.top) / scale;
      const inside =
        event.clientX >= origin.left &&
        event.clientX <= origin.right &&
        event.clientY >= origin.top &&
        event.clientY <= origin.bottom;

      if (!inside) {
        if (draft !== null || activeId !== null) dismiss();
        return;
      }

      // Last first: the newest highlight is painted on top, so it should win.
      const hit = [...mine]
        .reverse()
        .find((h) =>
          h.rects.some(
            (r) => px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h,
          ),
        );

      if (hit) {
        setDraft(null);
        offerClaim(key, page, null);
        setActiveId(hit.id);
        setNoteText(hit.note ?? "");
        setNoteOpen(false);
      } else if (draft !== null || activeId !== null) {
        dismiss();
      }
    };

    document.addEventListener("mouseup", onMouseUp);
    return () => document.removeEventListener("mouseup", onMouseUp);
  }, [scale, pageHeight, mine, draft, activeId, dismiss, key, page]);

  /* Escape closes the toolbar without touching anything. */
  useEffect(() => {
    if (draft === null && activeId === null) return;
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        e.stopPropagation();
        dismiss();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [draft, activeId, dismiss]);

  /* ----------------------------------------------------------------- actions */

  const active = useMemo(() => mine.find((h) => h.id === activeId) ?? null, [mine, activeId]);

  const pick = useCallback(
    async (color: string) => {
      setSaveErr(null);
      // Branch on the id, not on the resolved row: right after 添加笔记 creates a
      // highlight, `activeId` is set but the refetch has not landed, and keying
      // on `active` would make a colour click in that window do nothing at all.
      try {
        if (activeId) {
          await update.mutateAsync({ id: activeId, color });
          dismiss();
          return;
        }
        if (!draft) return;
        await commitAll(key, color);
        // Clear the browser selection: its wash sits above the mark we just
        // painted, so leaving it would hide the result of the user's own click.
        window.getSelection()?.removeAllRanges();
        dismiss();
      } catch {
        // mutateAsync rejects through here too; onError already recorded the
        // message. The popup stays open with the draft intact for a retry.
      }
    },
    [activeId, draft, key, update, dismiss],
  );

  const openNote = useCallback(async () => {
    setSaveErr(null);
    if (!activeId && draft) {
      // A note needs a highlight to hang on, so store one at the default colour
      // first — then the editor is editing something real, and abandoning the
      // note still leaves the mark the user asked for.
      try {
        const made = await commitAll(key, COLORS[0].key);
        window.getSelection()?.removeAllRanges();
        setDraft(null);
        offerClaim(key, page, null);
        // The row for *this* page, taken from the create response rather than by
        // refetching the list and assuming the newest entry is ours — which it
        // would not be for a selection that also created one on the next page.
        setActiveId(made.find((h) => h.page === page)?.id ?? null);
        setNoteText("");
      } catch {
        /* onError already surfaced the failure; keep the draft for a retry */
        return;
      }
    }
    setNoteOpen(true);
  }, [activeId, draft, key, page]);

  const saveNote = useCallback(async () => {
    if (!activeId) return;
    setSaveErr(null);
    try {
      await update.mutateAsync({ id: activeId, note: noteText.trim() || null });
      dismiss();
    } catch {
      /* the editor stays open; onError recorded why */
    }
  }, [activeId, noteText, update, dismiss]);

  /**
   * Hand the selection to the AI panel as an already-asked question. The
   * toolbar knows only that the panel exists; `store.aiPrompt` is the seam —
   * the panel consumes the prompt whether it was open, closed, or not yet
   * mounted. A fresh draft is NOT stored: asking a question should not plant
   * a highlight the user never asked for.
   */
  const askAi = useCallback(
    (manner: "explain" | "translate") => {
      const text = (active?.text ?? draft?.text ?? "").trim();
      if (!text) return;
      const prompt =
        manner === "translate"
          ? `请把下面这段论文原文翻译成中文，专业术语保留英文原文：\n\n「${text}」`
          : `请结合这篇论文的上下文，解释下面这段内容；涉及公式或术语时一并说明：\n\n「${text}」`;
      const ui = useUI.getState();
      ui.setAiPrompt(prompt);
      ui.openAI();
      dismiss();
    },
    [active, draft, dismiss],
  );

  /* -------------------------------------------------------------------- view */

  const activeAnchor = useMemo(() => {
    if (!active) return null;
    const last = active.rects[active.rects.length - 1]!;
    const box = toCss(last, scale, pageHeight);
    return { x: box.left + box.width / 2, y: box.top + box.height };
  }, [active, scale, pageHeight]);

  // Prefer the stored highlight's own geometry once it exists, so the toolbar
  // follows the mark through a zoom; fall back to where the drag ended.
  const at = activeAnchor ?? anchor;
  // Keyed on `activeId`, not on `active`: between creating a highlight and the
  // list refetching, the row is not in `mine` yet, and keying on the resolved
  // object would blink the note editor out of existence just as it opened.
  const showPopup = (draft?.primary ?? false) || activeId !== null;
  const half = POPUP_W / 2;
  // Clamped so the toolbar stays over the page rather than out in the margin.
  // `Math.min` first, then `Math.max`, so a page narrower than the toolbar
  // pins it to the left edge instead of producing an inverted range.
  const left = at ? Math.max(half + 2, Math.min(pageWidth - half - 2, at.x)) : 0;

  return (
    <>
      <div className="ph-hl" ref={rootRef} aria-hidden="true">
        {mine.map((h) =>
          h.rects.map((r, i) => {
            const box = toCss(r, scale, pageHeight);
            return (
              <span
                key={`${h.id}-${i}`}
                className={`ph-hl-mark${h.id === activeId ? " is-on" : ""}${
                  h.note ? " has-note" : ""
                }`}
                style={{
                  left: box.left,
                  top: box.top,
                  width: box.width,
                  height: box.height,
                  background: `var(--c-hl-${h.color}, var(--c-hl-amber))`,
                }}
              />
            );
          }),
        )}
      </div>

      {showPopup && at && (
        <div
          className="ph-hl-pop"
          ref={popRef}
          style={{ left, top: at.y + 8 }}
          role="dialog"
          aria-label="标注"
        >
          <div className="ph-hl-row">
            {COLORS.map((c) => (
              <button
                key={c.key}
                className={`ph-hl-sw${active?.color === c.key ? " is-on" : ""}`}
                style={{ background: `var(--c-hl-${c.key})` }}
                title={c.label}
                aria-label={c.label}
                onClick={() => void pick(c.key)}
              />
            ))}
            <span className="ph-hl-sep" />
            <button className="ph-hl-btn" title="添加笔记" onClick={() => void openNote()}>
              <Icons.writing size={13} />
              {active?.note ? "编辑笔记" : "添加笔记"}
            </button>
            {active && (
              <button
                className="ph-hl-btn is-danger"
                title="删除标注"
                onClick={() => {
                  remove.mutate(active.id);
                  dismiss();
                }}
              >
                <Icons.trash size={13} />
              </button>
            )}
          </div>

          <div className="ph-hl-row">
            <button
              className="ph-hl-btn is-ai"
              title="让 AI 结合论文上下文解释这段内容"
              onClick={() => askAi("explain")}
            >
              <Icons.spark size={12} />
              解释
            </button>
            <button
              className="ph-hl-btn is-ai"
              title="翻译这段内容"
              onClick={() => askAi("translate")}
            >
              翻译
            </button>
            <button
              className="ph-hl-btn"
              title="复制原文，粘贴到任何地方"
              onClick={() => {
                const text = (active?.text ?? draft?.text ?? "").trim();
                if (text) void navigator.clipboard?.writeText(text).catch(() => undefined);
                dismiss();
              }}
            >
              复制
            </button>
          </div>

          {saveErr && <div className="ph-hl-err">{saveErr}</div>}
          {!saveErr && (create.isPaused || update.isPaused) && (
            <div className="ph-hl-err is-wait">等待网络，恢复后自动保存</div>
          )}
          {!saveErr && !create.isPaused && !update.isPaused && (create.isPending || update.isPending) && (
            <div className="ph-hl-err is-wait">正在保存…</div>
          )}

          {noteOpen && (
            <div className="ph-hl-note">
              <textarea
                className="ph-hl-ta"
                value={noteText}
                autoFocus
                placeholder="写下你的想法…"
                onChange={(e) => setNoteText(e.target.value)}
              />
              <div className="ph-hl-note-row">
                <button className="ph-hl-ghost" onClick={dismiss}>
                  取消
                </button>
                <button className="ph-hl-primary" onClick={() => void saveNote()}>
                  保存
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}

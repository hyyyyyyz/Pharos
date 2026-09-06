/**
 * NoteLayer — typed text on a page: 文本框 (text box) and 便利贴 (sticky note).
 *
 * The third kind of mark, after handwriting and tape, and the only one made of
 * characters. A stylus is the wrong instrument for a paragraph and handwriting
 * is the wrong format for anything meant to be re-read or searched, so the
 * keyboard tool (键盘符) and the pen's long-press menu both land here.
 *
 * DOM, not canvas, and that is the whole reason this is its own layer: the
 * thing being placed is a `<textarea>`. Real text editing — a caret, an IME
 * for Chinese, selection, autocorrect, the on-screen keyboard appearing at all
 * — is something the platform does and a canvas cannot. Ink is painted because
 * ink is geometry; this is typed because it is text.
 *
 * Mounted inside `.ph-pc-page` alongside `InkLayer`/`TapeLayer`, above ink and
 * below tape: a note is written ON the page, and a strip of tape can cover it
 * exactly as it covers everything else.
 *
 * Coordinates: PDF user space, points at scale 1, bottom-left origin — the
 * same contract as every other mark. `(x, y)` is the box's own CENTRE, like a
 * tape strip, because a note is moved and resized as an object.
 *
 * Interaction:
 *
 * - **The 文本 tool active**: a tap on blank paper makes a box and focuses it.
 * - **Any tool, or none**: an existing note can be tapped to edit, dragged by
 *   its grip to move, and pulled by its corner to resize. Those handles only
 *   appear while the 文本 tool is on — the rest of the time a note is content,
 *   not furniture.
 * - **Empty on blur is deleted.** A box you tapped into and thought better of
 *   leaves nothing behind; otherwise every stray tap would litter the page
 *   with invisible empty rectangles that still catch the pointer.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { PageNoteRow, PageNoteStyle, PdfKind } from "../api/types";
import { Icons } from "../design/icons";
import { useUI } from "../store";
import "./NoteLayer.css";

/** A new box's size, in PDF points at scale 1 — about a line and a half of
 *  12pt text across a comfortable column. Big enough to type into without
 *  immediately resizing, small enough not to cover what it comments on. */
export const NEW_NOTE_W = 180;
export const NEW_NOTE_H = 44;
/** Mirrors `services/pagenote.MIN_SIZE`/`MAX_SIZE` so a drag can never ask for
 *  a size the server will refuse. */
const MIN_SIZE = 8;
const MAX_SIZE = 2000;

const clampSize = (v: number): number =>
  !Number.isFinite(v) ? MIN_SIZE : Math.min(MAX_SIZE, Math.max(MIN_SIZE, v));

/** How long the text is debounced before it goes to the server. Typing is not
 *  a per-keystroke network event; it is a burst that settles. */
const SAVE_DEBOUNCE_MS = 600;

export function NoteLayer({
  paperId,
  kind,
  page,
  scale,
  pageHeight,
}: {
  paperId: string;
  kind: PdfKind;
  /** 1-based. */
  page: number;
  /** PDF points -> CSS pixels for the current zoom. */
  scale: number;
  pageHeight: number;
}): JSX.Element | null {
  const qc = useQueryClient();
  const inkMode = useUI((s) => s.inkMode);
  const noteColor = useUI((s) => s.noteColor);
  const noteStyle = useUI((s) => s.noteStyle);
  /** A note the toolbar or a long-press asked to be created and focused. */
  const pendingFocus = useUI((s) => s.noteFocusId);
  const setPendingFocus = useUI((s) => s.setNoteFocusId);

  // Same `staleTime: Infinity` reasoning as ink and tape: every write patches
  // the cache directly, so a background refetch only risks a race — a stale
  // GET landing after a local edit and reviving what it replaced.
  const { data: all } = useQuery({
    queryKey: ["note", paperId, kind],
    queryFn: ({ signal }) => api.note.list(paperId, kind, signal),
    staleTime: Infinity,
  });
  const mine = useMemo(() => (all ?? []).filter((n) => n.page === page), [all, page]);

  const updateCache = useCallback(
    (updater: (prev: PageNoteRow[]) => PageNoteRow[]) => {
      qc.setQueryData<PageNoteRow[]>(["note", paperId, kind], (prev) => updater(prev ?? []));
    },
    [qc, paperId, kind],
  );

  const patch = useCallback(
    (id: string, fields: Partial<PageNoteRow>) => {
      updateCache((prev) => prev.map((n) => (n.id === id ? { ...n, ...fields } : n)));
      void api.note.update(id, fields).catch(() => {
        void qc.invalidateQueries({ queryKey: ["note", paperId, kind] });
      });
    },
    [updateCache, qc, paperId, kind],
  );

  const remove = useCallback(
    (id: string) => {
      updateCache((prev) => prev.filter((n) => n.id !== id));
      void api.note.remove(id).catch(() => {
        void qc.invalidateQueries({ queryKey: ["note", paperId, kind] });
      });
    },
    [updateCache, qc, paperId, kind],
  );

  /* --------------------------------------------------------------- create */

  const wrapRef = useRef<HTMLDivElement>(null);

  const addAt = useCallback(
    async (pageX: number, pageY: number, style: PageNoteStyle) => {
      try {
        const row = await api.note.create(paperId, {
          kind,
          page,
          x: pageX,
          y: pageY,
          w: NEW_NOTE_W,
          h: NEW_NOTE_H,
          style,
          color: noteColor,
        });
        updateCache((prev) => [...prev, row]);
        setPendingFocus(row.id);
      } catch {
        /* refused (hostile geometry, or the paper is not this user's) —
           nothing was optimistically added, so nothing to roll back. */
      }
    },
    [paperId, kind, page, noteColor, updateCache, setPendingFocus],
  );

  /**
   * A tap on blank paper with the 文本 tool active makes a note there.
   *
   * Native listener on the create surface rather than a React handler, for the
   * same reason `InkLayer` uses native listeners: this element only exists
   * while the tool is on, and a tap must be told from a drag before anything
   * is created.
   */
  useEffect(() => {
    if (inkMode !== "text") return;
    const el = wrapRef.current?.querySelector(".ph-note-catch") as HTMLElement | null;
    if (!el) return;
    let down: { x: number; y: number } | null = null;
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY };
    };
    const onUp = (e: PointerEvent) => {
      const from = down;
      down = null;
      if (!from) return;
      if (Math.hypot(e.clientX - from.x, e.clientY - from.y) > 6) return; // a drag
      const origin = el.getBoundingClientRect();
      void addAt(
        (e.clientX - origin.left) / scale,
        pageHeight - (e.clientY - origin.top) / scale,
        noteStyle,
      );
    };
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
    };
  }, [inkMode, addAt, scale, pageHeight, noteStyle]);

  if (mine.length === 0 && inkMode !== "text") return null;

  return (
    <div ref={wrapRef} className="ph-note" aria-hidden={false}>
      {/* The tap surface for making a new note, present only while the tool
          is. First child, so existing notes stay reachable on top of it. */}
      {inkMode === "text" && <div className="ph-note-catch" />}
      {mine.map((n) => (
        <NoteBox
          key={n.id}
          note={n}
          scale={scale}
          pageHeight={pageHeight}
          editable={inkMode === "text" || inkMode === "off"}
          chrome={inkMode === "text"}
          autoFocus={pendingFocus === n.id}
          onFocused={() => setPendingFocus(null)}
          onPatch={(fields) => patch(n.id, fields)}
          onRemove={() => remove(n.id)}
        />
      ))}
    </div>
  );
}

/**
 * One note.
 *
 * The text lives in local state while it is being typed and is pushed to the
 * server on a debounce — a PATCH per keystroke would be one request per
 * character, and on a tablet keyboard with an IME composing Chinese it would
 * also fire mid-composition, saving half-formed syllables.
 */
function NoteBox({
  note,
  scale,
  pageHeight,
  editable,
  chrome,
  autoFocus,
  onFocused,
  onPatch,
  onRemove,
}: {
  note: PageNoteRow;
  scale: number;
  pageHeight: number;
  /** Can the text be typed into at all? */
  editable: boolean;
  /** Show the move grip, resize corner and delete button — furniture that only
   *  belongs while the 文本 tool is the active one. */
  chrome: boolean;
  autoFocus: boolean;
  onFocused: () => void;
  onPatch: (fields: Partial<PageNoteRow>) => void;
  onRemove: () => void;
}): JSX.Element {
  const [text, setText] = useState(note.body);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  const savedRef = useRef(note.body);
  /** Live geometry during a drag/resize, so the box follows the finger without
   *  a network round trip per frame. Null = whatever the row says. */
  const [live, setLive] = useState<{ x: number; y: number; w: number; h: number } | null>(null);

  // A change from elsewhere (another device, an undo) replaces what is shown —
  // but never while this box is the one being typed into.
  useEffect(() => {
    if (document.activeElement === areaRef.current) return;
    setText(note.body);
    savedRef.current = note.body;
  }, [note.body]);

  useEffect(() => {
    if (!autoFocus) return;
    areaRef.current?.focus();
    onFocused();
  }, [autoFocus, onFocused]);

  /* Debounced save. The timer is cleared on unmount, and `flush` runs on blur
     so leaving a note never loses the last few characters. */
  useEffect(() => {
    if (text === savedRef.current) return;
    const t = setTimeout(() => {
      savedRef.current = text;
      onPatch({ body: text });
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [text, onPatch]);

  const box = live ?? note;
  const left = (box.x - box.w / 2) * scale;
  const top = (pageHeight - box.y - box.h / 2) * scale;

  /** Drag the grip to move, the corner to resize. One handler for both: they
   *  differ only in which numbers the delta is added to. */
  const startDrag = (e: React.PointerEvent, mode: "move" | "resize") => {
    e.preventDefault();
    e.stopPropagation();
    const el = e.currentTarget as HTMLElement;
    el.setPointerCapture(e.pointerId);
    const from = { x: e.clientX, y: e.clientY };
    const base = { x: note.x, y: note.y, w: note.w, h: note.h };
    const onMove = (ev: PointerEvent) => {
      const dx = (ev.clientX - from.x) / scale;
      const dy = (ev.clientY - from.y) / scale;
      setLive(
        mode === "move"
          ? { ...base, x: base.x + dx, y: base.y - dy }
          : {
              // Resizing from the bottom-right keeps the top-left corner
              // still, which means the centre moves by half the delta.
              w: clampSize(base.w + dx),
              h: clampSize(base.h + dy),
              x: base.x + (clampSize(base.w + dx) - base.w) / 2,
              y: base.y - (clampSize(base.h + dy) - base.h) / 2,
            },
      );
    };
    const onUp = () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      setLive((cur) => {
        if (cur) onPatch(cur);
        return null;
      });
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
  };

  return (
    <div
      className={`ph-note-box is-${note.style}${chrome ? " has-chrome" : ""}`}
      style={{
        left,
        top,
        width: box.w * scale,
        height: box.h * scale,
        // The token resolves against `.ph-note`'s palette; a note's colour
        // tints the card for a 便利贴 and inks the glyphs for a 文本框.
        "--c-note": `var(--c-ink-${note.color}, var(--c-tx))`,
      } as React.CSSProperties}
    >
      <textarea
        ref={areaRef}
        className="ph-note-text"
        style={{ fontSize: note.size * scale }}
        value={text}
        readOnly={!editable}
        placeholder={editable ? "输入文字…" : ""}
        onChange={(e) => setText(e.target.value)}
        onBlur={() => {
          // Empty and abandoned: take it away rather than leave an invisible
          // rectangle on the page that still catches the pointer.
          if (text.trim() === "" && note.body.trim() === "") {
            onRemove();
            return;
          }
          if (text !== savedRef.current) {
            savedRef.current = text;
            onPatch({ body: text });
          }
        }}
        // A pen or finger inside the text must reach the caret, not the page
        // beneath — this element is the one place on the page that wants the
        // platform's own text handling.
        onPointerDown={(e) => e.stopPropagation()}
      />
      {chrome && (
        <>
          <span
            className="ph-note-grip"
            title="拖动移动"
            onPointerDown={(e) => startDrag(e, "move")}
          />
          <span
            className="ph-note-resize"
            title="拖动改变大小"
            onPointerDown={(e) => startDrag(e, "resize")}
          />
          <button
            className="ph-note-del"
            title="删除"
            aria-label="删除这条文字"
            onPointerDown={(e) => e.stopPropagation()}
            onClick={onRemove}
          >
            <Icons.close size={10} />
          </button>
        </>
      )}
    </div>
  );
}

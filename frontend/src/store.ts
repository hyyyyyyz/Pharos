import { create } from "zustand";
import { resolveTheme } from "./design/tokens";
import type { AccentKey, ThemeMode, ThemePref } from "./design/tokens";
import type {
  AuthUser,
  InkStrokeRow,
  PageNoteStyle,
  TapeRow,
  ZoteroOAuthResult,
} from "./api/types";
import type { InkColorUsage } from "./lib/ink";
import { getSession, subscribe as subscribeSession } from "./auth/session";

export type ModuleKey = "library" | "daily" | "search" | "kb" | "writing" | "runs" | "admin";
/** Modules that are actually built. Everything else falls through to <ComingSoon />. */
export type LiveModuleKey = "library" | "daily" | "search" | "kb" | "runs" | "admin";
export type ReadMode = "zh" | "bilingual" | "original";
export type OutlineMode = "outline" | "thumbs";
export type SettingsTab = "account" | "ai" | "appearance" | "daily";
export type SortCol = "title" | "authors" | "year" | "pages" | "status";
export type SortDir = "asc" | "desc";

/** The reader's stylus tools. Off = the ink layer paints but never captures. */
/** "water" and "laser" both write with the same gesture code as "draw" —
 *  each is a colour/rendering choice (see `InkLayer`'s `isWaterColor`
 *  routing, and its laser-mode branch in `paintWet`), not a different
 *  interaction. A laser stroke is never sent to the backend at all: it
 *  fades on the wet canvas and is gone, nothing to undo or persist. */
/** "tape" is `TapeLayer`'s own tool and "text" is `NoteLayer`'s, neither of
 *  them `InkLayer`'s — `InkLayer` treats both exactly like "off" (see its
 *  gesture effect's early return) so nothing draws ink while a strip is being
 *  placed or a text box typed into. */
export type InkMode =
  | "off"
  | "draw"
  | "water"
  | "laser"
  | "style"
  | "erase"
  | "select"
  | "tape"
  | "text";

/**
 * One undoable ink operation, for the document-level undo stack.
 *
 * "add" is one finished stroke; "remove" is one eraser gesture, which may have
 * taken several strokes at once and must come back together. "edit" replaces
 * whole rows (a lasso move/recolour, or a partial erase that splits a stroke):
 * `added` rows exist after the op, `removed` rows don't — the inverse flips
 * the sides. In every op, `added` carries the ids that must be *live* for the
 * next undo to delete; `removed` is only ever a payload to recreate. Redo of
 * an edit re-creates from payloads and replaces `added` with fresh rows —
 * the same protocol "add" already follows.
 */
export type InkOp =
  | { kind: "add"; stroke: InkStrokeRow }
  | { kind: "remove"; strokes: InkStrokeRow[] }
  | { kind: "edit"; removed: InkStrokeRow[]; added: InkStrokeRow[] }
  /* 胶带 shares the reader's ONE undo stack rather than keeping its own:
     "撤回操作应包括…胶带粘贴", and a reader who has just laid down a strip and
     hits undo means that strip, whatever kind of mark it happened to be.
     Same id protocol as the stroke ops — a row recreated by an undo carries a
     fresh id, so the op that crosses to the other stack is rewritten with
     what the network returned. */
  | { kind: "tape-add"; tape: TapeRow }
  | { kind: "tape-remove"; tape: TapeRow }
  | { kind: "tape-edit"; id: string; before: TapePatch; after: TapePatch }
  /* One gesture, one undo. A lasso drag over a mixed selection changes
     strokes AND strips, and pushing those as separate entries meant undoing
     "that drag" took several presses, with the page half-restored in between.
     A batch is applied in order and undone in reverse, like any transaction. */
  | { kind: "batch"; ops: InkOp[] };

/**
 * The fields a 胶带 edit can change — everything a resize, a straighten, or a
 * lasso move/scale/rotate touches. A freehand strip's `points` are in here
 * too: rotating one rewrites its path, and an undo that restored only the box
 * would leave the strip drawn along the OLD curve in a new place.
 *
 * Deliberately NOT `revealed`: covering and uncovering is its own undo (tap
 * it again), and filling a capped history with reveal toggles would push real
 * edits out of it.
 */
export interface TapePatch {
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  angle?: number;
  points?: { x: number; y: number }[] | null;
}

/** Undo/redo ops carry full stroke payloads (points and all), so an
 *  all-day note-taking session cannot be let grow the stack forever — the
 *  oldest ops fall off once the cap is hit, same as every other note app's
 *  bounded undo. Redo is capped identically so undo→redo→undo… cannot
 *  regrow past it either. */
export const MAX_INK_HISTORY = 200;

/** Stroke width bounds, in PDF points at scale 1 — mirrors the backend's
 *  `MIN_WIDTH`/`MAX_WIDTH` (`services/ink.py`) exactly, so a value the
 *  slider allows is never one the server refuses. */
export const MIN_INK_WIDTH = 1;
export const MAX_INK_WIDTH = 100;

export function clampInkWidth(width: number): number {
  if (!Number.isFinite(width)) return MIN_INK_WIDTH;
  return Math.min(MAX_INK_WIDTH, Math.max(MIN_INK_WIDTH, width));
}

/** A highlighter needs breadth, not a pen's line — the width switching into
 *  水彩笔 mode suggests, if the reader has not already picked one of their own. */
export const DEFAULT_WATER_WIDTH = 14;

/**
 * What each drawing tool remembers between visits.
 *
 * One thickness control and one palette serve every tool ("粗细统一拉出来设定
 * 大小，不要单独列开"), but the VALUE each tool wants is different: a pen
 * writes thin, a watercolour wash has to be broad to be a wash at all, and
 * their palettes do not even overlap (opaque inks vs light washes). Sharing a
 * single `inkWidth`/`inkColor` across tools meant every switch either carried
 * the wrong value over — a 2pt "wash", a wash colour on the pen that the
 * water canvas then painted — or had to be papered over with a reset on each
 * toolbar button, which is what round 2 did.
 *
 * So the live `inkColor`/`inkWidth` stay exactly as every consumer already
 * reads them, and `setInkMode` swaps them out to and in from these slots.
 * Nothing downstream had to learn about tools.
 */
export interface ToolPref {
  color: string;
  width: number;
}

export const DEFAULT_TOOL_PREFS: Record<string, ToolPref> = {
  draw: { color: "ink", width: 2 },
  water: { color: "wc-amber", width: DEFAULT_WATER_WIDTH },
  // The style brush paints an existing stroke's colour/width onto it, so it
  // starts from the pen's own habits rather than a third set.
  style: { color: "ink", width: 2 },
};

/** Tools that carry a colour/width of their own. The eraser, lasso, laser and
 *  tape do not — nothing they do is coloured by `inkColor`. */
export function toolRemembers(mode: InkMode): boolean {
  return mode === "draw" || mode === "water" || mode === "style";
}

export function capHistory<T>(ops: T[]): T[] {
  return ops.length > MAX_INK_HISTORY ? ops.slice(ops.length - MAX_INK_HISTORY) : ops;
}

/** Rewrite one op so any reference to `oldId` names the recreated row
 *  instead. Exported for its own test — the shape is easy to get subtly
 *  wrong, and the failure it prevents (a duplicated strip) only shows up
 *  two undos later. */
export function remapOp(op: InkOp, oldId: string, next: InkStrokeRow | TapeRow): InkOp {
  const stroke = next as InkStrokeRow;
  const tape = next as TapeRow;
  switch (op.kind) {
    case "add":
      return op.stroke.id === oldId ? { ...op, stroke } : op;
    case "remove":
      return op.strokes.some((s) => s.id === oldId)
        ? { ...op, strokes: op.strokes.map((s) => (s.id === oldId ? stroke : s)) }
        : op;
    case "edit": {
      const hit =
        op.removed.some((s) => s.id === oldId) || op.added.some((s) => s.id === oldId);
      return hit
        ? {
            ...op,
            removed: op.removed.map((s) => (s.id === oldId ? stroke : s)),
            added: op.added.map((s) => (s.id === oldId ? stroke : s)),
          }
        : op;
    }
    case "tape-add":
      return op.tape.id === oldId ? { ...op, tape } : op;
    case "tape-remove":
      return op.tape.id === oldId ? { ...op, tape } : op;
    case "tape-edit":
      return op.id === oldId ? { ...op, id: next.id } : op;
    case "batch":
      return { ...op, ops: op.ops.map((inner) => remapOp(inner, oldId, next)) };
  }
}

/** Fold several ops from ONE gesture into a single history entry. A lone op
 *  stays as it is — a batch of one would only make the stacks harder to read
 *  in a debugger for no behavioural gain. */
export function batchOps(ops: InkOp[]): InkOp[] {
  if (ops.length <= 1) return ops;
  return [{ kind: "batch", ops }];
}

/**
 * What colour a new note starts as, per style.
 *
 * A 文本框's colour is its GLYPHS and a 便利贴's is its CARD, so one default
 * cannot serve both: black text is right and a black card is a redaction bar;
 * amber is a sticky note and amber handwriting is hard to read. Same problem
 * the pen and the watercolour have, solved the same way — see `DEFAULT_TOOL_PREFS`.
 */
export const DEFAULT_NOTE_COLORS: Record<PageNoteStyle, string> = {
  text: "ink",
  note: "amber",
};

/** How the eraser takes ink away. */
export type EraseMode = "stroke" | "pixel";

/**
 * What a 剪切/复制 of a lasso selection holds, ready for 粘贴.
 *
 * Payloads rather than rows: pasting creates new rows on whatever page is
 * under the reader now, so the ids, timestamps and page numbers of the source
 * would all be wrong on the copy. Coordinates are the source's own PDF-space
 * ones; the paste translates them (and clamps the result onto the target
 * page), so a copy can be pasted onto a different page — or the same one —
 * without the clipboard having to know where it will land.
 */
export interface InkClipboard {
  strokes: {
    points: { x: number; y: number; p: number }[];
    color: string;
    width: number;
  }[];
  tapes: {
    x: number;
    y: number;
    w: number;
    h: number;
    angle: number;
    points: { x: number; y: number }[] | null;
    revealed: boolean;
  }[];
}

export const RAIL_MIN_WIDTH = 144;
export const RAIL_DEFAULT_WIDTH = 178;
export const RAIL_MAX_WIDTH = 280;
const RAIL_EXPANDED_KEY = "ph-rail-expanded-v2";
const RAIL_WIDTH_KEY = "ph-rail-width";

/** An open tab in the 文库 module: the library itself, or one paper. */
export type Tab =
  | { id: "library"; kind: "library" }
  | { id: string; kind: "paper"; paperId: string };

const ls = (key: string): string | null =>
  typeof localStorage !== "undefined" ? localStorage.getItem(key) : null;

const save = (key: string, value: string) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode — appearance just won't persist */
  }
};

const THEME_KEY = "ph-theme";

/** The live `prefers-color-scheme` query, or null where matchMedia is absent. */
const darkQuery = (): MediaQueryList | null =>
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

const systemPrefersDark = (): boolean => darkQuery()?.matches ?? false;

/**
 * The live `(pointer: coarse)` query, or null where matchMedia is absent.
 *
 * Touch is what separates an Android tablet from a desktop window of the same
 * pixel size: the tablet gets the detail panel as a slide-over instead of a
 * third permanent column, because 196px of 分类树 plus 280px of 详情 leave a
 * list too narrow to read. A narrow desktop window keeps the classic three
 * panes — mouse users squeeze, they do not tap through overlays.
 */
const coarseQuery = (): MediaQueryList | null =>
  typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(pointer: coarse)")
    : null;

const hasCoarsePointer = (): boolean => coarseQuery()?.matches ?? false;

/**
 * The stored appearance preference, validated rather than cast.
 *
 * The key predates `auto` and holds whatever a previous build wrote, so a value
 * outside the three known ones has to fall back rather than become the theme —
 * an unchecked `as ThemePref` would put a garbage string into `resolveTheme`
 * and paint light without anyone being able to tell why.
 *
 * The default stays `light`, NOT `auto`. A user who has never opened 外观 has
 * nothing stored, and defaulting to `auto` would restyle every one of those
 * browsers on a release that only added an option. Retiring a stored default is
 * something this file does deliberately and visibly, by versioning the key —
 * see `ph-accent-v2` below.
 */
function initialThemePref(): ThemePref {
  const raw = ls(THEME_KEY);
  return raw === "light" || raw === "dark" || raw === "auto" ? raw : "light";
}

const INITIAL_THEME_PREF = initialThemePref();

export function clampRailWidth(width: number): number {
  return Math.min(RAIL_MAX_WIDTH, Math.max(RAIL_MIN_WIDTH, Math.round(width)));
}

function initialRailWidth(): number {
  const raw = ls(RAIL_WIDTH_KEY);
  if (raw === null) return RAIL_DEFAULT_WIDTH;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? clampRailWidth(parsed) : RAIL_DEFAULT_WIDTH;
}

const INK_COLOR_USAGE_KEY = "ph-ink-color-usage-v1";
const INK_SOUND_KEY = "ph-ink-sound";

function initialInkColorUsage(): Record<string, InkColorUsage> {
  const raw = ls(INK_COLOR_USAGE_KEY);
  if (raw === null) return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed !== null && typeof parsed === "object"
      ? (parsed as Record<string, InkColorUsage>)
      : {};
  } catch {
    return {}; // corrupt value from a previous build — start clean, not crash
  }
}

interface UIState {
  /* ---------------------------------------------------------- appearance */
  /** What the user chose — the value the 主题 picker highlights. */
  themePref: ThemePref;
  /** What to paint. Equal to `themePref` unless that is `auto`, in which case
   *  it tracks the OS. Everything that renders reads THIS one. */
  theme: ThemeMode;
  accent: AccentKey;
  setTheme: (t: ThemePref) => void;
  setAccent: (a: AccentKey) => void;

  /* ---------------------------------------------------------------- rail */
  activeModule: ModuleKey;
  setModule: (m: ModuleKey) => void;
  railExpanded: boolean;
  toggleRail: () => void;
  railWidth: number;
  setRailWidth: (width: number) => void;
  resetRailWidth: () => void;

  /* ------------------------------------------------------- library: tree */
  selectedCol: string; // "lib" | "uncat" | "trash" | collection id
  selectCol: (id: string) => void;
  favOpen: boolean;
  toggleGroup: (g: "favOpen") => void;
  selectedTags: string[];
  toggleTag: (t: string) => void;

  /* ------------------------------------------------------- library: list */
  query: string;
  setQuery: (q: string) => void;
  arxivInput: string;
  setArxivInput: (v: string) => void;
  sortCol: SortCol;
  sortDir: SortDir;
  setSort: (col: SortCol) => void;
  selectedIds: string[];
  selectedPaperId: string | null;
  lastClick: string | null;
  /** Click a row. `order` is the currently visible id order (for shift-range). */
  selectRow: (id: string, order: string[], mods: { meta: boolean; shift: boolean }) => void;
  /** Whether the 详情 panel is on screen in overlay mode (see `isDetailOverlay`). */
  libDetailOpen: boolean;
  setLibDetail: (open: boolean) => void;

  /* --------------------------------------------------------------- daily */
  /** "YYYY-MM-DD" of the digest being viewed; null = not chosen yet, so the
   *  view falls back to the newest date the backend reports. */
  dailyDate: string | null;
  setDailyDate: (d: string | null) => void;
  /** DailyPaper.id of the expanded card; null = none open. */
  dailyPaperId: string | null;
  setDailyPaper: (id: string | null) => void;

  /* ------------------------------------------------------------ projects */
  /** Shared between 文献探索 and 研究项目 so a result can be filed without
   *  making the user select the same project twice. */
  activeProjectId: string | null;
  setActiveProject: (id: string | null) => void;

  /* ---------------------------------------------------------------- tabs */
  tabs: Tab[];
  activeTabId: string;
  openPaper: (paperId: string) => void;
  setTab: (id: string) => void;
  closeTab: (id: string) => void;

  /* -------------------------------------------------------------- reader */
  readMode: ReadMode;
  setReadMode: (m: ReadMode) => void;
  outlineOpen: boolean;
  toggleOutline: () => void;
  outlineMode: OutlineMode;
  setOutlineMode: (m: OutlineMode) => void;
  /** "auto" = follow the window width (>=1200px); a boolean once the user decides. */
  aiOpenPref: "auto" | boolean;
  winW: number;
  setWinW: (w: number) => void;
  toggleAI: () => void;
  /** Open the AI panel for real (a selection asking for an explanation must
   *  open it, not flip it). */
  openAI: () => void;
  /**
   * A question queued for the AI panel by somewhere else in the reader (a
   * selection's 问AI button). The panel consumes it — sends, then clears —
   * so the sender never needs to know whether the panel is mounted yet.
   */
  aiPrompt: string | null;
  setAiPrompt: (text: string | null) => void;
  /** Live `(pointer: coarse)` match — the tablet/desktop fork for layouts. */
  pointerCoarse: boolean;

  /* ----------------------------------------------------------------- ink */
  /** Stylus tool for the reader. Off by default: ink never captures a pointer
   *  until asked, so selection/pan/scroll behave exactly as before. */
  inkMode: InkMode;
  setInkMode: (m: InkMode) => void;
  /** Per-tool colour/width memory — see `DEFAULT_TOOL_PREFS`. Read through
   *  `inkColor`/`inkWidth`; this is only where the values a tool is not
   *  currently using are parked. */
  toolPrefs: Record<string, ToolPref>;
  /**
   * Which collapsible tray under the active tool's popover is open — 调色盘,
   * 粗细, or neither. At most one at a time ("每次只启用一个，不然会重叠").
   *
   * In the store rather than in `ReadingView`'s own state because the thing
   * that most needs to close it is the furthest from it: the moment a stroke
   * starts, the tray should get out of the way ("开始书写后，折叠栏应该自动收
   * 起来"), and that gesture is detected down in `InkLayer`/`TapeLayer`.
   */
  inkTray: "color" | "width" | null;
  setInkTray: (tray: "color" | "width" | null) => void;
  /**
   * Ids currently being previewed by a lasso drag in `InkLayer`.
   *
   * `TapeLayer` hides these while the drag is in flight, because the moving
   * copy is painted on the ink layer's wet canvas — without this the reader
   * sees the strip twice, once where it was and once where it is going. The
   * ink layer already does the same for strokes through a ref; tape needs it
   * in the store because the two live in different components.
   */
  inkCarried: string[];
  setInkCarried: (ids: string[]) => void;
  /** Token name from `INK_COLORS`; the backend stores names, never hexes. */
  inkColor: string;
  setInkColor: (c: string) => void;
  /** Pick count + last-picked time per colour token, for the quick-bar
   *  ranking (`rankInkColors`) — how the four-swatch bar decides which three
   *  non-black colours earn a slot today. Persisted so the ranking survives
   *  a reload instead of resetting to the fixed order every session. */
  inkColorUsage: Record<string, InkColorUsage>;
  /** Stroke width in PDF points at scale 1 — the 1× value, not screen px. */
  inkWidth: number;
  setInkWidth: (w: number) => void;
  /** Draw with a finger, not just a stylus. Off = palm rejection on touch. */
  inkFingerDraw: boolean;
  toggleInkFingerDraw: () => void;
  /** 书写音效: a synthesised nib-on-paper texture that follows the pen's own
   *  speed (`lib/penSound`). Off by default and persisted — a reader in a
   *  library does not want a surprise noise out of a page, and a feature you
   *  have to find in order to silence it is a worse default than one you have
   *  to find in order to hear it. */
  inkSound: boolean;
  toggleInkSound: () => void;
  /** Eraser radius in CSS pixels — what the on-page preview circle shows. */
  inkEraserSize: number;
  setInkEraserSize: (s: number) => void;
  /** 整笔 = remove whole strokes (OneNote); 局部 = split strokes where the
   *  eraser passes (real erasing). Default 整笔: it is what shipped first and
   *  what a pen's barrel button should keep doing regardless of this pick. */
  inkEraseMode: EraseMode;
  setInkEraseMode: (m: EraseMode) => void;
  /** Undo (past) and redo (future) stacks, oldest operation first. */
  inkPast: InkOp[];
  inkFuture: InkOp[];
  /** Which document the stacks belong to — switching papers/kinds resets. */
  inkOpsKey: string;
  /** Record finished operations; a key change discards the old document's stacks. */
  pushInkOps: (key: string, ops: InkOp[]) => void;
  /**
   * Point every op in BOTH stacks at a row that has just been recreated.
   *
   * Undo/redo re-creates rows through the API, and the row that comes back
   * has a new id. The op being moved between stacks is rewritten with it
   * (that much always worked), but any OTHER op still holding the old id is
   * left naming a row the server has never heard of — and its delete then
   * silently 404s while its redo happily creates a SECOND copy.
   *
   * Concretely: place a strip, delete it, undo (it comes back with a new id),
   * undo again — the placement op still names the original id, so the strip
   * does not go away, and redo duplicates it. Same shape applies to strokes
   * recreated by an eraser undo. So the rename is applied across the whole
   * history, not just to the op in flight.
   */
  remapInkRow: (oldId: string, next: InkStrokeRow | TapeRow) => void;
  /** Drop the stacks on document switch or when the cache is invalidated away. */
  resetInkOps: () => void;

  /** 胶带: size a NEW strip's thickness from the text line under the drag,
   *  rather than a fixed default. A global preference, not per-strip — it
   *  only ever affects strips placed while it is on. */
  tapeAutoThickness: boolean;
  toggleTapeAutoThickness: () => void;

  /**
   * What 剪切/复制 put aside, ready for 粘贴.
   *
   * In the store, not in a component: the lasso's toolbar unmounts the moment
   * the selection clears — which is exactly what 剪切 does — so anything held
   * in its own state would be gone before there was anything to paste it
   * into. Held as payloads (points, colours, paths), never as ids: a paste
   * makes NEW rows, and may well make them on a different page from the one
   * the copy came off.
   *
   * Coordinates are relative to the copied selection's own top-left corner,
   * so a paste can be placed anywhere without having to remember where it was
   * cut from.
   */
  inkClipboard: InkClipboard | null;
  setInkClipboard: (c: InkClipboard | null) => void;

  /* --------------------------------------------------------- 文本框 / 便利贴 */
  /** Which presentation the 文本 tool places: characters on the paper, or a
   *  tinted card on top of it. The long-press menu offers both explicitly and
   *  ignores this. */
  noteStyle: PageNoteStyle;
  setNoteStyle: (s: PageNoteStyle) => void;
  /**
   * Ink token for new notes — the glyph colour of a 文本框, the CARD TINT of a
   * 便利贴.
   *
   * Because those are two different jobs, the two styles remember two values,
   * exactly as the pen and the watercolour do (`toolPrefs`): black glyphs are
   * right for a text box and would make an invisible sticky note, while amber
   * is a sticky note and unreadable handwriting. `setNoteStyle` swaps the live
   * value out to and in from `noteColors`, so nothing downstream has to know
   * there are two.
   */
  noteColor: string;
  setNoteColor: (c: string) => void;
  /** Where the style not currently selected parks its colour. */
  noteColors: Record<PageNoteStyle, string>;
  /**
   * A note that has just been created and should receive the caret.
   *
   * In the store because the thing that creates a note is not the thing that
   * renders it: a long-press is detected in `InkLayer`, the toolbar tap in
   * `ReadingView`, and the `<textarea>` lives in `NoteLayer`. Cleared by
   * whoever focuses it, so it can never re-steal the caret on a later render.
   */
  noteFocusId: string | null;
  setNoteFocusId: (id: string | null | ((cur: string | null) => string | null)) => void;

  /**
   * Show what the stylus is actually reporting, live, on the page.
   *
   * A debugging affordance in shipped UI, and deliberately so: the S Pen's
   * button has now failed twice on a device none of this code can be run
   * against, and every fix has been a guess at what Android hands the WebView.
   * `pointerType`, `button`, `buttons` and `pressure` on screen turn a third
   * guess into a reading. Off by default and tucked into the eraser's own
   * popover, where someone looking for "why doesn't my pen erase" will be.
   */
  inkPenDebug: boolean;
  toggleInkPenDebug: () => void;
  /** The last stylus event, formatted for that readout. Written by `InkLayer`
   *  (which sees the events) and rendered by `ReadingView` (which has the
   *  toolbar) — and only while `inkPenDebug` is on, so the write rate is a
   *  non-issue the rest of the time. */
  inkPenProbe: string | null;
  setInkPenProbe: (probe: string | null) => void;

  /* ------------------------------------------------------------ settings */
  settingsOpen: boolean;
  settingsTab: SettingsTab;
  zoteroOAuthResult: ZoteroOAuthResult | null;
  setZoteroOAuthResult: (result: ZoteroOAuthResult | null) => void;
  openSettings: (tab?: SettingsTab) => void;
  closeSettings: () => void;
  setSettingsTab: (t: SettingsTab) => void;
}

/**
 * Is the AI 对话 panel showing? While the preference is "auto" it follows the
 * window width (the prototype's 1200px breakpoint); once the user toggles it
 * explicitly, their choice sticks.
 */
export function isAiOpen(s: Pick<UIState, "aiOpenPref" | "winW">): boolean {
  return s.aiOpenPref === "auto" ? s.winW >= 1200 : s.aiOpenPref;
}

/**
 * Above this width a touch device gets the classic three-pane library; below
 * it the 详情 panel becomes a slide-over.
 *
 * The number is the sum the three columns need to stay legible: at 1040px the
 * 178px expanded rail + 196px 分类树 + 280px 详情 leave ~386px of list, which
 * still reads; below that the list is the pane that starves. iPad landscape
 * (1024px) deliberately lands on the overlay side — a 370px list with touch
 * row heights loses to a 550px list plus a tap-to-open panel.
 */
export const DETAIL_OVERLAY_MAX_WIDTH = 1040;

/** Pure so both the components and the tests can evaluate the same fork. */
export function isDetailOverlay(s: Pick<UIState, "pointerCoarse" | "winW">): boolean {
  return s.pointerCoarse && s.winW < DETAIL_OVERLAY_MAX_WIDTH;
}

export const useUI = create<UIState>((set) => ({
  // One read, used twice: the preference and the theme it resolves to are two
  // views of the same stored value and must never be able to disagree.
  themePref: INITIAL_THEME_PREF,
  theme: resolveTheme(INITIAL_THEME_PREF, systemPrefersDark()),
  // Storage key is versioned because the palette was rebranded: the previous
  // default, "indigo", is indistinguishable from a deliberate choice once it
  // is in localStorage, so every existing browser would have kept showing the
  // pre-brand accent forever. Bumping the key retires those values once and
  // lets the brand default apply; a user who re-picks indigo keeps it.
  accent: (ls("ph-accent-v2") as AccentKey) ?? "pharos",
  setTheme: (themePref) => {
    save(THEME_KEY, themePref);
    set({ themePref, theme: resolveTheme(themePref, systemPrefersDark()) });
  },
  setAccent: (accent) => {
    save("ph-accent-v2", accent);
    set({ accent });
  },

  activeModule: "library",
  setModule: (activeModule) => set({ activeModule }),
  // Versioned once to retire the old collapsed-by-default preference. Every
  // existing browser receives the corrected expanded state on this release;
  // after that, only an explicit user collapse ("0") starts compact.
  railExpanded: ls(RAIL_EXPANDED_KEY) !== "0",
  toggleRail: () =>
    set((s) => {
      save(RAIL_EXPANDED_KEY, s.railExpanded ? "0" : "1");
      return { railExpanded: !s.railExpanded };
    }),
  railWidth: initialRailWidth(),
  setRailWidth: (width) => {
    const railWidth = clampRailWidth(width);
    save(RAIL_WIDTH_KEY, String(railWidth));
    set({ railWidth });
  },
  resetRailWidth: () => {
    save(RAIL_WIDTH_KEY, String(RAIL_DEFAULT_WIDTH));
    set({ railWidth: RAIL_DEFAULT_WIDTH });
  },

  selectedCol: "lib",
  selectCol: (selectedCol) => set({ selectedCol }),
  favOpen: true,
  toggleGroup: (g) => set((s) => ({ [g]: !s[g] }) as Pick<UIState, typeof g>),
  selectedTags: [],
  toggleTag: (t) =>
    set((s) => ({
      selectedTags: s.selectedTags.includes(t)
        ? s.selectedTags.filter((x) => x !== t)
        : [...s.selectedTags, t],
    })),

  query: "",
  setQuery: (query) => set({ query }),
  arxivInput: "",
  setArxivInput: (arxivInput) => set({ arxivInput }),
  sortCol: "year",
  sortDir: "desc",
  setSort: (col) =>
    set((s) => ({
      sortCol: col,
      sortDir: s.sortCol === col ? (s.sortDir === "asc" ? "desc" : "asc") : "asc",
    })),
  selectedIds: [],
  selectedPaperId: null,
  lastClick: null,
  libDetailOpen: false,
  setLibDetail: (libDetailOpen) => set({ libDetailOpen }),
  selectRow: (id, order, mods) =>
    set((s) => {
      let sel: string[];
      if (mods.meta) {
        sel = s.selectedIds.includes(id)
          ? s.selectedIds.filter((x) => x !== id)
          : [...s.selectedIds, id];
      } else if (mods.shift && s.lastClick) {
        const a = order.indexOf(s.lastClick);
        const b = order.indexOf(id);
        if (a >= 0 && b >= 0) sel = order.slice(Math.min(a, b), Math.max(a, b) + 1);
        else sel = [id];
      } else {
        sel = [id];
      }
      return { selectedIds: sel, selectedPaperId: id, lastClick: id };
    }),

  dailyDate: null,
  // Papers belong to exactly one date, so a stale selection could never be
  // rendered after the date changes — clear it rather than leave it dangling.
  setDailyDate: (dailyDate) => set({ dailyDate, dailyPaperId: null }),
  dailyPaperId: null,
  setDailyPaper: (dailyPaperId) => set({ dailyPaperId }),

  activeProjectId: null,
  setActiveProject: (activeProjectId) => set({ activeProjectId }),

  tabs: [{ id: "library", kind: "library" }],
  activeTabId: "library",
  openPaper: (paperId) =>
    set((s) => {
      const tabId = `t-${paperId}`;
      const exists = s.tabs.some((t) => t.id === tabId);
      return {
        tabs: exists ? s.tabs : [...s.tabs, { id: tabId, kind: "paper", paperId }],
        activeTabId: tabId,
      };
    }),
  setTab: (activeTabId) => set({ activeTabId }),
  closeTab: (id) =>
    set((s) => {
      const idx = s.tabs.findIndex((t) => t.id === id);
      const tabs = s.tabs.filter((t) => t.id !== id);
      let activeTabId = s.activeTabId;
      if (activeTabId === id) activeTabId = (tabs[idx - 1] ?? tabs[0] ?? { id: "library" }).id;
      return { tabs, activeTabId };
    }),

  readMode: "bilingual",
  setReadMode: (readMode) => set({ readMode }),
  outlineOpen: true,
  toggleOutline: () => set((s) => ({ outlineOpen: !s.outlineOpen })),
  outlineMode: "outline",
  setOutlineMode: (outlineMode) => set({ outlineMode }),
  aiOpenPref: "auto",
  winW: typeof window !== "undefined" ? window.innerWidth : 1440,
  setWinW: (winW) => set({ winW }),
  toggleAI: () => set((s) => ({ aiOpenPref: !isAiOpen(s) })),
  openAI: () => set({ aiOpenPref: true }),
  aiPrompt: null,
  setAiPrompt: (aiPrompt) => set({ aiPrompt }),
  pointerCoarse: hasCoarsePointer(),

  inkMode: "off",
  toolPrefs: { ...DEFAULT_TOOL_PREFS },
  inkTray: null,
  setInkTray: (inkTray) => set({ inkTray }),
  inkCarried: [],
  setInkCarried: (inkCarried) => set({ inkCarried }),
  // The pen is the point of the feature, but a mouse should also be able to
  // draw when the tool is on — defaulting to erase would be a trap.
  //
  // Switching tools parks the outgoing tool's colour/width and loads the
  // incoming one's, so a pen stays thin and a wash stays broad without either
  // toolbar button having to "fix up" the other's leftovers.
  setInkMode: (inkMode) =>
    set((s) => {
      if (inkMode === s.inkMode) return { inkMode };
      const toolPrefs = toolRemembers(s.inkMode)
        ? { ...s.toolPrefs, [s.inkMode]: { color: s.inkColor, width: s.inkWidth } }
        : s.toolPrefs;
      if (!toolRemembers(inkMode)) return { inkMode, toolPrefs };
      const next = toolPrefs[inkMode] ?? DEFAULT_TOOL_PREFS[inkMode]!;
      return { inkMode, toolPrefs, inkColor: next.color, inkWidth: next.width };
    }),
  inkColor: "ink",
  // Picking a colour IS the usage signal the quick-bar ranking runs on — no
  // separate "log this" call for every caller to remember to make.
  setInkColor: (inkColor) =>
    set((s) => {
      const prior = s.inkColorUsage[inkColor];
      const inkColorUsage = {
        ...s.inkColorUsage,
        [inkColor]: { count: (prior?.count ?? 0) + 1, last: Date.now() },
      };
      save(INK_COLOR_USAGE_KEY, JSON.stringify(inkColorUsage));
      return { inkColor, inkColorUsage };
    }),
  inkColorUsage: initialInkColorUsage(),
  inkWidth: 2,
  setInkWidth: (inkWidth) => set({ inkWidth: clampInkWidth(inkWidth) }),
  inkFingerDraw: false,
  toggleInkFingerDraw: () => set((s) => ({ inkFingerDraw: !s.inkFingerDraw })),
  inkSound: ls(INK_SOUND_KEY) === "1",
  toggleInkSound: () =>
    set((s) => {
      const inkSound = !s.inkSound;
      save(INK_SOUND_KEY, inkSound ? "1" : "0");
      return { inkSound };
    }),
  inkEraserSize: 16,
  setInkEraserSize: (inkEraserSize) => set({ inkEraserSize }),
  inkEraseMode: "stroke",
  setInkEraseMode: (inkEraseMode) => set({ inkEraseMode }),
  inkPast: [],
  inkFuture: [],
  inkOpsKey: "",
  pushInkOps: (inkOpsKey, ops) =>
    set((s) => {
      if (s.inkOpsKey !== inkOpsKey) return { inkOpsKey, inkPast: capHistory(ops), inkFuture: [] };
      return { inkPast: capHistory([...s.inkPast, ...ops]), inkFuture: [] };
    }),
  remapInkRow: (oldId, next) =>
    set((s) => ({
      inkPast: s.inkPast.map((op) => remapOp(op, oldId, next)),
      inkFuture: s.inkFuture.map((op) => remapOp(op, oldId, next)),
    })),
  resetInkOps: () => set({ inkPast: [], inkFuture: [], inkOpsKey: "" }),

  tapeAutoThickness: false,
  toggleTapeAutoThickness: () => set((s) => ({ tapeAutoThickness: !s.tapeAutoThickness })),

  inkClipboard: null,
  setInkClipboard: (inkClipboard) => set({ inkClipboard }),
  noteStyle: "text",
  setNoteStyle: (noteStyle) =>
    set((s) =>
      noteStyle === s.noteStyle
        ? { noteStyle }
        : {
            noteStyle,
            noteColors: { ...s.noteColors, [s.noteStyle]: s.noteColor },
            noteColor: s.noteColors[noteStyle],
          },
    ),
  noteColor: DEFAULT_NOTE_COLORS.text,
  setNoteColor: (noteColor) => set({ noteColor }),
  noteColors: { ...DEFAULT_NOTE_COLORS },
  noteFocusId: null,
  setNoteFocusId: (noteFocusId) =>
    set((s) => ({
      noteFocusId: typeof noteFocusId === "function" ? noteFocusId(s.noteFocusId) : noteFocusId,
    })),
  inkPenDebug: false,
  toggleInkPenDebug: () =>
    set((s) => ({ inkPenDebug: !s.inkPenDebug, inkPenProbe: null })),
  inkPenProbe: null,
  setInkPenProbe: (inkPenProbe) => set({ inkPenProbe }),

  settingsOpen: false,
  settingsTab: "account",
  zoteroOAuthResult: null,
  setZoteroOAuthResult: (zoteroOAuthResult) => set({ zoteroOAuthResult }),
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab ?? "account" }),
  closeSettings: () => set({ settingsOpen: false }),
  setSettingsTab: (settingsTab) => set({ settingsTab }),
}));

/* The OS flipping to dark (or back) while 跟随系统 is the chosen preference.
   One subscription for the app's lifetime rather than an effect in a component:
   the resolved theme is read by App, by AuthGate — which are two separate trees
   — and by every accent swatch, so a listener owned by one of them would leave
   the others painting the previous scheme. `resolveTheme` re-reads the stored
   preference on every event, so a user who picks 浅色 or 深色 in the meantime
   keeps it and the OS is simply ignored from then on. */
darkQuery()?.addEventListener("change", (event) => {
  useUI.setState((s) => ({ theme: resolveTheme(s.themePref, event.matches) }));
});

/* Same story for a mouse being plugged into (or a touch digitiser leaving) an
   Android tablet: the library layout fork reads `pointerCoarse`, so the store
   has to notice rather than freeze at the boot-time match. */
coarseQuery()?.addEventListener("change", (event) => {
  useUI.setState({ pointerCoarse: event.matches });
});

/* ======================================================================= */
/*                                session                                  */
/* ======================================================================= */

/**
 * Who is signed in, for rendering.
 *
 * Deliberately a second store rather than fields on `useUI`: this is not UI
 * state, it does not reset with the workbench, and giving it its own hook means
 * a component that only needs the user does not re-render when a tab changes.
 *
 * READ-ONLY BY DESIGN. `auth/session.ts` owns the session, because `api/client.ts`
 * has to read the token and clear it on a 401 from outside React entirely. This
 * store has no actions on purpose: a second way to write the token is a second
 * way for the token and the localStorage copy to disagree, and on a gate whose
 * whole job is "is this person allowed in", disagreeing states are the bug you
 * cannot afford. To change the session, call `api.auth.*` (login, register,
 * logout, logoutAll) or `auth/session.ts` directly — the change lands here.
 */
interface SessionState {
  /** Null = signed out. AuthGate renders sign-in on exactly this condition. */
  token: string | null;
  /** May be null while a stored token is still being validated on a cold start. */
  user: AuthUser | null;
}

/**
 * Does this account get whole-PDF translation?
 *
 * Server-owned and per-account — deliberately NOT a localStorage key alongside
 * `ph-theme`. The theme is a property of this screen; this is a property of the
 * person. A per-device copy would disagree with the server the moment they open
 * Pharos in another browser, and it would disagree in the expensive direction:
 * a stale "on" keeps offering 翻译此篇 for an account that has turned it off,
 * and every press spends API budget the user thought they had stopped spending.
 * So it rides in on the session user — refreshed by the `GET /auth/me` the gate
 * makes on every cold start, and by the PATCH response when it is changed —
 * which is why this is a selector over `useSession` rather than state of its own.
 *
 * Absent reads as ON, and only an explicit `false` turns the apparatus off.
 * There are two ways it can arrive absent: a session cached by a build that
 * predates the field, and the legacy NULL the backend's additive migration
 * leaves on rows that existed before the column did. In both the account's real
 * setting is the default, `true` — so this mirrors `pdf_translation_enabled()`
 * in `pharos/api/auth.py` exactly, rather than letting a missing field quietly
 * hide a feature the user actually has.
 */
export function pdfTranslationEnabled(s: Pick<SessionState, "user">): boolean {
  return s.user?.pdf_translation !== false;
}

const sessionSnapshot = (): SessionState => {
  const s = getSession();
  return { token: s.token, user: s.user };
};

export const useSession = create<SessionState>(() => sessionSnapshot());

// One subscription for the app's lifetime: every change to the underlying
// session — sign-in, sign-out, a 401 handled deep inside client.ts, or another
// tab signing out — pushes into the store and re-renders the gate.
subscribeSession(() => useSession.setState(sessionSnapshot()));

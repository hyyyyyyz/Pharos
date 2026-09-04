import { create } from "zustand";
import { resolveTheme } from "./design/tokens";
import type { AccentKey, ThemeMode, ThemePref } from "./design/tokens";
import type { AuthUser, ZoteroOAuthResult } from "./api/types";
import { getSession, subscribe as subscribeSession } from "./auth/session";

export type ModuleKey = "library" | "daily" | "search" | "kb" | "writing" | "runs" | "admin";
/** Modules that are actually built. Everything else falls through to <ComingSoon />. */
export type LiveModuleKey = "library" | "daily" | "search" | "kb" | "runs" | "admin";
export type ReadMode = "zh" | "bilingual" | "original";
export type OutlineMode = "outline" | "thumbs";
export type SettingsTab = "account" | "ai" | "appearance" | "daily";
export type SortCol = "title" | "authors" | "year" | "pages" | "status";
export type SortDir = "asc" | "desc";

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
  /** Live `(pointer: coarse)` match — the tablet/desktop fork for layouts. */
  pointerCoarse: boolean;

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
  pointerCoarse: hasCoarsePointer(),

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

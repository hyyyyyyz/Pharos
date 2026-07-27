import { create } from "zustand";
import type { AccentKey, ThemeMode } from "./design/tokens";
import type { AuthUser } from "./api/types";
import { getSession, subscribe as subscribeSession } from "./auth/session";

export type ModuleKey = "library" | "daily" | "search" | "kb" | "writing";
/** Modules that are actually built. Everything else falls through to <ComingSoon />. */
export type LiveModuleKey = "library" | "daily" | "search" | "kb";
export type ReadMode = "zh" | "bilingual" | "original";
export type OutlineMode = "outline" | "thumbs";
export type SettingsTab = "account" | "appearance" | "daily";
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
  | { id: string; kind: "paper"; paperId: string; localAttachmentId?: string };

const ls = (key: string): string | null =>
  typeof localStorage !== "undefined" ? localStorage.getItem(key) : null;

const save = (key: string, value: string) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode — appearance just won't persist */
  }
};

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
  theme: ThemeMode;
  accent: AccentKey;
  setTheme: (t: ThemeMode) => void;
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
  zoteroOpen: boolean;
  toggleGroup: (g: "favOpen" | "zoteroOpen") => void;
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
  openPaper: (paperId: string, localAttachmentId?: string) => void;
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

  /* ------------------------------------------------------------ settings */
  settingsOpen: boolean;
  settingsTab: SettingsTab;
  openSettings: (tab?: SettingsTab) => void;
  closeSettings: () => void;
  setSettingsTab: (t: SettingsTab) => void;
}

/**
 * Is the 领航 panel showing? While the preference is "auto" it follows the
 * window width (the prototype's 1200px breakpoint); once the user toggles it
 * explicitly, their choice sticks.
 */
export function isAiOpen(s: Pick<UIState, "aiOpenPref" | "winW">): boolean {
  return s.aiOpenPref === "auto" ? s.winW >= 1200 : s.aiOpenPref;
}

export const useUI = create<UIState>((set) => ({
  theme: (ls("ph-theme") as ThemeMode) ?? "light",
  // Storage key is versioned because the palette was rebranded: the previous
  // default, "indigo", is indistinguishable from a deliberate choice once it
  // is in localStorage, so every existing browser would have kept showing the
  // pre-brand accent forever. Bumping the key retires those values once and
  // lets the brand default apply; a user who re-picks indigo keeps it.
  accent: (ls("ph-accent-v2") as AccentKey) ?? "pharos",
  setTheme: (theme) => {
    save("ph-theme", theme);
    set({ theme });
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
  zoteroOpen: true,
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
  openPaper: (paperId, localAttachmentId) =>
    set((s) => {
      const tabId = localAttachmentId
        ? `t-${paperId}-${localAttachmentId}`
        : `t-${paperId}`;
      const exists = s.tabs.some((t) => t.id === tabId);
      return {
        tabs: exists
          ? s.tabs
          : [...s.tabs, { id: tabId, kind: "paper", paperId, localAttachmentId }],
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

  settingsOpen: false,
  settingsTab: "account",
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab ?? "account" }),
  closeSettings: () => set({ settingsOpen: false }),
  setSettingsTab: (settingsTab) => set({ settingsTab }),
}));

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

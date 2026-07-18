import { create } from "zustand";
import type { AccentKey, ThemeMode } from "./design/tokens";

export type ModuleKey = "library" | "search" | "kb" | "writing";
export type ReadMode = "zh" | "bilingual" | "original";
export type OutlineMode = "outline" | "thumbs";
export type SettingsTab = "account" | "appearance";
export type SortCol = "title" | "authors" | "year" | "pages" | "status";
export type SortDir = "asc" | "desc";

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
  accent: (ls("ph-accent") as AccentKey) ?? "indigo",
  setTheme: (theme) => {
    save("ph-theme", theme);
    set({ theme });
  },
  setAccent: (accent) => {
    save("ph-accent", accent);
    set({ accent });
  },

  activeModule: "library",
  setModule: (activeModule) => set({ activeModule }),
  railExpanded: ls("ph-rail") === "1",
  toggleRail: () =>
    set((s) => {
      save("ph-rail", s.railExpanded ? "0" : "1");
      return { railExpanded: !s.railExpanded };
    }),

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

  settingsOpen: false,
  settingsTab: "account",
  openSettings: (tab) => set({ settingsOpen: true, settingsTab: tab ?? "account" }),
  closeSettings: () => set({ settingsOpen: false }),
  setSettingsTab: (settingsTab) => set({ settingsTab }),
}));

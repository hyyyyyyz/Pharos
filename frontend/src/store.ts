import { create } from "zustand";

export type ReaderKind = "mono" | "dual" | "original";
export type View = "landing" | "read";
export type Mode = "light" | "dark";
export type AccentKey =
  | "mint"
  | "sky"
  | "emerald"
  | "indigo"
  | "violet"
  | "rose"
  | "amber"
  | "slate";

const load = (key: string, fallback: string): string =>
  (typeof localStorage !== "undefined" ? localStorage.getItem(key) : null) ?? fallback;

interface UIState {
  view: View;
  setView: (v: View) => void;

  // appearance (shared, single source of truth)
  mode: Mode;
  setMode: (m: Mode) => void;
  toggleMode: () => void;
  accent: AccentKey;
  setAccent: (a: AccentKey) => void;

  selectedPaperId: string | null;
  select: (id: string | null) => void;
  readerKind: ReaderKind;
  setReaderKind: (kind: ReaderKind) => void;
  chatOpen: boolean;
  toggleChat: () => void;
}

export const useUI = create<UIState>((set) => ({
  view: "landing",
  setView: (view) => set({ view }),

  mode: load("ph-mode", "light") as Mode,
  setMode: (mode) => set({ mode }),
  toggleMode: () => set((s) => ({ mode: s.mode === "dark" ? "light" : "dark" })),
  accent: load("ph-accent", "mint") as AccentKey,
  setAccent: (accent) => set({ accent }),

  selectedPaperId: null,
  select: (id) => set({ selectedPaperId: id }),
  readerKind: "mono",
  setReaderKind: (readerKind) => set({ readerKind }),
  chatOpen: true,
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
}));

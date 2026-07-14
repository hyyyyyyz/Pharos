import { create } from "zustand";

export type ReaderKind = "mono" | "dual" | "original";

interface UIState {
  selectedPaperId: string | null;
  select: (id: string | null) => void;
  readerKind: ReaderKind;
  setReaderKind: (kind: ReaderKind) => void;
  chatOpen: boolean;
  toggleChat: () => void;
}

export const useUI = create<UIState>((set) => ({
  selectedPaperId: null,
  select: (id) => set({ selectedPaperId: id }),
  readerKind: "mono",
  setReaderKind: (readerKind) => set({ readerKind }),
  chatOpen: true,
  toggleChat: () => set((s) => ({ chatOpen: !s.chatOpen })),
}));

import { invoke, isTauri } from "@tauri-apps/api/core";

import type { PdfSource } from "../api/client";

export const LOCAL_ZOTERO_COLLECTION_ID = "zotero-local";
export const LOCAL_ZOTERO_ID_PREFIX = "zotero-local-";

export interface LocalZoteroLibrary {
  id: string;
  kind: "personal" | "group";
  name: string;
  paperCount: number;
  pdfAvailableCount: number;
}

export interface LocalZoteroStatus {
  available: boolean;
  syncing: boolean;
  cachedPaperCount: number;
  pdfAvailableCount: number;
  lastSuccessfulSyncMs: number | null;
  zoteroVersion: number | null;
  lastError: string | null;
  libraries: LocalZoteroLibrary[];
}

export interface LocalZoteroPaper {
  id: string;
  libraryId: string;
  libraryKind: "personal" | "group";
  libraryName: string;
  itemKey: string;
  itemVersion: number;
  itemType: string;
  title: string;
  authors: string[];
  year: number | null;
  venue: string | null;
  doi: string | null;
  abstractText: string | null;
  url: string | null;
  dateAdded: string | null;
  pdfAvailable: boolean;
  pdfAttachmentId: string | null;
  pdfFilename: string | null;
  pdfAttachmentCount: number;
}

export const localZoteroAvailable = (): boolean => isTauri();

export const isLocalZoteroPaperId = (value: string | null | undefined): boolean =>
  typeof value === "string" && value.startsWith(LOCAL_ZOTERO_ID_PREFIX);

const desktopOnly = (): void => {
  if (!isTauri()) throw new Error("本机 Zotero 仅在桌面客户端中可用。");
};

export const localZotero = {
  status: async (): Promise<LocalZoteroStatus> => {
    desktopOnly();
    return invoke<LocalZoteroStatus>("zotero_local_status");
  },

  sync: async (): Promise<LocalZoteroStatus> => {
    desktopOnly();
    return invoke<LocalZoteroStatus>("zotero_local_sync");
  },

  list: async (): Promise<LocalZoteroPaper[]> => {
    desktopOnly();
    return invoke<LocalZoteroPaper[]>("zotero_local_list");
  },

  get: async (paperId: string): Promise<LocalZoteroPaper> => {
    desktopOnly();
    return invoke<LocalZoteroPaper>("zotero_local_get", { paperId });
  },

  pdfSource: async (attachmentId: string): Promise<PdfSource> => {
    desktopOnly();
    const url = await invoke<string>("zotero_local_pdf_url", { attachmentId });
    return { url, httpHeaders: {} };
  },

  pdfFile: async (paper: LocalZoteroPaper): Promise<File> => {
    desktopOnly();
    if (!paper.pdfAttachmentId) throw new Error("这篇文献没有可用的本地 PDF。");
    const raw = await invoke<ArrayBuffer | Uint8Array | number[]>("zotero_local_pdf_bytes", {
      attachmentId: paper.pdfAttachmentId,
    });
    const bytes =
      raw instanceof ArrayBuffer
        ? new Uint8Array(raw)
        : raw instanceof Uint8Array
          ? raw
          : new Uint8Array(raw);
    // TS's typed-array generic permits SharedArrayBuffer, while File accepts
    // only an ordinary ArrayBuffer view. Copy once at this explicit upload
    // boundary; normal reading uses the range protocol and does not copy.
    const owned = new Uint8Array(bytes.byteLength);
    owned.set(bytes);
    const fallback = `${paper.title || "paper"}.pdf`;
    const filename = (paper.pdfFilename || fallback).replace(/[\\/:*?"<>|]/g, "_");
    return new File([owned.buffer], filename, { type: "application/pdf" });
  },
};

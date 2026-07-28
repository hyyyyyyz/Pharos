import { invoke, isTauri } from "@tauri-apps/api/core";

import type {
  ZoteroCollection,
  ZoteroConnectionStatus,
  ZoteroFulltext,
  ZoteroItemDetail,
  ZoteroItemQuery,
  ZoteroItemRef,
  ZoteroItemSummary,
  ZoteroLibrary,
  ZoteroLibraryRef,
  ZoteroPage,
  ZoteroSavedSearch,
  ZoteroSyncReport,
  ZoteroTag,
} from "../types/zotero";

export const ZOTERO_ITEM_ID_PREFIX = "zotero-item:";
export const ZOTERO_LIBRARY_NODE_PREFIX = "zotero-library:";
export const ZOTERO_COLLECTION_NODE_PREFIX = "zotero-collection:";

const desktopOnly = (): void => {
  if (!isTauri()) throw new Error("本机 Zotero 仅在 Pharos 桌面客户端中可用。");
};

const encodeParts = (...parts: string[]): string =>
  parts.map((part) => encodeURIComponent(part)).join("/");

const decodeParts = (value: string): string[] | null => {
  try {
    return value.split("/").map((part) => decodeURIComponent(part));
  } catch {
    return null;
  }
};

export const zoteroItemId = (item: ZoteroItemRef): string =>
  `${ZOTERO_ITEM_ID_PREFIX}${encodeParts(item.sourceId, item.libraryId, item.itemKey)}`;

export const parseZoteroItemId = (id: string | null | undefined): ZoteroItemRef | null => {
  if (!id?.startsWith(ZOTERO_ITEM_ID_PREFIX)) return null;
  const parts = decodeParts(id.slice(ZOTERO_ITEM_ID_PREFIX.length));
  if (parts?.length !== 3 || parts.some((part) => part === "")) return null;
  return { sourceId: parts[0], libraryId: parts[1], itemKey: parts[2] };
};

export const zoteroLibraryNodeId = (library: ZoteroLibraryRef): string =>
  `${ZOTERO_LIBRARY_NODE_PREFIX}${encodeParts(library.sourceId, library.libraryId)}`;

export const parseZoteroLibraryNodeId = (
  id: string | null | undefined,
): ZoteroLibraryRef | null => {
  if (!id?.startsWith(ZOTERO_LIBRARY_NODE_PREFIX)) return null;
  const parts = decodeParts(id.slice(ZOTERO_LIBRARY_NODE_PREFIX.length));
  if (parts?.length !== 2 || parts.some((part) => part === "")) return null;
  return { sourceId: parts[0], libraryId: parts[1] };
};

export const zoteroCollectionNodeId = (
  library: ZoteroLibraryRef,
  collectionKey: string,
): string =>
  `${ZOTERO_COLLECTION_NODE_PREFIX}${encodeParts(
    library.sourceId,
    library.libraryId,
    collectionKey,
  )}`;

export const parseZoteroCollectionNodeId = (
  id: string | null | undefined,
): { library: ZoteroLibraryRef; collectionKey: string } | null => {
  if (!id?.startsWith(ZOTERO_COLLECTION_NODE_PREFIX)) return null;
  const parts = decodeParts(id.slice(ZOTERO_COLLECTION_NODE_PREFIX.length));
  if (parts?.length !== 3 || parts.some((part) => part === "")) return null;
  return {
    library: { sourceId: parts[0], libraryId: parts[1] },
    collectionKey: parts[2],
  };
};

export const zoteroAvailable = (): boolean => isTauri();

export const zotero = {
  status: async (): Promise<ZoteroConnectionStatus> => {
    desktopOnly();
    return invoke<ZoteroConnectionStatus>("zotero_connection_status");
  },

  refresh: async (forceFull = false): Promise<ZoteroSyncReport> => {
    desktopOnly();
    return invoke<ZoteroSyncReport>("zotero_refresh", {
      request: { forceFull },
    });
  },

  libraries: async (): Promise<ZoteroLibrary[]> => {
    desktopOnly();
    return invoke<ZoteroLibrary[]>("zotero_list_libraries");
  },

  collections: async (library: ZoteroLibraryRef): Promise<ZoteroCollection[]> => {
    desktopOnly();
    return invoke<ZoteroCollection[]>("zotero_list_collections", { library });
  },

  queryItems: async (query: ZoteroItemQuery = {}): Promise<ZoteroPage<ZoteroItemSummary>> => {
    desktopOnly();
    return invoke<ZoteroPage<ZoteroItemSummary>>("zotero_query_items", { query });
  },

  item: async (item: ZoteroItemRef): Promise<ZoteroItemDetail> => {
    desktopOnly();
    return invoke<ZoteroItemDetail>("zotero_get_item", { item });
  },

  children: async (item: ZoteroItemRef): Promise<ZoteroItemSummary[]> => {
    desktopOnly();
    return invoke<ZoteroItemSummary[]>("zotero_list_item_children", { item });
  },

  tags: async (library: ZoteroLibraryRef): Promise<ZoteroTag[]> => {
    desktopOnly();
    return invoke<ZoteroTag[]>("zotero_list_tags", { library });
  },

  savedSearches: async (library: ZoteroLibraryRef): Promise<ZoteroSavedSearch[]> => {
    desktopOnly();
    return invoke<ZoteroSavedSearch[]>("zotero_list_saved_searches", { library });
  },

  fulltext: async (item: ZoteroItemRef): Promise<ZoteroFulltext | null> => {
    desktopOnly();
    return invoke<ZoteroFulltext | null>("zotero_get_fulltext", { item });
  },

  attachmentUrl: async (attachmentId: string): Promise<string> => {
    desktopOnly();
    return invoke<string>("zotero_get_attachment_url", { attachmentId });
  },
};

export type ZoteroProviderKind = "connector" | "local-api" | "cloud";
export type ZoteroConnectionPhase =
  | "disconnected"
  | "detecting"
  | "connecting"
  | "indexing"
  | "ready"
  | "stale"
  | "error";
export type ZoteroLibraryKind = "user" | "group";

export interface ZoteroProviderCapabilities {
  metadataRead: boolean;
  fileRead: boolean;
  fulltextRead: boolean;
  metadataWrite: boolean;
  notesWrite: boolean;
  annotationsWrite: boolean;
  realtimeEvents: boolean;
}

export interface ZoteroConnectionStatus {
  sourceId: string;
  provider: ZoteroProviderKind;
  phase: ZoteroConnectionPhase;
  capabilities: ZoteroProviderCapabilities;
  available: boolean;
  syncing: boolean;
  zoteroVersion: string | null;
  apiVersion: number | null;
  schemaVersion: number | null;
  lastSuccessfulSyncMs: number | null;
  lastError: string | null;
  libraryCount: number;
  itemCount: number;
}

export interface ZoteroLibraryRef {
  sourceId: string;
  libraryId: string;
}

export interface ZoteroItemRef extends ZoteroLibraryRef {
  itemKey: string;
}

export interface ZoteroLibrary extends ZoteroLibraryRef {
  kind: ZoteroLibraryKind;
  name: string;
  version: number;
  editable: boolean;
  filesEditable: boolean;
  raw: unknown;
}

export interface ZoteroCollection extends ZoteroLibraryRef {
  key: string;
  version: number;
  name: string;
  parentKey: string | null;
  itemCount: number;
  deleted: boolean;
  raw: unknown;
}

export interface ZoteroCreator {
  creatorType: string | null;
  firstName: string | null;
  lastName: string | null;
  name: string | null;
}

export interface ZoteroTagRef {
  tag: string;
  kind: number | null;
}

export interface ZoteroItem extends ZoteroLibraryRef {
  key: string;
  version: number;
  itemType: string;
  parentKey: string | null;
  title: string | null;
  abstractNote: string | null;
  dateAdded: string | null;
  dateModified: string | null;
  creators: ZoteroCreator[];
  tags: ZoteroTagRef[];
  collectionKeys: string[];
  relations: unknown;
  raw: unknown;
  deleted: boolean;
}

export interface ZoteroItemSummary extends ZoteroLibraryRef {
  key: string;
  version: number;
  itemType: string;
  parentKey: string | null;
  title: string | null;
  abstractNote: string | null;
  year: number | null;
  venue: string | null;
  doi: string | null;
  url: string | null;
  dateAdded: string | null;
  dateModified: string | null;
  creators: ZoteroCreator[];
  tags: ZoteroTagRef[];
  collectionKeys: string[];
  deleted: boolean;
  childCount: number;
  attachmentCount: number;
  availableAttachmentCount: number;
}

export interface ZoteroAttachment extends ZoteroLibraryRef {
  key: string;
  version: number;
  parentKey: string | null;
  publicId: string;
  linkMode: string | null;
  contentType: string | null;
  filename: string | null;
  available: boolean;
  sizeBytes: number | null;
  raw: unknown;
}

export interface ZoteroItemDetail {
  item: ZoteroItem;
  attachments: ZoteroAttachment[];
  children: ZoteroItemSummary[];
  annotations: ZoteroItemSummary[];
}

export interface ZoteroTag extends ZoteroLibraryRef {
  tag: string;
  kind: number | null;
  itemCount: number | null;
}

export interface ZoteroSavedSearch extends ZoteroLibraryRef {
  key: string;
  version: number;
  name: string;
  deleted: boolean;
  conditions: unknown;
  raw: unknown;
}

export interface ZoteroFulltext extends ZoteroItemRef {
  version: number;
  content: string;
  indexedPages: number | null;
  totalPages: number | null;
}

export interface ZoteroItemQuery {
  library?: ZoteroLibraryRef | null;
  collectionKey?: string | null;
  parentKey?: string | null;
  itemTypes?: string[];
  tag?: string | null;
  search?: string | null;
  includeDeleted?: boolean;
  limit?: number;
  offset?: number;
}

export interface ZoteroPage<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ZoteroSyncReport {
  sourceId: string;
  provider: ZoteroProviderKind;
  full: boolean;
  libraryCount: number;
  itemCount: number;
  attachmentCount: number;
  collectionCount: number;
  noteCount: number;
  annotationCount: number;
  availableAttachmentCount: number;
  completedAtMs: number;
}

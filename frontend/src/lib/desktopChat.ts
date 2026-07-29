import { Channel, invoke, isTauri } from "@tauri-apps/api/core";

export interface DocumentRef {
  key: string;
  kind: "paper" | "zotero" | "local-pdf" | "codex" | string;
  title: string;
  sourceId?: string | null;
  libraryId?: string | null;
  itemKey?: string | null;
  attachmentId?: string | null;
  paperId?: string | null;
}

export interface DocumentContext {
  title: string;
  authors: string;
  abstractText: string;
  fullText: string;
  currentPage?: number | null;
  pageCount?: number | null;
}

export interface ProviderStatus {
  configured: boolean;
  hasCredential: boolean;
  baseUrl: string;
  model: string;
  temperature: number;
  maxOutputTokens: number | null;
  /** Web only: whether this account overrides the instance provider. */
  source?: "personal" | "server" | "none";
  /** Web only: false when the server lacks credential encryption. */
  canStoreCredential?: boolean;
}

export interface ProviderSaveRequest {
  baseUrl: string;
  model: string;
  temperature: number;
  maxOutputTokens?: number | null;
  apiKey?: string | null;
}

export interface PaperContextStatus {
  documentKey: string;
  status: "preparing" | "indexed" | "understanding" | "ready" | string;
  charCount: number;
  pageCount: number | null;
  hasSummary: boolean;
  summary: string | null;
  error: string | null;
  updatedAtMs: number;
}

export interface ConversationSummary {
  id: string;
  documentKey: string;
  documentKind: string;
  documentTitle: string;
  title: string;
  source: string;
  sourceSessionId: string | null;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestampMs: number;
  model: string | null;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
}

export type ChatEvent =
  | { type: "started"; run_id: string }
  | { type: "delta"; text: string }
  | { type: "done"; message: ChatMessage }
  | { type: "error"; message: string };

export interface CodexSessionSummary {
  path: string;
  sessionId: string | null;
  title: string;
  cwd: string | null;
  updatedAtMs: number;
  messageCount: number;
  truncated: boolean;
  archived: boolean;
}

export interface CodexCapabilities {
  available: boolean;
  version: string | null;
  codexHome: string | null;
  readableRoots: string[];
}

export interface CodexHandoffResult {
  threadId: string;
  cwd: string;
}

export interface WorkspaceStatus {
  root: string;
  configuredRoot: string;
  dailyPath: string;
  workspaceId: string;
  formatVersion: number;
  databaseSchemaVersion: number;
  requiresRestart: boolean;
}

export interface WorkspaceRelocateResult {
  root: string;
  workspaceId: string;
  copied: boolean;
  requiresRestart: boolean;
}

const desktopOnly = (): void => {
  if (!isTauri()) throw new Error("AI 对话的本地数据能力目前仅在 Pharos 客户端中可用。");
};

export const desktopChatAvailable = (): boolean => isTauri();

export const desktopChat = {
  providerStatus: async (): Promise<ProviderStatus> => {
    desktopOnly();
    return invoke<ProviderStatus>("provider_status");
  },

  saveProvider: async (request: ProviderSaveRequest): Promise<ProviderStatus> => {
    desktopOnly();
    return invoke<ProviderStatus>("provider_save", { request });
  },

  clearProvider: async (): Promise<void> => {
    desktopOnly();
    return invoke("provider_clear");
  },

  contextStatus: async (documentRef: DocumentRef): Promise<PaperContextStatus | null> => {
    desktopOnly();
    return invoke<PaperContextStatus | null>("document_context_status", { documentRef });
  },

  prepareContext: async (
    documentRef: DocumentRef,
    context: DocumentContext,
  ): Promise<PaperContextStatus> => {
    desktopOnly();
    return invoke<PaperContextStatus>("document_prepare_context", {
      request: { documentRef, context },
    });
  },

  listConversations: async (documentRef: DocumentRef): Promise<ConversationSummary[]> => {
    desktopOnly();
    return invoke<ConversationSummary[]>("conversation_list", { documentRef });
  },

  createConversation: async (
    documentRef: DocumentRef,
    title?: string,
  ): Promise<ConversationSummary> => {
    desktopOnly();
    return invoke<ConversationSummary>("conversation_create", {
      request: { documentRef, title: title ?? null },
    });
  },

  loadConversation: async (conversationId: string): Promise<ConversationDetail> => {
    desktopOnly();
    return invoke<ConversationDetail>("conversation_load", { conversationId });
  },

  deleteConversation: async (conversationId: string): Promise<void> => {
    desktopOnly();
    return invoke("conversation_delete", { conversationId });
  },

  send: async (
    request: {
      runId: string;
      conversationId: string;
      documentRef: DocumentRef;
      message: string;
      currentContext?: DocumentContext | null;
    },
    onEvent: (event: ChatEvent) => void,
  ): Promise<string> => {
    desktopOnly();
    const channel = new Channel<ChatEvent>();
    channel.onmessage = onEvent;
    return invoke<string>("conversation_send_stream", {
      request,
      onEvent: channel,
    });
  },

  cancel: async (runId: string): Promise<boolean> => {
    desktopOnly();
    return invoke<boolean>("conversation_cancel", { runId });
  },

  codexCapabilities: async (): Promise<CodexCapabilities> => {
    desktopOnly();
    return invoke<CodexCapabilities>("codex_capabilities");
  },

  discoverCodexSessions: async (limit = 40): Promise<CodexSessionSummary[]> => {
    desktopOnly();
    return invoke<CodexSessionSummary[]>("codex_discover_sessions", { limit });
  },

  importCodexSession: async (
    path: string,
    documentRef: DocumentRef,
  ): Promise<ConversationSummary> => {
    desktopOnly();
    return invoke<ConversationSummary>("codex_import_session", {
      path,
      documentRef,
    });
  },

  handoffToCodex: async (
    conversationId: string,
    cwd?: string | null,
  ): Promise<CodexHandoffResult> => {
    desktopOnly();
    return invoke<CodexHandoffResult>("codex_create_handoff", {
      conversationId,
      cwd: cwd ?? null,
    });
  },

  workspaceStatus: async (): Promise<WorkspaceStatus> => {
    desktopOnly();
    return invoke<WorkspaceStatus>("workspace_status");
  },

  relocateWorkspace: async (destination: string): Promise<WorkspaceRelocateResult> => {
    desktopOnly();
    return invoke<WorkspaceRelocateResult>("workspace_relocate", { destination });
  },
};

export function desktopError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "操作失败，请稍后重试。";
}

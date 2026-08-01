import { clearSession, getToken } from "../auth/session";

/**
 * The document a conversation is about.
 *
 * Only papers in the signed-in account's Pharos library qualify: the server
 * indexes and answers from its own copy, so a document it cannot fetch has no
 * conversation to hold.
 */
export interface DocumentRef {
  key: string;
  kind: "paper";
  title: string;
  paperId?: string | null;
}

export interface ProviderStatus {
  configured: boolean;
  hasCredential: boolean;
  baseUrl: string;
  model: string;
  temperature: number;
  maxOutputTokens: number | null;
  /** Whether this account overrides the instance-wide provider. */
  source?: "personal" | "server" | "none";
  /** False when the server lacks credential encryption. */
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

const BASE = import.meta.env.VITE_API_BASE ?? "/api";
const PREPARE_POLL_MS = 1_200;
const PREPARE_TIMEOUT_MS = 180_000;
const controllers = new Map<string, AbortController>();

class WebChatError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "WebChatError";
    this.status = status;
  }
}

const paperIdOf = (documentRef: DocumentRef): string => {
  const paperId = documentRef.paperId?.trim();
  if (!paperId) {
    throw new WebChatError("网页端 AI 对话只能读取已经导入 Pharos 文库的论文。", 400);
  }
  return paperId;
};

const delay = (ms: number): Promise<void> =>
  new Promise((resolve) => window.setTimeout(resolve, ms));

async function request(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  if (!token) throw new WebChatError("登录状态已失效，请重新登录。", 401);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (response.status === 401) clearSession();
  return response;
}

async function failure(response: Response): Promise<WebChatError> {
  let message = response.statusText || "请求失败";
  try {
    const value = (await response.json()) as { detail?: unknown };
    if (typeof value.detail === "string" && value.detail.trim()) message = value.detail;
  } catch {
    /* non-JSON response */
  }
  return new WebChatError(message, response.status);
}

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await request(path, init);
  if (!response.ok) throw await failure(response);
  return (await response.json()) as T;
}

const jsonBody = (value: unknown): Pick<RequestInit, "headers" | "body"> => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(value),
});

async function empty(path: string, init: RequestInit): Promise<void> {
  const response = await request(path, init);
  if (!response.ok) throw await failure(response);
}

async function pollPreparation(
  paperId: string,
  initial: PaperContextStatus,
): Promise<PaperContextStatus> {
  if (initial.status !== "understanding") return initial;
  const deadline = Date.now() + PREPARE_TIMEOUT_MS;
  let latest = initial;
  while (Date.now() < deadline) {
    await delay(PREPARE_POLL_MS);
    const next = await json<PaperContextStatus | null>(`/ai/papers/${encodeURIComponent(paperId)}/context`);
    if (next) latest = next;
    if (latest.status !== "understanding") return latest;
  }
  return {
    ...latest,
    error: latest.error ?? "论文理解仍在服务器后台进行，可以先提问或稍后重新打开。",
  };
}

function parseEvent(line: string): ChatEvent | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed) as ChatEvent;
  } catch {
    return null;
  }
}

async function consumeEvents(
  response: Response,
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  if (!response.body) throw new WebChatError("模型响应没有可读的数据流。", 502);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  for (;;) {
    const { value, done } = await reader.read();
    pending += decoder.decode(value, { stream: !done });
    let lineBreak = pending.indexOf("\n");
    while (lineBreak >= 0) {
      const line = pending.slice(0, lineBreak);
      pending = pending.slice(lineBreak + 1);
      const event = parseEvent(line);
      if (event) onEvent(event);
      lineBreak = pending.indexOf("\n");
    }
    if (done) break;
  }
  const finalEvent = parseEvent(pending);
  if (finalEvent) onEvent(finalEvent);
}

export const webChat = {
  providerStatus: (): Promise<ProviderStatus> => json<ProviderStatus>("/ai/provider"),

  saveProvider: (provider: ProviderSaveRequest): Promise<ProviderStatus> =>
    json<ProviderStatus>("/ai/provider", {
      method: "PUT",
      ...jsonBody(provider),
    }),

  clearProvider: (): Promise<void> => empty("/ai/provider", { method: "DELETE" }),

  contextStatus: async (documentRef: DocumentRef): Promise<PaperContextStatus | null> => {
    const paperId = paperIdOf(documentRef);
    return json<PaperContextStatus | null>(`/ai/papers/${encodeURIComponent(paperId)}/context`);
  },

  prepareContext: async (documentRef: DocumentRef): Promise<PaperContextStatus> => {
    const paperId = paperIdOf(documentRef);
    const initial = await json<PaperContextStatus>(
      `/ai/papers/${encodeURIComponent(paperId)}/prepare`,
      { method: "POST" },
    );
    return pollPreparation(paperId, initial);
  },

  listConversations: (documentRef: DocumentRef): Promise<ConversationSummary[]> => {
    const paperId = paperIdOf(documentRef);
    return json<ConversationSummary[]>(
      `/ai/papers/${encodeURIComponent(paperId)}/conversations`,
    );
  },

  createConversation: (
    documentRef: DocumentRef,
    title?: string,
  ): Promise<ConversationSummary> => {
    const paperId = paperIdOf(documentRef);
    return json<ConversationSummary>(
      `/ai/papers/${encodeURIComponent(paperId)}/conversations`,
      {
        method: "POST",
        ...jsonBody({ title: title ?? null }),
      },
    );
  },

  loadConversation: (conversationId: string): Promise<ConversationDetail> =>
    json<ConversationDetail>(`/ai/conversations/${encodeURIComponent(conversationId)}`),

  deleteConversation: (conversationId: string): Promise<void> =>
    empty(`/ai/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" }),

  send: async (
    sendRequest: {
      runId: string;
      conversationId: string;
      documentRef: DocumentRef;
      message: string;
    },
    onEvent: (event: ChatEvent) => void,
  ): Promise<string> => {
    paperIdOf(sendRequest.documentRef);
    const controller = new AbortController();
    controllers.set(sendRequest.runId, controller);
    try {
      const response = await request(
        `/ai/conversations/${encodeURIComponent(sendRequest.conversationId)}/messages/stream`,
        {
          method: "POST",
          ...jsonBody({ runId: sendRequest.runId, message: sendRequest.message }),
          signal: controller.signal,
        },
      );
      if (!response.ok) throw await failure(response);
      await consumeEvents(response, onEvent);
      return sendRequest.runId;
    } catch (error) {
      if (controller.signal.aborted) throw new WebChatError("已停止生成。");
      throw error;
    } finally {
      controllers.delete(sendRequest.runId);
    }
  },

  cancel: async (runId: string): Promise<boolean> => {
    const controller = controllers.get(runId);
    if (!controller) return false;
    controller.abort();
    return true;
  },
};

export function webChatError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return "操作失败，请稍后重试。";
}

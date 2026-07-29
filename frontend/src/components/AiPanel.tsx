import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icons } from "../design/icons";
import {
  desktopChat,
  desktopChatAvailable,
  desktopError,
  type ChatMessage,
  type CodexSessionSummary,
  type ConversationSummary,
  type DocumentContext,
  type DocumentRef,
  type PaperContextStatus,
  type ProviderStatus,
} from "../lib/desktopChat";
import { useUI } from "../store";
import "./AiPanel.css";

const CHIPS = ["核心贡献是什么？", "真正关键的 trick 是什么？", "实验如何证明方法有效？", "这篇论文有哪些局限？"];

type ContextPhase = "idle" | "waiting" | "extracting" | "understanding" | "ready" | "indexed" | "error";

interface AiPanelProps {
  documentRef: DocumentRef;
  documentTitle: string;
  contextReady: boolean;
  getContext: () => Promise<DocumentContext>;
  onOpenSettings?: () => void;
}

const newRunId = (): string =>
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `run-${Date.now()}-${Math.random().toString(16).slice(2)}`;

const phaseText = (
  phase: ContextPhase,
  provider: ProviderStatus | null,
  status: PaperContextStatus | null,
): string => {
  if (phase === "waiting") return "等待 PDF 加载";
  if (phase === "extracting") return "正在读取论文";
  if (phase === "understanding") return "正在建立论文理解";
  if (phase === "ready") return "已理解当前论文";
  if (phase === "indexed") return provider?.configured ? "论文已索引" : "已索引 · 等待配置模型";
  if (phase === "error") return "论文理解失败";
  if (status?.charCount) return `已读取 ${status.charCount.toLocaleString()} 字符`;
  return "准备论文上下文";
};

export function AiPanel({
  documentRef,
  documentTitle,
  contextReady,
  getContext,
  onOpenSettings,
}: AiPanelProps): JSX.Element {
  const toggleAI = useUI((state) => state.toggleAI);
  const available = desktopChatAvailable();

  const [provider, setProvider] = useState<ProviderStatus | null>(null);
  const [contextStatus, setContextStatus] = useState<PaperContextStatus | null>(null);
  const [contextPhase, setContextPhase] = useState<ContextPhase>("idle");
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [codexSessions, setCodexSessions] = useState<CodexSessionSummary[] | null>(null);
  const [codexBusy, setCodexBusy] = useState(false);

  const bodyRef = useRef<HTMLDivElement | null>(null);
  const getContextRef = useRef(getContext);
  const cachedContextRef = useRef<DocumentContext | null>(null);
  const runIdRef = useRef<string | null>(null);
  const documentGenerationRef = useRef(0);
  getContextRef.current = getContext;

  const preparePaperContext = useCallback(
    async (generation: number, providerConfigured: boolean): Promise<PaperContextStatus | null> => {
      if (!contextReady || documentGenerationRef.current !== generation) return null;
      setContextPhase("extracting");
      const context = cachedContextRef.current ?? await getContextRef.current();
      if (documentGenerationRef.current !== generation) return null;
      cachedContextRef.current = context;
      setContextPhase(providerConfigured ? "understanding" : "indexed");
      const prepared = await desktopChat.prepareContext(documentRef, context);
      if (documentGenerationRef.current !== generation) return null;
      setContextStatus(prepared);
      setContextPhase(prepared.hasSummary && prepared.status === "ready" ? "ready" : "indexed");
      if (prepared.error) setError(prepared.error);
      return prepared;
    },
    [contextReady, documentRef],
  );

  const loadConversation = useCallback(async (conversationId: string): Promise<void> => {
    const detail = await desktopChat.loadConversation(conversationId);
    setActiveId(detail.id);
    setMessages(detail.messages);
  }, []);

  const refreshConversations = useCallback(
    async (preferredId?: string | null): Promise<ConversationSummary[]> => {
      const next = await desktopChat.listConversations(documentRef);
      setConversations(next);
      const target = preferredId ?? activeId ?? next[0]?.id ?? null;
      if (target && next.some((conversation) => conversation.id === target)) {
        await loadConversation(target);
      } else {
        setActiveId(null);
        setMessages([]);
      }
      return next;
    },
    [activeId, documentRef, loadConversation],
  );

  useEffect(() => {
    let cancelled = false;
    const generation = documentGenerationRef.current + 1;
    documentGenerationRef.current = generation;
    const isCurrent = (): boolean => !cancelled && documentGenerationRef.current === generation;
    cachedContextRef.current = null;
    setProvider(null);
    setContextStatus(null);
    setContextPhase("idle");
    setConversations([]);
    setActiveId(null);
    setMessages([]);
    setInput("");
    setStreaming(false);
    setStreamingText("");
    setRunId(null);
    runIdRef.current = null;
    setError(null);
    setMenuOpen(false);
    setCodexSessions(null);

    if (!available) return () => undefined;
    if (!contextReady) {
      setContextPhase("waiting");
      return () => {
        cancelled = true;
      };
    }

    void (async () => {
      try {
        const [nextProvider, nextConversations, existingContext] = await Promise.all([
          desktopChat.providerStatus(),
          desktopChat.listConversations(documentRef),
          desktopChat.contextStatus(documentRef),
        ]);
        if (!isCurrent()) return;
        setProvider(nextProvider);
        setConversations(nextConversations);
        setContextStatus(existingContext);

        const first = nextConversations[0];
        if (first) {
          const detail = await desktopChat.loadConversation(first.id);
          if (!isCurrent()) return;
          setActiveId(detail.id);
          setMessages(detail.messages);
        }

        if (existingContext?.hasSummary && existingContext.status === "ready") {
          setContextPhase("ready");
          return;
        }
        if (existingContext && !nextProvider.configured) {
          setContextPhase("indexed");
          return;
        }

        await preparePaperContext(generation, nextProvider.configured);
      } catch (cause) {
        if (isCurrent()) {
          setContextPhase("error");
          setError(desktopError(cause));
        }
      }
    })();

    return () => {
      cancelled = true;
      if (documentGenerationRef.current === generation) documentGenerationRef.current += 1;
      if (runIdRef.current) void desktopChat.cancel(runIdRef.current).catch(() => undefined);
    };
    // A paper identity change is the only reason to initialise again. The
    // extraction callback also changes as pdf.js reports pages and zoom state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available, contextReady, documentRef.key, preparePaperContext]);

  useLayoutEffect(() => {
    const body = bodyRef.current;
    if (body) body.scrollTop = body.scrollHeight;
  }, [messages, streamingText, error]);

  useEffect(() => {
    if (!available) return () => undefined;
    const refreshProvider = (): void => {
      const generation = documentGenerationRef.current;
      void (async () => {
        try {
          const next = await desktopChat.providerStatus();
          if (documentGenerationRef.current !== generation) return;
          setProvider(next);
          if (!next.configured || !contextReady) return;
          const current = await desktopChat.contextStatus(documentRef);
          if (documentGenerationRef.current !== generation) return;
          setContextStatus(current);
          if (!current?.hasSummary) await preparePaperContext(generation, true);
        } catch (cause) {
          if (documentGenerationRef.current === generation) setError(desktopError(cause));
        }
      })();
    };
    window.addEventListener("pharos:model-provider-updated", refreshProvider);
    return () => window.removeEventListener("pharos:model-provider-updated", refreshProvider);
  }, [available, contextReady, documentRef, preparePaperContext]);

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (activeId) return activeId;
    const created = await desktopChat.createConversation(documentRef);
    setConversations((current) => [created, ...current]);
    setActiveId(created.id);
    setMessages([]);
    return created.id;
  }, [activeId, documentRef]);

  const send = useCallback(
    async (raw: string): Promise<void> => {
      const text = raw.trim();
      if (!text || streaming || !available) return;
      let activeProvider = provider;
      if (!activeProvider?.configured) {
        try {
          activeProvider = await desktopChat.providerStatus();
          setProvider(activeProvider);
        } catch (cause) {
          setError(desktopError(cause));
          return;
        }
        if (!activeProvider.configured) {
          setError("请先配置 OpenAI 兼容模型和 API Key。");
          onOpenSettings?.();
          return;
        }
        if (!contextStatus?.hasSummary && contextPhase !== "extracting" && contextPhase !== "understanding") {
          void (async () => {
            try {
              setContextPhase("extracting");
              const context = await getContextRef.current();
              cachedContextRef.current = context;
              setContextPhase("understanding");
              const prepared = await desktopChat.prepareContext(documentRef, context);
              setContextStatus(prepared);
              setContextPhase(prepared.hasSummary ? "ready" : "indexed");
            } catch (cause) {
              setContextPhase("error");
              setError(desktopError(cause));
            }
          })();
        }
      }

      setError(null);
      const nextRunId = newRunId();
      let conversationId: string;
      try {
        conversationId = await ensureConversation();
      } catch (cause) {
        setError(desktopError(cause));
        return;
      }

      const optimistic: ChatMessage = {
        id: `pending-${nextRunId}`,
        role: "user",
        content: text,
        timestampMs: Date.now(),
        model: null,
      };
      setMessages((current) => [...current, optimistic]);
      setInput("");
      setStreaming(true);
      setStreamingText("");
      setRunId(nextRunId);
      runIdRef.current = nextRunId;

      try {
        await desktopChat.send(
          {
            runId: nextRunId,
            conversationId,
            documentRef,
            message: text,
            currentContext: cachedContextRef.current,
          },
          (event) => {
            if (event.type === "delta") {
              setStreamingText((current) => current + event.text);
            } else if (event.type === "done") {
              setMessages((current) => [...current, event.message]);
              setStreamingText("");
            } else if (event.type === "error") {
              setError(event.message);
            }
          },
        );
      } catch (cause) {
        const message = desktopError(cause);
        if (message !== "已停止生成。") setError(message);
      } finally {
        setStreaming(false);
        setStreamingText("");
        setRunId(null);
        runIdRef.current = null;
        try {
          await loadConversation(conversationId);
          await refreshConversations(conversationId);
        } catch {
          /* The streamed answer is still on screen if disk refresh fails. */
        }
      }
    },
    [
      available,
      contextPhase,
      contextStatus?.hasSummary,
      documentRef,
      ensureConversation,
      loadConversation,
      onOpenSettings,
      provider?.configured,
      refreshConversations,
      streaming,
    ],
  );

  const stop = useCallback(() => {
    if (runId) void desktopChat.cancel(runId);
  }, [runId]);

  const createConversation = useCallback(async () => {
    if (streaming) return;
    try {
      const created = await desktopChat.createConversation(documentRef);
      setConversations((current) => [created, ...current]);
      setActiveId(created.id);
      setMessages([]);
      setError(null);
    } catch (cause) {
      setError(desktopError(cause));
    }
  }, [documentRef, streaming]);

  const deleteConversation = useCallback(async () => {
    if (!activeId || streaming || !window.confirm("删除当前 AI 对话？论文索引不会被删除。")) return;
    try {
      await desktopChat.deleteConversation(activeId);
      const next = await desktopChat.listConversations(documentRef);
      setConversations(next);
      if (next[0]) await loadConversation(next[0].id);
      else {
        setActiveId(null);
        setMessages([]);
      }
      setMenuOpen(false);
    } catch (cause) {
      setError(desktopError(cause));
    }
  }, [activeId, documentRef, loadConversation, streaming]);

  const openCodexImport = useCallback(async () => {
    setMenuOpen(false);
    setCodexSessions([]);
    setCodexBusy(true);
    setError(null);
    try {
      setCodexSessions(await desktopChat.discoverCodexSessions());
    } catch (cause) {
      setError(desktopError(cause));
    } finally {
      setCodexBusy(false);
    }
  }, []);

  const importCodex = useCallback(
    async (session: CodexSessionSummary) => {
      setCodexBusy(true);
      try {
        const imported = await desktopChat.importCodexSession(session.path, documentRef);
        await refreshConversations(imported.id);
        setCodexSessions(null);
        setMenuOpen(false);
      } catch (cause) {
        setError(desktopError(cause));
      } finally {
        setCodexBusy(false);
      }
    },
    [documentRef, refreshConversations],
  );

  const handoffToCodex = useCallback(async () => {
    if (!activeId) return;
    setCodexBusy(true);
    try {
      const result = await desktopChat.handoffToCodex(activeId);
      setError(`已创建 Codex 任务 ${result.threadId}`);
      setMenuOpen(false);
    } catch (cause) {
      setError(desktopError(cause));
    } finally {
      setCodexBusy(false);
    }
  }, [activeId]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void send(input);
    }
  };

  const statusLabel = phaseText(contextPhase, provider, contextStatus);
  const empty = messages.length === 0 && !streaming;

  return (
    <aside className="ph-ai" aria-label="AI 对话">
      <header className="ph-ai-hd">
        <span className="ph-ai-hd-mark"><Icons.spark size={13} /></span>
        <div className="ph-ai-hd-grow">
          <div className="ph-ai-hd-title">AI 对话</div>
          <div className={`ph-ai-context is-${contextPhase}`}>{statusLabel}</div>
        </div>
        {available && (
          <>
            <button className="ph-ai-head-btn" title="新建对话" onClick={() => void createConversation()}>
              <Icons.plus size={14} />
            </button>
            <div className="ph-ai-menu-wrap">
              <button className="ph-ai-head-btn" title="对话与 Codex" onClick={() => setMenuOpen((open) => !open)}>
                ···
              </button>
              {menuOpen && (
                <div className="ph-ai-menu">
                  <button onClick={() => void openCodexImport()} disabled={codexBusy}>从 Codex 导入…</button>
                  <button onClick={() => void handoffToCodex()} disabled={!activeId || codexBusy}>转交给 Codex</button>
                  <button className="is-danger" onClick={() => void deleteConversation()} disabled={!activeId || streaming}>删除当前对话</button>
                </div>
              )}
            </div>
          </>
        )}
        <button className="ph-ai-collapse" title="折叠 AI 对话" onClick={toggleAI}>
          <Icons.panelR />
        </button>
      </header>

      {available && conversations.length > 0 && (
        <div className="ph-ai-session-row">
          <select
            value={activeId ?? ""}
            disabled={streaming}
            aria-label="选择 AI 对话"
            onChange={(event) => void loadConversation(event.target.value)}
          >
            {conversations.map((conversation) => (
              <option key={conversation.id} value={conversation.id}>
                {conversation.source === "codex" ? "Codex · " : ""}{conversation.title}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="ph-ai-body ph-scroll" ref={bodyRef}>
        {!available ? (
          <div className="ph-ai-notice">
            <strong>AI 对话正在使用客户端本地能力</strong>
            <span>网页端不会接触你的模型密钥。请在 Pharos macOS 客户端中使用此功能。</span>
          </div>
        ) : provider !== null && !provider.configured ? (
          <div className="ph-ai-notice">
            <strong>连接你的模型</strong>
            <span>支持 OpenAI 兼容接口。API Key 只保存在系统凭据库中。</span>
            <button onClick={onOpenSettings}>配置模型</button>
          </div>
        ) : empty ? (
          <div className="ph-ai-empty">
            <div className="ph-ai-empty-hd">
              <span className="ph-ai-empty-mark"><Icons.spark /></span>
              <div className="ph-ai-empty-txt">
                {contextPhase === "ready"
                  ? "我已经预先理解这篇论文"
                  : contextPhase === "waiting"
                    ? "PDF 加载完成后会自动建立上下文"
                  : contextPhase === "extracting" || contextPhase === "understanding"
                    ? "正在为这篇论文建立上下文"
                    : "围绕当前论文开始对话"}
              </div>
              <div className="ph-ai-empty-paper" title={documentTitle}>{documentTitle}</div>
            </div>
            <div className="ph-ai-chips">
              {CHIPS.map((chip) => (
                <button key={chip} className="ph-ai-chip" onClick={() => void send(chip)}>
                  {chip}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="ph-ai-msgs">
            {messages.map((message) => {
              const user = message.role === "user";
              return (
                <div key={message.id} className={user ? "ph-ai-row ph-ai-row-user" : "ph-ai-row"}>
                  {!user && <span className="ph-ai-avatar"><Icons.spark size={13} /></span>}
                  <div className={`ph-ai-bubble ${user ? "ph-ai-bubble-user" : "ph-ai-bubble-ai"}`}>
                    {message.content}
                  </div>
                </div>
              );
            })}
            {streaming && (
              <div className="ph-ai-row">
                <span className="ph-ai-avatar"><Icons.spark size={13} /></span>
                <div className="ph-ai-bubble ph-ai-bubble-ai">
                  {streamingText || "正在思考"}<span className="ph-ai-caret" />
                </div>
              </div>
            )}
          </div>
        )}

        {error && (
          <div className={`ph-ai-feedback${error.startsWith("已创建 Codex") ? " is-success" : ""}`}>
            {error}
            <button title="关闭" onClick={() => setError(null)}><Icons.close /></button>
          </div>
        )}

        {codexSessions !== null && (
          <div className="ph-ai-codex">
            <div className="ph-ai-codex-head">
              <strong>导入 Codex 历史对话</strong>
              <button onClick={() => setCodexSessions(null)}><Icons.close /></button>
            </div>
            {codexSessions.length === 0 ? (
              <div className="ph-ai-codex-empty">
                {codexBusy ? "正在扫描 Codex 历史…" : "没有发现可导入的 Codex 对话。"}
              </div>
            ) : codexSessions.map((session) => (
              <button key={session.path} className="ph-ai-codex-item" disabled={codexBusy} onClick={() => void importCodex(session)}>
                <span>{session.title}</span>
                <small>{session.truncated ? "至少 " : ""}{session.messageCount} 条消息 · {new Date(session.updatedAtMs).toLocaleDateString()}</small>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="ph-ai-composer">
        <div className="ph-ai-box">
          <textarea
            className="ph-ai-ta"
            rows={1}
            placeholder="就这篇论文提问…"
            value={input}
            disabled={!available || streaming || provider?.configured === false}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <div className="ph-ai-foot">
            <span className="ph-ai-hint">Enter 发送 · Shift+Enter 换行</span>
            {streaming ? (
              <button className="ph-ai-send is-stop" title="停止生成" onClick={stop}>■</button>
            ) : (
              <button className="ph-ai-send" title="发送" disabled={!input.trim() || provider?.configured === false} onClick={() => void send(input)}>
                <Icons.send />
              </button>
            )}
          </div>
        </div>
      </div>
    </aside>
  );
}

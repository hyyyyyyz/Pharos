import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ChatMessage } from "../api/types";
import { useUI } from "../store";

const SUGGESTIONS = ["核心贡献是什么？", "方法有何创新？", "实验如何设计？", "有哪些局限？"];

export function ChatPanel() {
  const selectedId = useUI((s) => s.selectedPaperId);
  const toggleChat = useUI((s) => s.toggleChat);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Conversation is per-paper: reset when the selection changes.
  useEffect(() => {
    abortRef.current?.abort();
    setMessages([]);
    setInput("");
    setError(null);
    setStreaming(false);
  }, [selectedId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const send = async (raw: string) => {
    const text = raw.trim();
    if (!selectedId || !text || streaming) return;
    const history: ChatMessage[] = [...messages, { role: "user", content: text }];
    setMessages([...history, { role: "assistant", content: "" }]);
    setInput("");
    setError(null);
    setStreaming(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await api.chatStream(
        selectedId,
        history,
        (tok) =>
          setMessages((cur) => {
            const copy = [...cur];
            const last = copy[copy.length - 1];
            copy[copy.length - 1] = { ...last, content: last.content + tok };
            return copy;
          }),
        ctrl.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setError((e as Error).message);
        setMessages((cur) => cur.filter((m, i) => !(i === cur.length - 1 && m.role === "assistant" && !m.content)));
      }
    } finally {
      setStreaming(false);
    }
  };

  return (
    <aside className="chat">
      <header className="chat-head">
        <span className="chat-title">
          <span className="xz-seal chat-seal">P</span> 领航
        </span>
        <button className="icon-btn" onClick={toggleChat} title="收起" aria-label="收起对话">
          ▷
        </button>
      </header>

      <div className="chat-scroll" ref={scrollRef}>
        {!selectedId ? (
          <p className="chat-empty xz-faint">先自左侧择一卷，再于此问答。</p>
        ) : messages.length === 0 ? (
          <div className="chat-welcome">
            <p className="xz-muted">关于这篇论文，想问些什么？</p>
            <div className="chat-suggest">
              {SUGGESTIONS.map((q) => (
                <button key={q} className="suggest-chip" onClick={() => send(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role}`}>
              {m.role === "assistant" && <span className="xz-seal bubble-seal">P</span>}
              <div className="bubble-body">
                {m.content || (streaming && i === messages.length - 1 ? <span className="caret" /> : "")}
                {streaming && i === messages.length - 1 && m.content && <span className="caret" />}
              </div>
            </div>
          ))
        )}
        {error && <p className="paper-error chat-error">{error}</p>}
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(input);
            }
          }}
          placeholder={selectedId ? "问一句…（Enter 发送，Shift+Enter 换行）" : "先择一卷"}
          disabled={!selectedId || streaming}
          rows={1}
        />
        <button
          type="submit"
          className="xz-btn xz-btn--primary send-btn"
          disabled={!selectedId || streaming || !input.trim()}
        >
          {streaming ? "…" : "问"}
        </button>
      </form>
    </aside>
  );
}

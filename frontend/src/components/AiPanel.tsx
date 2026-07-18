import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ChatMessage } from "../api/types";
import { Icons } from "../design/icons";
import { useUI } from "../store";
import "./AiPanel.css";

const CHIPS = ["核心贡献是什么？", "方法有何创新？", "用一句话概括这篇论文", "有哪些局限性？"];

/**
 * Chat history per paper, kept outside React so switching tabs (which unmounts
 * this panel) does not lose the conversation. Deliberately not in src/store.ts.
 */
const chatHistory: Record<string, ChatMessage[]> = {};

export function AiPanel({ paperId }: { paperId: string }): JSX.Element {
  const toggleAI = useUI((s) => s.toggleAI);

  const [msgs, setMsgs] = useState<ChatMessage[]>(() => chatHistory[paperId] ?? []);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [error, setError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  // Switching papers swaps the transcript and cancels any in-flight answer.
  useEffect(() => {
    setMsgs(chatHistory[paperId] ?? []);
    setInput("");
    setStreamingText("");
    setError(null);
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, [paperId]);

  // Follow the tail as tokens arrive.
  useLayoutEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, streamingText, error]);

  const commit = useCallback(
    (next: ChatMessage[]) => {
      chatHistory[paperId] = next;
      setMsgs(next);
    },
    [paperId],
  );

  const run = useCallback(
    async (history: ChatMessage[]) => {
      const ac = new AbortController();
      abortRef.current = ac;
      setError(null);
      setStreaming(true);
      setStreamingText("");
      let acc = "";
      try {
        await api.chatStream(
          paperId,
          history,
          (t) => {
            acc += t;
            setStreamingText(acc);
          },
          ac.signal,
        );
        if (!ac.signal.aborted) commit([...history, { role: "assistant", content: acc }]);
      } catch (e) {
        if (!ac.signal.aborted) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!ac.signal.aborted) {
          setStreaming(false);
          setStreamingText("");
        }
      }
    },
    [paperId, commit],
  );

  const send = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text || streaming) return;
      const next: ChatMessage[] = [...msgs, { role: "user", content: text }];
      commit(next);
      setInput("");
      void run(next);
    },
    [msgs, streaming, commit, run],
  );

  const retry = useCallback(() => {
    const lastUser = msgs.map((m) => m.role).lastIndexOf("user");
    setError(null);
    if (lastUser < 0) return;
    void run(msgs.slice(0, lastUser + 1));
  }, [msgs, run]);

  const onInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  const sendDisabled = streaming || input.trim().length === 0;
  const isEmpty = msgs.length === 0 && !streaming && error === null;

  return (
    <aside className="ph-ai">
      <header className="ph-ai-hd">
        <span className="ph-ai-hd-mark">
          <Icons.spark size={13} />
        </span>
        <div className="ph-ai-hd-grow">
          <div className="ph-ai-hd-title">领航</div>
        </div>
        <button className="ph-ai-collapse" title="折叠" onClick={toggleAI}>
          <Icons.panelR />
        </button>
      </header>

      <div className="ph-ai-body ph-scroll" ref={bodyRef}>
        {isEmpty ? (
          <div className="ph-ai-empty">
            <div className="ph-ai-empty-hd">
              <span className="ph-ai-empty-mark">
                <Icons.spark />
              </span>
              <div className="ph-ai-empty-txt">
                我已读完这篇论文
                <br />
                试试从这些问题开始
              </div>
            </div>
            <div className="ph-ai-chips">
              {CHIPS.map((c) => (
                <button key={c} className="ph-ai-chip" onClick={() => send(c)}>
                  {c}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="ph-ai-msgs">
            {msgs.map((m, i) => {
              const user = m.role === "user";
              return (
                <div key={i} className={user ? "ph-ai-row ph-ai-row-user" : "ph-ai-row"}>
                  {!user && (
                    <span className="ph-ai-avatar">
                      <Icons.spark size={13} />
                    </span>
                  )}
                  <div className={`ph-ai-bubble ${user ? "ph-ai-bubble-user" : "ph-ai-bubble-ai"}`}>
                    {m.content}
                  </div>
                </div>
              );
            })}

            {streaming && (
              <div className="ph-ai-row">
                <span className="ph-ai-avatar">
                  <Icons.spark size={13} />
                </span>
                <div className="ph-ai-bubble ph-ai-bubble-ai">
                  {streamingText}
                  <span className="ph-ai-caret" />
                </div>
              </div>
            )}

            {error !== null && (
              <div className="ph-ai-row">
                <span className="ph-ai-avatar ph-ai-avatar-err">
                  <Icons.alert size={14} />
                </span>
                <div className="ph-ai-bubble-err" title={error}>
                  回答生成失败，请稍后重试。
                  <button className="ph-ai-retry" onClick={retry}>
                    重试
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="ph-ai-composer">
        <div className="ph-ai-box">
          <textarea
            className="ph-ai-ta"
            rows={1}
            placeholder="向领航提问…"
            value={input}
            disabled={streaming}
            onChange={onInput}
            onKeyDown={onKeyDown}
          />
          <div className="ph-ai-foot">
            <span className="ph-ai-hint">Enter 发送 · Shift+Enter 换行</span>
            <button
              className="ph-ai-send"
              title="发送"
              disabled={sendDisabled}
              onClick={() => send(input)}
            >
              <Icons.send />
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}

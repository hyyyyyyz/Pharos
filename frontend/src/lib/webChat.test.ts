/**
 * The chat transport's load-bearing logic is the part a manual click-through
 * never exercises: NDJSON framing over a stream that arrives in whatever pieces
 * the network chose. Locally an answer usually lands in a single chunk, so a
 * broken reassembly loop looks perfect in development and only garbles text in
 * production. These tests deliver the stream deliberately badly.
 *
 * The rest of the module is `fetch` plumbing, and mocking HTTP to assert that
 * HTTP was called would pin nothing. What is pinned here besides framing: the
 * two guards that must fail *before* a request goes out.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearSession, setSession } from "../auth/session";
import type { ChatEvent, DocumentRef } from "./webChat";
import { webChat, webChatError } from "./webChat";

const signIn = (): void =>
  setSession({
    token: "test-token",
    expires_at: new Date(Date.now() + 3_600_000).toISOString(),
    user: {
      id: "u1",
      email: "reader@example.com",
      display_name: null,
      created_at: "2026-01-01T00:00:00Z",
      last_login_at: null,
    },
  });

const inLibrary: DocumentRef = {
  key: "paper:p1",
  kind: "paper",
  title: "Attention Is All You Need",
  paperId: "p1",
};

/** A response whose body arrives as the given pieces, in order. */
const streaming = (chunks: readonly Uint8Array[]): Response =>
  new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(chunk);
        controller.close();
      },
    }),
  );

const encode = (text: string): Uint8Array => new TextEncoder().encode(text);

const collect = async (response: Response): Promise<ChatEvent[]> => {
  const events: ChatEvent[] = [];
  vi.stubGlobal("fetch", vi.fn(async () => response));
  await webChat.send(
    { runId: "r1", conversationId: "c1", documentRef: inLibrary, message: "hi" },
    (event) => events.push(event),
  );
  return events;
};

const DONE = '{"type":"done","message":{"id":"m1","role":"assistant","content":"你好世界","timestampMs":1,"model":"gpt-4o"}}';

beforeEach(signIn);
afterEach(() => vi.unstubAllGlobals());

describe("webChat.send framing", () => {
  it("reassembles events split across chunk boundaries", async () => {
    const events = await collect(
      streaming([
        encode('{"type":"started","run_id":"r1"}\n{"type":"delta","te'),
        encode('xt":"你好"}\n{"type":"delta","text":"世界"}\n'),
        // No trailing newline: the last event is only ever flushed by the
        // after-the-loop parse, which is easy to drop in a refactor.
        encode(DONE),
      ]),
    );

    expect(events.map((e) => e.type)).toEqual(["started", "delta", "delta", "done"]);
    expect(events[1]).toEqual({ type: "delta", text: "你好" });
    expect(events[3]).toEqual({
      type: "done",
      message: {
        id: "m1",
        role: "assistant",
        content: "你好世界",
        timestampMs: 1,
        model: "gpt-4o",
      },
    });
  });

  it("decodes multi-byte characters split across chunk boundaries", async () => {
    // One byte at a time is the worst case the network can produce, and every
    // Chinese character in the payload lands mid-decode. Without a streaming
    // TextDecoder each split character becomes U+FFFD and the JSON still parses
    // — the answer arrives, just as mojibake.
    const payload = encode(`{"type":"delta","text":"翻译完成"}\n${DONE}`);
    const events = await collect(
      streaming(Array.from(payload, (_, i) => payload.subarray(i, i + 1))),
    );

    expect(events[0]).toEqual({ type: "delta", text: "翻译完成" });
    expect(events).toHaveLength(2);
  });

  it("skips blank and malformed lines instead of abandoning the answer", async () => {
    // A stray keep-alive newline or a truncated line must not cost the user the
    // rest of a reply that is still streaming in fine.
    const events = await collect(
      streaming([
        encode('\n{"type":"started","run_id":"r1"}\n\nnot json at all\n'),
        encode(`{"type":"delta","text":"ok"}\n${DONE}\n`),
      ]),
    );

    expect(events.map((e) => e.type)).toEqual(["started", "delta", "done"]);
  });

  it("forgets the run once it finishes, so cancel does not hold a controller", async () => {
    await collect(streaming([encode(`${DONE}\n`)]));
    expect(await webChat.cancel("r1")).toBe(false);
  });

  it("reports an unknown run as nothing to cancel", async () => {
    expect(await webChat.cancel("never-started")).toBe(false);
  });
});

describe("webChat guards", () => {
  it("refuses a document that is not in the Pharos library", async () => {
    // The server answers from its own copy of a paper, so a reference it cannot
    // resolve has no conversation to hold. Failing here rather than at the
    // server keeps the error in the user's language and off the network.
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const outside: DocumentRef = { key: "zotero:x", kind: "paper", title: "Elsewhere" };

    await expect(webChat.contextStatus(outside)).rejects.toThrow(/已经导入 Pharos 文库/);
    // Whitespace is absence: a blank id must not become a request for "/ai/papers/%20".
    await expect(webChat.contextStatus({ ...outside, paperId: "   " })).rejects.toThrow(
      /已经导入 Pharos 文库/,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses to send without a session rather than firing an anonymous request", async () => {
    clearSession();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(webChat.providerStatus()).rejects.toThrow(/登录状态已失效/);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("carries the bearer token on every authenticated request", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await webChat.providerStatus();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/ai/provider");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer test-token");
  });
});

describe("webChatError", () => {
  it("prefers the server's own words", () => {
    expect(webChatError(new Error("模型额度不足"))).toBe("模型额度不足");
    expect(webChatError("已停止生成。")).toBe("已停止生成。");
  });

  it("falls back to something sayable for a non-error", () => {
    // Anything can reach a catch block; the panel still has to render a string.
    expect(webChatError(null)).toBe("操作失败，请稍后重试。");
    expect(webChatError(502)).toBe("操作失败，请稍后重试。");
    expect(webChatError({ detail: "unhelpful" })).toBe("操作失败，请稍后重试。");
  });
});

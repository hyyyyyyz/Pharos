import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./client";

describe("api.importArxiv", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts the pasted value as JSON and returns the PaperOut payload", async () => {
    const paper = {
      id: "paper-1",
      title: "Attention Is All You Need",
      orig_filename: "1706.03762.pdf",
      page_count: 15,
      source: "arxiv",
      source_lang: "en",
      added_at: "2026-08-12T00:00:00Z",
      authors: [],
      year: 2017,
      venue: "arXiv",
      doi: null,
      abstract: null,
      meta_source: "arxiv",
      latest_job: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(paper), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.importArxiv("https://arxiv.org/abs/1706.03762v7")).resolves.toEqual(paper);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/papers/import/arxiv");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({
      input: "https://arxiv.org/abs/1706.03762v7",
    });
  });

  it("surfaces the backend's Chinese validation error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "请输入有效的 arXiv 链接或编号" }), {
          status: 400,
          statusText: "Bad Request",
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.importArxiv("not-an-id")).rejects.toMatchObject({
      status: 400,
      message: "请输入有效的 arXiv 链接或编号",
    });
  });
});

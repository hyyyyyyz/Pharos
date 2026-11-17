import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const session = vi.hoisted(() => ({ token: "tok" as string | null, clearSession: vi.fn() }));
vi.mock("../auth/session", () => ({
  getToken: () => session.token,
  clearSession: session.clearSession,
}));

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

async function load() {
  vi.resetModules();
  return import("./transport");
}

describe("transport", () => {
  beforeEach(() => {
    session.token = "tok";
    session.clearSession.mockClear();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("prefixes /api and sends the bearer token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    const t = await load();
    await expect(t.json("/harness/workflows")).resolves.toEqual({ ok: true });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/harness/workflows");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("honours VITE_API_BASE for every caller", async () => {
    vi.stubEnv("VITE_API_BASE", "https://example.test/api");
    const fetchMock = vi.fn().mockResolvedValue(reply(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    const t = await load();
    await t.json("/x");
    expect(fetchMock.mock.calls[0][0]).toBe("https://example.test/api/x");
  });

  it("omits the token for anonymous requests and does not sign out on their 401", async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(401, { detail: "bad password" }));
    vi.stubGlobal("fetch", fetchMock);
    const t = await load();
    await expect(t.json("/auth/login", {}, { anon: true })).rejects.toMatchObject({
      status: 401,
      message: "bad password",
    });
    expect(new Headers(fetchMock.mock.calls[0][1].headers).has("Authorization")).toBe(false);
    expect(session.clearSession).not.toHaveBeenCalled();
  });

  it("signs out on a 401 to an authenticated request and throws ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(401, { detail: "expired" })));
    const t = await load();
    const err = await t.json("/papers").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(t.ApiError);
    expect(session.clearSession).toHaveBeenCalledTimes(1);
  });

  it("falls back to the status text when the error body has no string detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(422, { detail: [{ msg: "x" }] })));
    const t = await load();
    await expect(t.json("/papers")).rejects.toMatchObject({ status: 422 });
  });

  it("maps a transport failure to ApiError with status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const t = await load();
    await expect(t.json("/health")).rejects.toMatchObject({ status: t.NETWORK_ERROR });
  });

  it("empty() accepts 204 and rejects errors", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(reply(204)).mockResolvedValueOnce(reply(500, { detail: "boom" }));
    vi.stubGlobal("fetch", fetchMock);
    const t = await load();
    await expect(t.empty("/highlights/1", { method: "DELETE" })).resolves.toBeUndefined();
    await expect(t.empty("/highlights/1", { method: "DELETE" })).rejects.toMatchObject({ status: 500 });
  });

  it("is shared by the harness and chat clients", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(reply(401, { detail: "no" })));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("VITE_API_BASE", "https://example.test/api");
    vi.resetModules();
    const { harnessApi } = await import("./harness");
    const { webChat } = await import("../lib/webChat");
    await expect(harnessApi.workflows()).rejects.toMatchObject({ status: 401 });
    await expect(webChat.providerStatus()).rejects.toMatchObject({ status: 401 });
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual([
      "https://example.test/api/harness/workflows",
      "https://example.test/api/ai/provider",
    ]);
    expect(session.clearSession).toHaveBeenCalledTimes(2);
  });
});

/**
 * The one transport every API caller shares.
 *
 * Four separate fetch wrappers used to live in this app (client, harness,
 * webChat, HighlightLayer). They disagreed on the base URL (harness hard-coded
 * "/api"), on what a 401 does, and on the error type. Anything that talks to the
 * backend now goes through `http` / `json` / `empty` here: one BASE, one
 * Authorization header, one 401 → sign-out, one `ApiError`. Streaming callers
 * use `http` and parse the body themselves.
 */
import { clearSession, getToken } from "../auth/session";

// Web production is same-origin, so "/api" reaches the FastAPI process that
// also serves this bundle. Development proxies it through Vite; VITE_API_BASE
// still overrides it for a deployment that splits the two.
export const BASE = import.meta.env.VITE_API_BASE ?? "/api";

/**
 * An HTTP-level failure, carrying the status so callers can branch on it.
 *
 * The UI needs to tell "wrong password" (401 on sign-in) from "registration is
 * closed" (403) from "the server is down", and a bare Error with a message
 * string forces every call site to pattern-match prose. Hence the status.
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Status for a request that never reached a server at all.
 *
 * Zero, because there is no HTTP status: the transport failed. Distinct from
 * every real code so `status === 401` and friends keep meaning what they mean,
 * and so a caller can ask "was this the network?" without matching on prose.
 */
export const NETWORK_ERROR = 0;

/* --------------------------------------------------------------- transport */

/** Options that change how a single request is authenticated. */
export interface FetchOpts {
  /** Send without a bearer token. Only register and login, which mint one. */
  anon?: boolean;
}

/**
 * The one place a request leaves this app.
 *
 * Every caller goes through here, which is the point: attaching the header at
 * call sites means the one call site somebody forgets ships an endpoint that
 * silently serves the wrong user's data — or, once the backend requires auth,
 * an endpoint that mysteriously 401s. Centralising it makes "authenticated" the
 * default you have to opt out of.
 */
export async function http(path: string, init: RequestInit = {}, opts: FetchOpts = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = opts.anon === true ? null : getToken();
  if (token !== null) headers.set("Authorization", `Bearer ${token}`);

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers });
  } catch (cause) {
    // `fetch` rejects with a bare `TypeError: Failed to fetch` for every
    // transport failure there is — no route to the host, the server not
    // listening, a connection reset mid-body, a request body that could not be
    // read, TLS refused, cleartext blocked. The browser deliberately tells the
    // page nothing more, to avoid leaking network topology.
    //
    // That message then went straight to the user, who saw "上传失败：Failed to
    // fetch" and had nothing to act on. Whatever the cause, the useful thing to
    // say is WHERE it was trying to reach and WHAT is worth checking; the
    // status is 0 so callers can still tell this from a real HTTP answer.
    if (cause instanceof TypeError) {
      // Spell the target out absolutely. On the web `BASE` is "/api", which
      // tells a reader nothing; in the Android build it is a LAN address baked
      // in at build time, and "which machine was it even trying?" is usually
      // the whole answer.
      const target =
        BASE.startsWith("/") && typeof location !== "undefined" ? `${location.origin}${BASE}` : BASE;
      throw new ApiError(
        NETWORK_ERROR,
        `连不上服务器 ${target}。请检查网络是否可达、后端是否在运行；` +
          `如果是上传，也可能是文件太大被中途掐断。`,
      );
    }
    throw cause;
  }

  // A 401 on a request we *authenticated* means the token is dead — expired,
  // revoked via logout-all, or the account is gone. Drop it: the session store
  // notifies AuthGate, which re-renders to sign-in. Without this the app sits
  // there failing every query with no explanation of why.
  //
  // A 401 on an anonymous request is just "wrong password" and must not touch
  // the session (there is nothing to clear, and clearing would churn the gate).
  if (res.status === 401 && token !== null) clearSession();

  return res;
}

/** Pull the backend's `detail` out of an error body; fall back to the status. */
export async function failure(res: Response): Promise<ApiError> {
  let detail = res.statusText;
  try {
    const body = (await res.json()) as { detail?: unknown };
    // FastAPI validation errors put a list here, not a string; showing
    // "[object Object]" to a user is worse than showing the status text.
    if (typeof body.detail === "string" && body.detail !== "") detail = body.detail;
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, detail);
}

/** GET/POST/… returning JSON. */
export async function json<T>(path: string, init: RequestInit = {}, opts: FetchOpts = {}): Promise<T> {
  const res = await http(path, init, opts);
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

/** For the 204 endpoints (password change, logout-all): no body to parse. */
export async function empty(path: string, init: RequestInit = {}): Promise<void> {
  const res = await http(path, init);
  if (!res.ok) throw await failure(res);
}

/** JSON request body + its content type, in one place. */
export const body = (data: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

/** The Authorization header as an object, or `{}` when signed out. Exported for
 *  the rare consumer that builds its own request (pdf.js); prefer `api`. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token === null ? {} : { Authorization: `Bearer ${token}` };
}

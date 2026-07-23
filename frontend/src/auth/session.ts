/**
 * The bearer token and the cached user, in one place.
 *
 * WHY a module and not just React state: `client.ts` has to read the token from
 * plain async functions that are nowhere near a component, and it has to be able
 * to *clear* it from a fetch handler when the server says 401. A React store
 * alone could do neither without a hook, so this module is the single source of
 * truth and `store.ts` mirrors it for rendering.
 *
 * WHY localStorage rather than an httpOnly cookie: the production frontend is a
 * static bundle on github.io talking to a backend on a different origin, so a
 * cookie would need SameSite=None + a shared parent domain we do not have, and
 * every request would need credentials-mode CORS. localStorage is the pragmatic
 * choice here, and it is an honest trade-off, not a free one: any script that
 * executes on our origin can read this token, so an XSS bug is a full account
 * takeover rather than a defaced page. That is the cost we are accepting. It is
 * paid down by keeping third-party script out of the bundle and by
 * `token_epoch` on the backend, which lets a user revoke every outstanding
 * token at once via POST /api/auth/logout-all.
 */
import type { AuthSession, AuthUser } from "../api/types";

/** One key, one JSON blob: the token and its user can never be half-written. */
const STORAGE_KEY = "ph-auth";

export interface Session {
  /** Null whenever the user is signed out — the gate keys off exactly this. */
  token: string | null;
  /** Last known profile, so the app can paint before /auth/me answers. */
  user: AuthUser | null;
  /** ISO 8601, from the server. Null for a session stored without one. */
  expiresAt: string | null;
}

const EMPTY: Session = { token: null, user: null, expiresAt: null };

type Listener = () => void;
const listeners = new Set<Listener>();

/** Cached so `getSession()` keeps a stable identity between changes; React
 *  external-store subscribers and zustand equality checks both rely on that. */
let current: Session = EMPTY;

/* --------------------------------------------------------------- storage io */

function read(): Session {
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    // Safari private mode throws on access rather than returning null.
    return EMPTY;
  }
  if (!raw) return EMPTY;
  try {
    const parsed = JSON.parse(raw) as Partial<Session>;
    // Anything without a usable token is treated as signed out rather than
    // trusted: a half-written or hand-edited blob must not produce a state the
    // rest of the app thinks is authenticated.
    if (typeof parsed.token !== "string" || parsed.token === "") return EMPTY;
    return {
      token: parsed.token,
      user: parsed.user ?? null,
      expiresAt: typeof parsed.expiresAt === "string" ? parsed.expiresAt : null,
    };
  } catch {
    return EMPTY;
  }
}

function write(next: Session): void {
  try {
    if (next.token === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Out of quota or private mode: the session still works for this tab, it
    // just will not survive a reload. Failing the sign-in would be worse.
  }
}

function emit(next: Session): void {
  current = next;
  for (const l of listeners) l();
}

/* ------------------------------------------------------------------ expiry */

/** True once `expiresAt` is in the past. A session with no expiry never expires
 *  locally — the server is still the authority, and a 401 will clear it. */
function expired(s: Session): boolean {
  if (s.expiresAt === null) return false;
  const t = Date.parse(s.expiresAt);
  return Number.isFinite(t) && t <= Date.now();
}

current = read();
// Drop an already-dead session at startup so the gate shows sign-in immediately
// instead of flashing the app and bouncing on the first 401.
if (current.token !== null && expired(current)) {
  write(EMPTY);
  current = EMPTY;
}

/* ------------------------------------------------------------------ public */

/** The current session. Stable identity until something actually changes. */
export function getSession(): Session {
  return current;
}

/**
 * The token to send, or null. Clears an expired session as a side effect, so a
 * request is never made with a token we already know the server will reject.
 * Called from request paths only — never from render.
 */
export function getToken(): string | null {
  if (current.token !== null && expired(current)) {
    clearSession();
    return null;
  }
  return current.token;
}

/** Store the result of a successful register/login. */
export function setSession(auth: AuthSession): void {
  const next: Session = { token: auth.token, user: auth.user, expiresAt: auth.expires_at };
  write(next);
  emit(next);
}

/**
 * Sign out locally: forget the token and the cached user.
 *
 * Called both by the user (the sign-out control) and by `client.ts` on any 401
 * from a request we authenticated. Idempotent, and silent when there was no
 * session, so the 401 handler can call it without checking first.
 */
export function clearSession(): void {
  if (current.token === null && current.user === null) return;
  write(EMPTY);
  emit(EMPTY);
}

/** Refresh the cached profile without touching the token (PATCH /auth/me,
 *  and the GET /auth/me the gate makes on a cold start). */
export function setSessionUser(user: AuthUser): void {
  if (current.token === null) return; // signed out mid-flight; do not resurrect
  const next: Session = { ...current, user };
  write(next);
  emit(next);
}

/** Subscribe to every change. Returns the unsubscribe function. */
export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/* -------------------------------------------------------------- cross-tab */

// Signing out in one tab must sign out the others: leaving a second tab holding
// a live token contradicts what the user just asked for. `storage` fires only
// in the *other* tabs, so this never echoes our own writes.
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== null && e.key !== STORAGE_KEY) return;
    const next = read();
    // Compare by value, not identity: `read()` re-parses and would otherwise
    // look "changed" on every unrelated write and re-render the whole app.
    if (next.token === current.token && next.user?.id === current.user?.id) return;
    emit(next);
  });
}

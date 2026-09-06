import { clearSession, getToken, setSession, setSessionUser } from "../auth/session";
import type {
  AuthSession,
  AuthStatus,
  AuthUser,
  ChatMessage,
  Collection,
  CollectionAddResult,
  CollectionCreateBody,
  CollectionDeleted,
  CollectionPatchBody,
  CollectionsResponse,
  DailyConfig,
  DailyConfigPatchBody,
  DailyDate,
  DailyDay,
  DailyPaper,
  DailyRun,
  DailyStatus,
  DailyVaultArchive,
  DailyVaultImportResult,
  DirectionCreateBody,
  DirectionPatchBody,
  Job,
  LoginBody,
  LiteratureResult,
  LiteratureSearch,
  LiteratureSearchBody,
  PasswordChangeBody,
  Paper,
  PdfKind,
  InkPoint,
  InkStrokeRow,
  PageNoteRow,
  PageNoteStyle,
  TapeRow,
  ProjectArtifact,
  ProjectArtifactCreateBody,
  ProjectArtifactPatchBody,
  ProjectSource,
  ProjectSourceCreateBody,
  ProjectSourcePatchBody,
  RegisterBody,
  ResearchProject,
  ResearchProjectCreateBody,
  ResearchProjectPatchBody,
  SearchResponse,
  Tag,
  TagCreateBody,
  TagPatchBody,
  UserDirection,
  ZoteroLinkBody,
  ZoteroOAuthStart,
  ZoteroStatus,
  AdminProbeResult,
  AdminProviders,
  AdminStats,
  AdminUser,
  AdminUserPage,
  AdminUserPatch,
  ArxivImportBody,
} from "./types";

// Web production is same-origin, so "/api" reaches the FastAPI process that
// also serves this bundle. Development proxies it through Vite; VITE_API_BASE
// still overrides it for a deployment that splits the two.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

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
interface FetchOpts {
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
async function http(path: string, init: RequestInit = {}, opts: FetchOpts = {}): Promise<Response> {
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
async function failure(res: Response): Promise<ApiError> {
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
async function json<T>(path: string, init: RequestInit = {}, opts: FetchOpts = {}): Promise<T> {
  const res = await http(path, init, opts);
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

/** For the 204 endpoints (password change, logout-all): no body to parse. */
async function empty(path: string, init: RequestInit = {}): Promise<void> {
  const res = await http(path, init);
  if (!res.ok) throw await failure(res);
}

/** JSON request body + its content type, in one place. */
const body = (data: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

/* --------------------------------------------------------------------- pdf */

/**
 * What pdf.js needs to fetch a PDF: the URL plus the headers to send with it.
 * Shaped to be spread straight into `pdfjs.getDocument(...)`, whose
 * DocumentInitParameters accepts exactly these two fields.
 */
export interface PdfSource {
  url: string;
  httpHeaders: Record<string, string>;
}

/* --------------------------------------------------------------------- api */

export const api = {
  health: (): Promise<{ status: string; engine: string; translator: string }> =>
    json<{ status: string; engine: string; translator: string }>("/health"),

  /* ------------------------------------------------------------------ auth */

  auth: {
    /**
     * Is this instance accepting new accounts? Anonymous by design — the answer
     * is needed before anyone has a token.
     *
     * A rejection here means "unknown", never "closed": a self-hosted backend
     * older than this route 404s, and an unreachable one throws, and hiding the
     * sign-up form in either case would lock people out of an instance that is
     * in fact open. POST /auth/register stays the authority; this only decides
     * what to draw.
     */
    status: (): Promise<AuthStatus> => json<AuthStatus>("/auth/status", {}, { anon: true }),

    /** Creates the account and signs in — the backend returns a token, so the
     *  user never has to type the password twice. 403 = registration closed. */
    register: async (data: RegisterBody): Promise<AuthSession> => {
      const auth = await json<AuthSession>("/auth/register", { method: "POST", ...body(data) }, { anon: true });
      setSession(auth);
      return auth;
    },

    login: async (data: LoginBody): Promise<AuthSession> => {
      const auth = await json<AuthSession>("/auth/login", { method: "POST", ...body(data) }, { anon: true });
      setSession(auth);
      return auth;
    },

    /** Validates the stored token against the server and refreshes the cached
     *  profile. Throws ApiError(401) — having already cleared the session — if
     *  the token is no longer good. */
    me: async (): Promise<AuthUser> => {
      const user = await json<AuthUser>("/auth/me");
      setSessionUser(user);
      return user;
    },

    updateMe: async (data: {
      display_name?: string;
      // The backend rejects an explicit null with a 400 — omit the key to leave
      // the setting alone, rather than sending null to mean "unchanged".
      pdf_translation?: boolean;
    }): Promise<AuthUser> => {
      const user = await json<AuthUser>("/auth/me", { method: "PATCH", ...body(data) });
      setSessionUser(user);
      return user;
    },

    changePassword: (data: PasswordChangeBody): Promise<void> =>
      empty("/auth/password", { method: "POST", ...body(data) }),

    /**
     * Bump the account's token epoch: every token ever issued to this user dies,
     * including this tab's. Clear locally too, so the UI reflects it at once
     * rather than on the next request's 401.
     */
    logoutAll: async (): Promise<void> => {
      try {
        await empty("/auth/logout-all", { method: "POST" });
      } finally {
        clearSession();
      }
    },

    /** Sign out this browser only. Tokens are stateless, so there is nothing to
     *  tell the server: forgetting the token *is* the logout. Use logoutAll to
     *  kill sessions on other devices. */
    logout: (): void => clearSession(),
  },

  /* ---------------------------------------------------------------- zotero */

  /** The signed-in user's Zotero connection. Consumed by SettingsModal. */
  zotero: {
    status: (): Promise<ZoteroStatus> => json<ZoteroStatus>("/zotero/status"),

    /** Create a one-use server flow, then let the browser visit authorize_url. */
    oauthStart: (): Promise<ZoteroOAuthStart> =>
      json<ZoteroOAuthStart>("/zotero/oauth/start", { method: "POST" }),

    /** Verify and store manually created credentials. Sync remains explicit. */
    link: (data: ZoteroLinkBody): Promise<ZoteroStatus> =>
      json<ZoteroStatus>("/zotero/link", { method: "POST", ...body(data) }),

    /**
     * Start a sync. Returns the *status*, not the result: the backend answers
     * 202 immediately and pages through Zotero in the background, so the
     * added/updated split appears under `status.sync` on a later GET /status.
     * Typing this as a result object would have the UI render `undefined`.
     */
    sync: (): Promise<ZoteroStatus> => json<ZoteroStatus>("/zotero/sync", { method: "POST" }),

    /** Forget the credentials. Papers already imported stay in the library. */
    unlink: (): Promise<void> => empty("/zotero/link", { method: "DELETE" }),
  },

  /* --------------------------------------------------------------- library */

  listPapers: (): Promise<Paper[]> => json<Paper[]>("/papers"),

  /** The recycle bin. A separate call rather than a flag on `listPapers` so the
   *  two live under distinct query keys and neither can overwrite the other's
   *  cache entry with the wrong set of rows. */
  listTrash: (): Promise<Paper[]> => json<Paper[]>("/papers?trash=true"),

  getPaper: (id: string): Promise<Paper> => json<Paper>(`/papers/${id}`),

  /**
   * Upload one PDF.
   *
   * The bytes are read into memory BEFORE the request, rather than handing the
   * `File` straight to `FormData` and letting the browser stream it. That looks
   * like a pointless copy and is not, on Android:
   *
   * A file chosen from a cloud provider — Drive, OneDrive, 百度网盘, anything
   * that plugs into the system document picker — is not a file. It is a
   * `content://` reference to a document the provider will materialise on
   * demand. The `File` object is created happily, `name` and `size` are
   * populated, and nothing has been read yet. The read happens later, while the
   * request body is being streamed, and if the provider cannot deliver (the
   * file is not cached offline, its permission grant has lapsed, the network is
   * gone, or it is a Google-native doc with no bytes at all) the body stream
   * errors mid-flight. `fetch` then rejects with exactly one thing:
   * `TypeError: Failed to fetch` — indistinguishable from the server being
   * down, which is why "上传云端文件会显示 Failed to fetch" was unactionable.
   *
   * Reading first moves that failure somewhere it can be named, and where it
   * can be told apart from a transport problem: if this throws, the file is the
   * problem; if the POST throws, the network is. It also means a read that DOES
   * succeed can no longer die halfway through the upload.
   */
  upload: async (file: File): Promise<Paper> => {
    let bytes: ArrayBuffer;
    try {
      bytes = await file.arrayBuffer();
    } catch {
      throw new ApiError(
        NETWORK_ERROR,
        "读不到这个文件的内容。如果它在云盘里（Google Drive / OneDrive / 百度网盘等），" +
          "系统只给了一个引用而不是文件本身——请先把它下载到平板本机，再从「文件」里选一次。",
      );
    }
    if (bytes.byteLength === 0) {
      throw new ApiError(
        NETWORK_ERROR,
        "这个文件是空的（0 字节）。云盘里没下载下来的文件常常是这样，请先下载到本机再上传。",
      );
    }
    const form = new FormData();
    // Re-wrapped as a Blob carrying the original name and type: the server
    // checks the extension, so the name has to survive the round trip.
    form.append("file", new Blob([bytes], { type: file.type || "application/pdf" }), file.name);
    // No Content-Type here on purpose: the browser must set it so it can add
    // the multipart boundary. `http` only ever *adds* Authorization, so the
    // upload is authenticated like everything else.
    return json<Paper>("/papers", { method: "POST", body: form });
  },

  /** Import a paper by arXiv id or official abs/pdf URL. */
  importArxiv: (input: string): Promise<Paper> =>
    json<Paper>("/papers/import/arxiv", {
      method: "POST",
      ...body({ input } satisfies ArxivImportBody),
    }),

  translate: (paperId: string, pages?: string): Promise<Job> => {
    const qs = pages ? `?pages=${encodeURIComponent(pages)}` : "";
    return json<Job>(`/papers/${paperId}/translate${qs}`, { method: "POST" });
  },

  getJob: (jobId: string): Promise<Job> => json<Job>(`/jobs/${jobId}`),

  /* There is deliberately no `pdfUrl()` returning a bare string. The endpoint
     requires a bearer token, a URL cannot carry one, and a helper that hands
     back a URL which type-checks perfectly and 401s at runtime is precisely how
     the reader broke the first time — the build stayed green while every PDF
     failed. Anything that needs the bytes goes through `pdfSource()`, or fetches
     them via `http()` and makes its own blob URL. */

  /**
   * The PDF as pdf.js should load it: URL + Authorization header.
   *
   * pdf.js fetches the file itself (with range requests), so it — not the
   * browser's document loader — controls the request, and `httpHeaders` rides
   * along on every one of those requests. That keeps the PDF endpoints behind
   * the same bearer check as everything else, instead of the alternative of
   * putting a token in the query string where it would land in server logs,
   * the Referer header, and the user's history.
   *
   * Cross-origin deployments need the backend's CORS config to allow the
   * Authorization request header, or the preflight will reject these.
   */
  pdfSource: (paperId: string, kind: PdfKind): PdfSource => ({
    url: `${BASE}/papers/${paperId}/pdf/${kind}`,
    httpHeaders: authHeaders(),
  }),

  /** Ask a question about a paper; streams the answer token-by-token. */
  chatStream: async (
    paperId: string,
    messages: ChatMessage[],
    onToken: (t: string) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    // Streams go through `http` like everything else — a streaming endpoint is
    // not an exception to authentication.
    const res = await http(`/papers/${paperId}/chat`, {
      method: "POST",
      ...body({ messages }),
      signal,
    });
    if (!res.ok) throw await failure(res);
    if (!res.body) throw new ApiError(res.status, "响应没有可读的流");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      onToken(decoder.decode(value, { stream: true }));
    }
  },

  /* ---------------------------------------------------------------- search */

  /**
   * Full-text search over the caller's own library.
   *
   * `signal` is threaded through because the search box fires per keystroke
   * (debounced): React Query aborts the superseded request, which both saves
   * the backend the work and stops a slow early response from landing after a
   * fast later one and showing results for a query the user has moved past.
   *
   * Any string is valid input — the backend sanitises stray quotes and FTS5
   * operators rather than 400ing, so there is nothing to validate here.
   */
  search: (
    q: string,
    opts: { limit?: number; offset?: number; signal?: AbortSignal } = {},
  ): Promise<SearchResponse> => {
    const params = new URLSearchParams({ q });
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.offset !== undefined) params.set("offset", String(opts.offset));
    return json<SearchResponse>(`/search?${params.toString()}`, { signal: opts.signal });
  },

  /* ----------------------------------------------------------- collections */

  collections: {
    /** The whole sidebar in one call: nested tree + the 我的文库/未分类 counts. */
    list: (): Promise<CollectionsResponse> => json<CollectionsResponse>("/collections"),

    create: (data: CollectionCreateBody): Promise<Collection> =>
      json<Collection>("/collections", { method: "POST", ...body(data) }),

    /**
     * Rename / re-parent / re-order. Send only the keys you mean to change:
     * the backend uses `exclude_unset`, so an omitted key is left alone while
     * an explicit `parent_id: null` is a real instruction to move to the top
     * level. An empty object is a 400, not a no-op.
     */
    update: (id: string, data: CollectionPatchBody): Promise<Collection> =>
      json<Collection>(`/collections/${id}`, { method: "PATCH", ...body(data) }),

    /** Deletes the folder only: children are promoted to its parent and the
     *  papers themselves are untouched (an unfiled paper falls back to 未分类). */
    remove: (id: string): Promise<CollectionDeleted> =>
      json<CollectionDeleted>(`/collections/${id}`, { method: "DELETE" }),

    /** Ids only, by design — the folder view intersects these against the
     *  library rather than taking a second copy of the Paper shape that could
     *  drift from `GET /papers`. */
    paperIds: (id: string): Promise<{ paper_ids: string[] }> =>
      json<{ paper_ids: string[] }>(`/collections/${id}/papers`),

    /** Idempotent: `added` counts only papers that were not already filed. A
     *  foreign paper id fails the whole batch rather than filing the rest. */
    addPapers: (id: string, paperIds: string[]): Promise<CollectionAddResult> =>
      json<CollectionAddResult>(`/collections/${id}/papers`, {
        method: "POST",
        ...body({ paper_ids: paperIds }),
      }),

    /** Returns the folder with a fresh count. 404 if the paper is not in it. */
    removePaper: (id: string, paperId: string): Promise<Collection> =>
      json<Collection>(`/collections/${id}/papers/${paperId}`, { method: "DELETE" }),
  },

  /* ------------------------------------------------------------------ tags */

  tags: {
    /** Ordered by name. */
    list: (): Promise<Tag[]> => json<Tag[]>("/tags"),

    create: (data: TagCreateBody): Promise<Tag> =>
      json<Tag>("/tags", { method: "POST", ...body(data) }),

    update: (id: string, data: TagPatchBody): Promise<Tag> =>
      json<Tag>(`/tags/${id}`, { method: "PATCH", ...body(data) }),

    remove: (id: string): Promise<void> => empty(`/tags/${id}`, { method: "DELETE" }),

    ofPaper: (paperId: string): Promise<Tag[]> => json<Tag[]>(`/papers/${paperId}/tags`),

    /** Replace, not merge — an empty list clears every tag and is valid input. */
    setForPaper: (paperId: string, tagIds: string[]): Promise<Tag[]> =>
      json<Tag[]>(`/papers/${paperId}/tags`, { method: "PUT", ...body({ tag_ids: tagIds }) }),
  },

  /** 每日论文 — the server-side digest. Grouped so the module owns its own surface. */
  daily: {
    /** Whether a reading layer is configured at all; drives the "未配置" banner. */
    status: (): Promise<DailyStatus> => json<DailyStatus>("/daily/status"),

    /** Newest first — the first entry is the default date to show. */
    dates: (): Promise<DailyDate[]> => json<DailyDate[]>("/daily/dates"),

    day: (date: string): Promise<DailyDay> => json<DailyDay>(`/daily/${encodeURIComponent(date)}`),

    /** Fetch + read. `days` back-fills a window; omit both for today only. */
    refresh: (data: { date?: string; days?: number } = {}): Promise<DailyRun> =>
      json<DailyRun>("/daily/refresh", { method: "POST", ...body(data) }),

    /** Force a (re-)read of one paper — used to retry a read_status="error" card. */
    read: (id: string): Promise<DailyPaper> =>
      json<DailyPaper>(`/daily/papers/${id}/read`, { method: "POST" }),

    /** Pull the paper into 文库; returns the new 文库 paper id. */
    import: (id: string): Promise<{ paper_id: string }> =>
      json<{ paper_id: string }>(`/daily/papers/${id}/import`, { method: "POST" }),

    vault: {
      /** Stable, id-free DTO used by both directory snapshots and JSON fallback. */
      export: (): Promise<DailyVaultArchive> =>
        json<DailyVaultArchive>("/daily/vault/export"),

      /** Merge is idempotent; profile replacement is explicit and user-scoped. */
      restore: (
        archive: DailyVaultArchive,
        restoreProfile = true,
      ): Promise<DailyVaultImportResult> =>
        json<DailyVaultImportResult>("/daily/vault/import", {
          method: "POST",
          ...body({ archive, restore_profile: restoreProfile }),
        }),
    },

    /**
     * The caller's own research directions — what they see, under which badge,
     * and how relevant it is to them.
     *
     * Every path here sits under `/daily/`, alongside `GET /daily/{date}`. The
     * backend mounts this router first precisely so `/daily/directions` is not
     * routed to the date handler; that is a server-side ordering guarantee, so
     * nothing here needs to work around it.
     */
    directions: {
      /** Disabled directions included, in `position` order — the settings page
       *  has to show a direction the user switched off, or it looks deleted. */
      list: (): Promise<UserDirection[]> => json<UserDirection[]>("/daily/directions"),

      /** 409 when the name collides with one of the caller's own, case-insensitively. */
      create: (data: DirectionCreateBody): Promise<UserDirection> =>
        json<UserDirection>("/daily/directions", { method: "POST", ...body(data) }),

      /** Send only the keys being changed; an empty body is a 400, not a no-op. */
      update: (id: string, data: DirectionPatchBody): Promise<UserDirection> =>
        json<UserDirection>(`/daily/directions/${encodeURIComponent(id)}`, {
          method: "PATCH",
          ...body(data),
        }),

      /** 204. No paper row is touched — the feed simply stops matching it. */
      remove: (id: string): Promise<void> =>
        empty(`/daily/directions/${encodeURIComponent(id)}`, { method: "DELETE" }),

      /**
       * Rewrite positions from an explicit id order; returns the whole list in
       * its new order. A partial list is accepted, but the editor always sends
       * every id so the result cannot depend on how the backend arranges the
       * ones it was not told about.
       */
      reorder: (directionIds: string[]): Promise<UserDirection[]> =>
        json<UserDirection[]>("/daily/directions/reorder", {
          method: "POST",
          ...body({ direction_ids: directionIds }),
        }),
    },

    /** The caller's sweep settings: which arXiv categories, and how many a day. */
    config: {
      get: (): Promise<DailyConfig> => json<DailyConfig>("/daily/config"),

      update: (data: DailyConfigPatchBody): Promise<DailyConfig> =>
        json<DailyConfig>("/daily/config", { method: "PATCH", ...body(data) }),
    },
  },

  /* ------------------------------------------------------ research workspace */

  discovery: {
    /** Search arXiv/OpenAlex and persist the complete run for later reopening. */
    search: (data: LiteratureSearchBody): Promise<LiteratureSearch> =>
      json<LiteratureSearch>("/discovery/search", { method: "POST", ...body(data) }),

    /** Newest first; entries include results so history remains useful offline. */
    listSearches: (): Promise<LiteratureSearch[]> =>
      json<LiteratureSearch[]>("/discovery/searches"),

    getSearch: (id: string): Promise<LiteratureSearch> =>
      json<LiteratureSearch>(`/discovery/searches/${encodeURIComponent(id)}`),

    /** Replace the rules-only digest with a provider-backed structured reading. */
    analyzeResult: (id: string): Promise<LiteratureResult> =>
      json<LiteratureResult>(
        `/discovery/results/${encodeURIComponent(id)}/analyze`,
        { method: "POST" },
      ),
  },

  projects: {
    list: (): Promise<ResearchProject[]> => json<ResearchProject[]>("/projects"),

    create: (data: ResearchProjectCreateBody): Promise<ResearchProject> =>
      json<ResearchProject>("/projects", { method: "POST", ...body(data) }),

    get: (id: string): Promise<ResearchProject> =>
      json<ResearchProject>(`/projects/${encodeURIComponent(id)}`),

    update: (id: string, data: ResearchProjectPatchBody): Promise<ResearchProject> =>
      json<ResearchProject>(`/projects/${encodeURIComponent(id)}`, {
        method: "PATCH",
        ...body(data),
      }),

    remove: (id: string): Promise<void> =>
      empty(`/projects/${encodeURIComponent(id)}`, { method: "DELETE" }),

    advance: (id: string): Promise<ResearchProject> =>
      json<ResearchProject>(`/projects/${encodeURIComponent(id)}/advance`, { method: "POST" }),

    addSource: (id: string, data: ProjectSourceCreateBody): Promise<ProjectSource> =>
      json<ProjectSource>(`/projects/${encodeURIComponent(id)}/sources`, {
        method: "POST",
        ...body(data),
      }),

    updateSource: (
      id: string,
      sourceId: string,
      data: ProjectSourcePatchBody,
    ): Promise<ProjectSource> =>
      json<ProjectSource>(
        `/projects/${encodeURIComponent(id)}/sources/${encodeURIComponent(sourceId)}`,
        { method: "PATCH", ...body(data) },
      ),

    removeSource: (id: string, sourceId: string): Promise<void> =>
      empty(`/projects/${encodeURIComponent(id)}/sources/${encodeURIComponent(sourceId)}`, {
        method: "DELETE",
      }),

    listArtifacts: (id: string): Promise<ProjectArtifact[]> =>
      json<ProjectArtifact[]>(`/projects/${encodeURIComponent(id)}/artifacts`),

    createArtifact: (id: string, data: ProjectArtifactCreateBody): Promise<ProjectArtifact> =>
      json<ProjectArtifact>(`/projects/${encodeURIComponent(id)}/artifacts`, {
        method: "POST",
        ...body(data),
      }),

    updateArtifact: (
      id: string,
      artifactId: string,
      data: ProjectArtifactPatchBody,
    ): Promise<ProjectArtifact> =>
      json<ProjectArtifact>(
        `/projects/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(artifactId)}`,
        { method: "PATCH", ...body(data) },
      ),

    removeArtifact: (id: string, artifactId: string): Promise<void> =>
      empty(
        `/projects/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(artifactId)}`,
        { method: "DELETE" },
      ),
  },

  /* ------------------------------------------------------------------ admin */

  /**
   * The administrator console. Every call here 403s for an ordinary account —
   * the backend gates them on `require_admin`, so the client never has to
   * decide who may see what; it only has to avoid *offering* the screen.
   */
  admin: {
    /** Account-only summary for the console. Research-library activity is
     * deliberately absent: local papers, projects and annotations are not
     * administrator inventory. */
    stats: (): Promise<AdminStats> => json<AdminStats>("/admin/stats"),

    listUsers: (
      opts: { q?: string; limit?: number; offset?: number; signal?: AbortSignal } = {},
    ): Promise<AdminUserPage> => {
      const params = new URLSearchParams();
      if (opts.q) params.set("q", opts.q);
      if (opts.limit !== undefined) params.set("limit", String(opts.limit));
      if (opts.offset !== undefined) params.set("offset", String(opts.offset));
      const query = params.toString();
      return json<AdminUserPage>(`/admin/users${query ? `?${query}` : ""}`, {
        signal: opts.signal,
      });
    },

    updateUser: (userId: string, patch: AdminUserPatch): Promise<AdminUser> =>
      json<AdminUser>(`/admin/users/${encodeURIComponent(userId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      }),

    /**
     * Permanently delete an account and its server-side Pharos data.
     * Local Zotero/Pharos libraries are outside this API and are not touched.
     *
     * `confirmEmail` must match the target's address — the backend checks it
     * too, so a mistyped id fails instead of erasing the wrong researcher.
     */
    deleteUser: (userId: string, confirmEmail: string): Promise<void> =>
      empty(
        `/admin/users/${encodeURIComponent(userId)}?confirm_email=${encodeURIComponent(confirmEmail)}`,
        { method: "DELETE" },
      ),

    providers: (): Promise<AdminProviders> => json<AdminProviders>("/admin/providers"),

    /** Sends one tiny completion to the provider. This is the only way to tell
     *  "a key is configured" from "the key works" — a typo and a decommissioned
     *  relay both look healthy in the configuration listing. */
    probeProvider: (name: string): Promise<AdminProbeResult> =>
      json<AdminProbeResult>(`/admin/providers/${encodeURIComponent(name)}/probe`, {
        method: "POST",
      }),
  },

  /* ------------------------------------------------------------------ ink */

  /**
   * Handwritten strokes, one row per pen-down to pen-up gesture. Owner-scoped
   * on the backend like every annotation; the list is read per document+kind,
   * a stroke is written the moment the pen lifts, and the eraser deletes.
   */
  ink: {
    list: (paperId: string, kind: PdfKind, signal?: AbortSignal): Promise<InkStrokeRow[]> =>
      json<InkStrokeRow[]>(`/papers/${encodeURIComponent(paperId)}/ink?kind=${kind}`, {
        signal,
      }),

    create: (
      paperId: string,
      stroke: {
        kind: PdfKind;
        page: number;
        points: InkPoint[];
        color: string;
        width: number;
      },
    ): Promise<InkStrokeRow> =>
      json<InkStrokeRow>(`/papers/${encodeURIComponent(paperId)}/ink`, {
        method: "POST",
        ...body(stroke),
      }),

    remove: (strokeId: string): Promise<void> =>
      empty(`/ink/${encodeURIComponent(strokeId)}`, { method: "DELETE" }),
  },

  /* ----------------------------------------------------------- page notes */

  /** 文本框 / 便利贴 — typed text anchored to a spot on a page. Edited in
   *  place like a tape strip, never replaced like a stroke: a note's geometry
   *  is four numbers, not a sampled path. */
  note: {
    list: (paperId: string, kind: PdfKind, signal?: AbortSignal): Promise<PageNoteRow[]> =>
      json<PageNoteRow[]>(`/papers/${encodeURIComponent(paperId)}/notes?kind=${kind}`, {
        signal,
      }),

    create: (
      paperId: string,
      note: {
        kind: PdfKind;
        page: number;
        x: number;
        y: number;
        w: number;
        h: number;
        style?: PageNoteStyle;
        color?: string;
        size?: number;
        body?: string;
      },
    ): Promise<PageNoteRow> =>
      json<PageNoteRow>(`/papers/${encodeURIComponent(paperId)}/notes`, {
        method: "POST",
        ...body(note),
      }),

    /** Any subset of fields. A field left out is untouched — which is what
     *  lets typing not move the box, and dragging not rewrite the text. */
    update: (
      noteId: string,
      patch: {
        x?: number;
        y?: number;
        w?: number;
        h?: number;
        style?: PageNoteStyle;
        color?: string;
        size?: number;
        body?: string;
      },
    ): Promise<PageNoteRow> =>
      json<PageNoteRow>(`/notes/${encodeURIComponent(noteId)}`, {
        method: "PATCH",
        ...body(patch),
      }),

    remove: (noteId: string): Promise<void> =>
      empty(`/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" }),
  },

  tape: {
    list: (paperId: string, kind: PdfKind, signal?: AbortSignal): Promise<TapeRow[]> =>
      json<TapeRow[]>(`/papers/${encodeURIComponent(paperId)}/tape?kind=${kind}`, {
        signal,
      }),

    create: (
      paperId: string,
      strip: {
        kind: PdfKind;
        page: number;
        x: number;
        y: number;
        w: number;
        h: number;
        angle?: number;
        /** Omit for a straight strip; send the pen's own path for a freehand one. */
        points?: { x: number; y: number }[];
      },
    ): Promise<TapeRow> =>
      json<TapeRow>(`/papers/${encodeURIComponent(paperId)}/tape`, {
        method: "POST",
        ...body(strip),
      }),

    /** A resize, a straighten, or a reveal/cover tap — any subset of fields.
     *  Only the keys actually present are sent, so leaving one out really
     *  means "untouched", not "reset to a default" (mirrors the backend's
     *  `...` sentinel in `update_tape`). */
    update: (
      tapeId: string,
      patch: Partial<{
        x: number;
        y: number;
        w: number;
        h: number;
        angle: number;
        revealed: boolean;
        /** Rewrites a freehand strip's path — what a lasso transform must
         *  do, since a moved or rotated strip traces a different curve. */
        points: { x: number; y: number }[] | null;
      }>,
    ): Promise<TapeRow> =>
      json<TapeRow>(`/tape/${encodeURIComponent(tapeId)}`, {
        method: "PATCH",
        ...body(patch),
      }),

    remove: (tapeId: string): Promise<void> =>
      empty(`/tape/${encodeURIComponent(tapeId)}`, { method: "DELETE" }),
  },

};

/** The Authorization header as an object, or `{}` when signed out. Exported for
 *  the rare consumer that builds its own request (pdf.js); prefer `api`. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token === null ? {} : { Authorization: `Bearer ${token}` };
}

// Mirrors the backend Pydantic schemas (pharos/api/schemas.py).

/* ------------------------------------------------------------------- auth */

/** A user account. Never carries the password hash or the token epoch — the
 *  backend does not serialise either, and the client has no use for them. */
export interface AuthUser {
  id: string;
  /** Stored casefolded by the backend, so this is the canonical form. */
  email: string;
  /** Null until the user sets one; the UI falls back to the email. */
  display_name: string | null;
  created_at: string;
  /** Null on the very first login response, before the row is stamped. */
  last_login_at: string | null;
  /**
   * Does this account get whole-PDF translation? Server-owned, not a device
   * preference.
   *
   * Optional rather than required even though `UserOut` always sends it: an
   * `AuthUser` can also come from the session blob in localStorage, and one
   * written by a build predating this field genuinely lacks it. Read it through
   * `pdfTranslationEnabled` in `store.ts`, which resolves absent to `true` —
   * mirroring `pdf_translation_enabled()` in `pharos/api/auth.py`, so a missing
   * field never silently hides a feature the account actually has.
   */
  pdf_translation?: boolean;
  /**
   * Operator account. Grants no special access yet — there are no admin-gated
   * endpoints — so nothing keys off this today; it is surfaced so a future
   * admin UI can appear without another migration. Optional for the same reason
   * as `pdf_translation`: a session blob from an older build may lack it.
   */
  is_admin?: boolean;
}

/** The response to register and login: a token plus the account it belongs to. */
export interface AuthSession {
  token: string;
  /** ISO 8601. The client drops the session once this passes rather than
   *  waiting for a 401, so an expired tab lands on sign-in, not on an error. */
  expires_at: string;
  user: AuthUser;
}

/**
 * What an anonymous sign-in screen is allowed to know about this instance:
 * whether to draw a sign-up form. `GET /api/auth/status` deliberately says
 * nothing about who is registered or how the server is configured.
 */
export interface AuthStatus {
  allow_registration: boolean;
}

export interface RegisterBody {
  email: string;
  password: string;
  display_name?: string;
}

export interface LoginBody {
  email: string;
  password: string;
}

export interface PasswordChangeBody {
  current_password: string;
  new_password: string;
}

/* ----------------------------------------------------------------- zotero */

/** Mirrors ZoteroLink.status on the backend. */
export type ZoteroLinkStatus = "linked" | "syncing" | "error";

/**
 * The state of this user's Zotero connection.
 *
 * Never carries `api_key` — the backend stores the key and redacts it, exactly
 * as DailyProvider does. A client that received it would have no use for it and
 * every opportunity to leak it.
 */
export interface ZoteroStatus {
  /** False = no link row at all; every field below is then null/zero. */
  linked: boolean;
  /** Whether this deployment has the server-side OAuth application and
   * credential-encryption secret required for browser authorization. */
  oauth_available: boolean;
  /**
   * The in-flight or most recent sync run, when the backend process knows of
   * one. Null after a backend restart — which is the truth, not a gap: that
   * process did not run the sync and cannot report its added/updated split.
   */
  sync: ZoteroSyncSummary | null;
  /** Zotero's library version last stored; where the next incremental sync
   *  resumes from. */
  library_version: number | null;
  /** Null until a link exists. "error" means the last sync failed, not that
   *  the link is broken — the credentials may still be good. */
  status: ZoteroLinkStatus | null;
  /** The Zotero-side user id, echoed back so the UI can show what is linked. */
  zotero_user_id: string | null;
  last_sync_at: string | null;
  /** The backend's recorded failure from the last sync; null once one succeeds. */
  last_error: string | null;
  item_count: number;
}

/** A short-lived Zotero consent URL created for the signed-in Pharos user. */
export interface ZoteroOAuthStart {
  authorize_url: string;
  expires_at: string;
}

/**
 * How the Zotero consent round trip ended.
 *
 * The backend sends the browser back to the product root with `?zotero=<one of
 * these>`, so this is the shape of that redirect rather than of a JSON body —
 * which is why it lives here with the other Zotero wire types.
 */
export type ZoteroOAuthResult =
  | "connected"
  | "cancelled"
  | "expired"
  | "invalid"
  | "busy"
  | "error";

export interface ZoteroLinkBody {
  zotero_user_id: string;
  api_key: string;
}

/**
 * What one sync run did — the backend's `SyncSummary`.
 *
 * Every count is nullable because a run that is still `running` has not
 * produced them yet, and a run this process only half-remembers reports what it
 * actually knows rather than zero. Rendering these requires a null check; that
 * is the point.
 */
export interface ZoteroSyncSummary {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  added: number | null;
  updated: number | null;
  /** Items Zotero returned and the backend understood, i.e. added + updated. */
  total: number | null;
  /** Items that could not become a paper — almost always a title-less entry. */
  skipped: number | null;
  library_version: number | null;
  error: string | null;
}

/* ------------------------------------------------------------------- jobs */

export type JobStatus = "queued" | "running" | "done" | "error";

export interface Job {
  id: string;
  paper_id: string;
  status: JobStatus;
  stage: string;
  progress: number; // 0–100
  translator_type: string;
  target_lang: string;
  error: string | null;
  tokens: number | null;
  total_seconds: number | null;
  has_mono: boolean;
  has_dual: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Paper {
  id: string;
  title: string;
  orig_filename: string;
  page_count: number | null;
  source: string;
  source_lang: string;
  added_at: string;
  /** Already split by the backend; empty when extraction found nothing usable. */
  authors: string[];
  year: number | null;
  venue: string | null;
  /** Bare DOI — the backend strips any https://doi.org/ prefix. */
  doi: string | null;
  abstract: string | null;
  /** Which extractor won: "pdf" | "crossref" | "arxiv" | "manual". */
  meta_source: string | null;
  latest_job: Job | null;
}

/** Body accepted by the direct arXiv import endpoint. */
export interface ArxivImportBody {
  input: string;
}

export type PdfKind = "original" | "mono" | "dual";

/* ------------------------------------------------------------------- ink */

/** One stylus sample: PDF points at scale 1, bottom-left origin, plus the
 *  pressure (0..1) captured at that instant. Mirrors `services/ink.Point`. */
export interface InkPoint {
  x: number;
  y: number;
  p: number;
}

/** One stored stroke — one pen-down to pen-up gesture. Mirrors `InkOut`. */
export interface InkStrokeRow {
  id: string;
  paper_id: string;
  /** The rendition this stroke was drawn on; never painted on another. */
  kind: PdfKind;
  /** 1-based page within that rendition. */
  page: number;
  points: InkPoint[];
  /** Token name resolved to a `--c-ink-*` CSS variable — never a hex value. */
  color: string;
  /** Stroke width in PDF points, so width scales with zoom like ink on paper. */
  width: number;
  created_at: string;
}

/* ------------------------------------------------------------------ daily */

/** A paper is only "done" once the reading layer actually produced a card.
 *  "pending" is a first-class, honest state: fetched but not yet read. */
export type DailyReadStatus = "pending" | "done" | "error";

/** The four-part card body. Chinese, 1–3 sentences each, no formulas.
 *  Every key is optional: the backend serialises whatever the model produced
 *  (`dict[str, str]`) without asserting the card is complete, so a consumer that
 *  assumed four guaranteed strings would be asserting something untrue. */
export interface DailyHighlights {
  contribution?: string;
  innovation?: string;
  method?: string;
  results?: string;
}

/** 0–10, one decimal. `recommendation` is the weighted overall call.
 *  Optional for the same reason as DailyHighlights — `dict[str, float]`. */
export interface DailyScores {
  relevance?: number;
  recency?: number;
  popularity?: number;
  quality?: number;
  recommendation?: number;
}

export interface DailyPaper {
  id: string;
  /** Version-stripped, e.g. "2601.01234" — not "2601.01234v2". */
  arxiv_id: string;
  date: string; // "YYYY-MM-DD"
  title: string;
  /** Already split by the backend; empty when arXiv gave no author list. */
  authors: string[];
  /** English original — the digest translates into summary_zh, it does not
   *  replace this. Null when arXiv's entry carried no abstract. */
  abstract: string | null;
  categories: string[];
  matched_domain: string | null;
  matched_keywords: string[];
  /** Both nullable: a row imported before URLs were recorded has neither. */
  arxiv_url: string | null;
  pdf_url: string | null;
  published_at: string | null;
  venue: string | null;
  read_status: DailyReadStatus;
  /** All four are null while read_status is "pending" or "error" — never faked. */
  summary_zh: string | null;
  highlights: DailyHighlights | null;
  scores: DailyScores | null;
  /** Denormalised copy of scores.recommendation so the backend can ORDER BY it. */
  score_recommendation: number | null;
  read_model: string | null;
  read_at: string | null;
  read_error: string | null;
  /** Set once the paper has been pulled into 文库. */
  imported_paper_id: string | null;
  created_at: string;
}

export type DailyRunStatus = "running" | "done" | "error";

export interface DailyRun {
  id: string;
  date: string;
  status: DailyRunStatus;
  fetched: number;
  read_done: number;
  read_failed: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/** One row of the date picker — counts let the UI show progress per day. */
export interface DailyDate {
  date: string;
  total: number;
  read: number;
  pending: number;
  failed: number;
}

export interface DailyDay {
  date: string;
  total: number;
  /** Null for a date that has never been swept in this install. */
  run: DailyRun | null;
  papers: DailyPaper[];
}

/** Never carries the API key — the backend redacts it before serialising. */
export interface DailyProvider {
  name: string;
  model: string;
  base_url: string;
  configured: boolean;
}

export interface DailyStatus {
  /** False = papers are still fetched, but nothing gets read. The UI must say so. */
  llm_configured: boolean;
  provider: DailyProvider | null;
  directions: string[];
  last_run: DailyRun | null;
  /** Today's counts by the backend's clock, or null if today is unswept.
   *  A DateSummaryOut, not a date string — the backend already sends the date
   *  inside it, so the client never guesses the timezone either way. */
  today: DailyDate | null;
  /** The date of a sweep running RIGHT NOW, or null. This — not `last_run` — is
   *  the authoritative liveness signal: `last_run` can read "running" for a row
   *  orphaned by a restart, which would poll forever. */
  sweeping: string | null;
}

/* --------------------------------------- portable Daily Vault (version 1) */

export interface DailyVaultSettings {
  kind: "pharos.daily.settings";
  schema_version: 1;
  categories: string[];
  max_per_day: number;
  enabled: boolean;
}

export interface DailyVaultDirection {
  name: string;
  keywords: string[];
  enabled: boolean;
  position: number;
}

export interface DailyVaultProfile {
  kind: "pharos.daily.profile";
  schema_version: 1;
  /** Filled by the client before a directory snapshot is written. */
  timezone: string | null;
  settings: DailyVaultSettings;
  directions: DailyVaultDirection[];
  updated_at: string | null;
}

export interface DailyVaultRun {
  status: DailyRunStatus;
  fetched: number;
  read_done: number;
  read_failed: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/** A portable paper snapshot contains no DB, account, or private-library id. */
export interface DailyVaultPaper {
  kind: "pharos.daily.paper";
  schema_version: 1;
  arxiv_id: string;
  date: string;
  rank: number;
  title: string;
  authors: string[];
  abstract: string | null;
  categories: string[];
  matched_direction: string | null;
  matched_keywords: string[];
  arxiv_url: string | null;
  pdf_url: string | null;
  published_at: string | null;
  venue: string | null;
  read_status: DailyReadStatus;
  summary_zh: string | null;
  highlights: DailyHighlights | null;
  scores: DailyScores | null;
  read_model: string | null;
  read_at: string | null;
  read_error: string | null;
  created_at: string;
}

export interface DailyVaultDay {
  kind: "pharos.daily.issue";
  schema_version: 1;
  date: string;
  run: DailyVaultRun | null;
  papers: DailyVaultPaper[];
}

export interface DailyVaultArchive {
  kind: "pharos.daily.archive";
  schema_version: 1;
  vault_id: string | null;
  exported_at: string;
  generator: string;
  profile: DailyVaultProfile;
  days: DailyVaultDay[];
}

export interface DailyVaultImportResult {
  days_seen: number;
  papers_added: number;
  papers_updated: number;
  papers_unchanged: number;
  directions_restored: number;
  profile_restored: boolean;
}

/* ------------------------------------------- 每日论文 settings (per-user) */

/**
 * One of the caller's own research directions.
 *
 * Mirrors `DirectionOut` in `pharos/api/directions.py`. Note `keywords` is an
 * array here but a newline-joined string in the column: the backend converts on
 * the way out, so a client never sees the storage shape.
 *
 * Matching happens at QUERY time, not at ingest time — editing one of these
 * re-ranks the feed on the next request, with nothing re-fetched and nothing
 * re-read. That is why the settings editor invalidates the feed queries but
 * never asks for a sweep.
 */
export interface UserDirection {
  id: string;
  name: string;
  /** Lower-cased, de-duplicated, in first-seen order — the backend's parse. */
  keywords: string[];
  /** Disabled directions are still listed here; they just stop matching. */
  enabled: boolean;
  /** Display order AND the tie-break when a paper matches several directions. */
  position: number;
  created_at: string;
}

/** `keywords` may be free text (one per line or comma separated) or a list —
 *  the backend normalises both identically, so the editor posts what was typed. */
export interface DirectionCreateBody {
  name: string;
  keywords: string | string[];
  enabled?: boolean;
}

/** Omitted keys are left alone. Nothing here is nullable: an explicit null is
 *  dropped by the backend, and a body that changes nothing is a 400. */
export interface DirectionPatchBody {
  name?: string;
  keywords?: string | string[];
  enabled?: boolean;
  position?: number;
}

/**
 * The caller's sweep settings.
 *
 * `categories` widens the SHARED net: the daily sweep fetches the union of every
 * user's categories in one arXiv request, so adding one never starts a private
 * crawl — and never back-fills days already swept.
 */
export interface DailyConfig {
  categories: string[];
  max_per_day: number;
  enabled: boolean;
  /**
   * Have the defaults been copied into this account yet?
   *
   * Exposed so the UI can tell "we gave you these" from "you chose these", and
   * so that an empty direction list reads as a deliberate deletion rather than
   * a fresh account — the backend will not hand the defaults back once this is
   * true, which is exactly why the empty state has to offer to restore them.
   */
  seeded: boolean;
  updated_at: string | null;
}

export interface DailyConfigPatchBody {
  categories?: string | string[];
  max_per_day?: number;
  enabled?: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/* ----------------------------------------------------------------- search */

/** Which column produced the hit. Mirrors the backend's `_FIELD_ORDER`. */
export type SearchField = "title" | "abstract" | "authors" | "full_text";

export interface SearchHit {
  paper_id: string;
  title: string;
  /**
   * HTML-escaped text with `<mark>…</mark>` around the matched terms — the
   * backend escapes with `html.escape(quote=True)` and only then swaps its
   * control-character sentinels for the tags, so nothing else in here is live.
   *
   * The client still does NOT hand this to `dangerouslySetInnerHTML`: see
   * `renderSnippet` in ItemList. Parsing the two known tags out and rendering
   * text nodes costs a few lines and removes the whole question of whether an
   * escaping bug upstream becomes stored XSS down here.
   */
  snippet: string;
  field: SearchField;
  /** Higher is more relevant. Comparable only within one response — the fts5
   *  and like engines score on unrelated scales. */
  rank: number;
}

export interface SearchResponse {
  query: string;
  hits: SearchHit[];
  /** Matches across the whole library, not just this page. */
  total: number;
  limit: number;
  offset: number;
  /** "fts5" | "like". Reported so the UI can say search is degraded rather
   *  than leave a user wondering why ranking looks odd. */
  engine: string;
}

/* ------------------------------------------------------ collections & tags */

/** A folder, without its subtree. */
export interface Collection {
  id: string;
  name: string;
  parent_id: string | null;
  position: number;
  /** Papers filed DIRECTLY here, trashed ones excluded. Deliberately not a
   *  roll-up over descendants, so a parent's badge does not double-count. */
  paper_count: number;
  created_at: string;
}

/** A folder with its children nested inside it. */
export interface CollectionNode extends Collection {
  children: CollectionNode[];
}

export interface CollectionsResponse {
  collections: CollectionNode[];
  /** The 我的文库 badge. */
  all_count: number;
  /** The 未分类 badge — computed as "in no collection", never a stored folder,
   *  which is why it has no id and cannot be filed into. */
  uncategorised_count: number;
}

export interface CollectionCreateBody {
  name: string;
  parent_id?: string | null;
}

/**
 * Omitted keys are left alone; an explicit `parent_id: null` moves the folder
 * to the top level. `name` may not be null — the backend 400s rather than
 * stringifying it into a folder called "None".
 */
export interface CollectionPatchBody {
  name?: string;
  parent_id?: string | null;
  position?: number;
}

/** `promoted_children` is how many folders moved up a level: deleting a folder
 *  keeps its subtree, one level shallower, rather than cascading it away. */
export interface CollectionDeleted {
  id: string;
  promoted_children: number;
}

/** `added` counts only papers that were not already filed here — the call is
 *  idempotent, so re-filing the same paper reports 0, not 1. */
export interface CollectionAddResult {
  added: number;
  collection: Collection;
}

/**
 * An allow-listed token name, never a colour value — the backend 400s a hex.
 * The UI resolves each to a `--c-tag-*` custom property.
 */
export type TagColor = "amber" | "blue" | "green" | "red" | "purple" | "grey";

export interface Tag {
  id: string;
  name: string;
  /** Null for the neutral chip. */
  color: TagColor | null;
  paper_count: number;
  created_at: string;
}

export interface TagCreateBody {
  name: string;
  color?: TagColor | null;
}

export interface TagPatchBody {
  name?: string;
  color?: TagColor | null;
}

/* ------------------------------------------------------ research workspace */

export type DiscoverySource = "arxiv" | "openalex";
export type LiteratureSearchStatus = "running" | "complete" | "partial" | "error";
export type LiteratureAnalysisMode = "rules" | "llm";

/** One de-duplicated paper returned by a literature discovery run. */
export interface LiteratureResult {
  id: string;
  search_id: string;
  title: string;
  authors: string[];
  abstract: string;
  year: number | null;
  venue: string | null;
  doi: string | null;
  url: string | null;
  pdf_url: string | null;
  /** A paper can be corroborated by both providers after de-duplication. */
  sources: string[];
  source_ids: Record<string, string>;
  citation_count: number | null;
  rank: number;
  analysis_mode: LiteratureAnalysisMode;
  analysis_model: string | null;
  analysis_warning: string | null;
  summary_zh: string;
  contribution: string;
  core_trick: string;
  method: string;
  results: string;
  limitations: string;
  created_at: string;
}

export interface LiteratureSearch {
  id: string;
  project_id: string | null;
  query: string;
  sources: string[];
  status: LiteratureSearchStatus;
  result_count: number;
  /** Per-provider failures. A partial run keeps successful providers' results. */
  errors: Record<string, string>;
  created_at: string;
  completed_at: string | null;
  results: LiteratureResult[];
}

export interface LiteratureSearchBody {
  query: string;
  project_id?: string;
  sources?: DiscoverySource[];
  limit?: number;
}

export type ResearchProjectStatus = "active" | "archived";
export type ResearchStage =
  | "discovery"
  | "ideation"
  | "planning"
  | "experimentation"
  | "analysis"
  | "claims"
  | "drafting"
  | "review"
  | "complete";

export type ProjectArtifactType =
  | "hypothesis"
  | "experiment_plan"
  | "result"
  | "claim"
  | "draft"
  | "review";
export type ProjectArtifactStatus = "draft" | "ready" | "verified" | "rejected";

export interface ProjectArtifact {
  id: string;
  project_id: string;
  stage: ResearchStage;
  type: ProjectArtifactType;
  title: string;
  body: string;
  status: ProjectArtifactStatus;
  created_at: string;
  updated_at: string | null;
}

export interface ProjectSource {
  id: string;
  project_id: string;
  result_id: string;
  /** Why this paper belongs in the project. */
  note: string | null;
  added_at: string;
  result: LiteratureResult;
}

export interface ResearchProject {
  id: string;
  name: string;
  description: string;
  research_question: string;
  status: ResearchProjectStatus;
  stage: ResearchStage;
  created_at: string;
  updated_at: string | null;
  source_count: number;
  artifact_count: number;
  sources: ProjectSource[];
  artifacts: ProjectArtifact[];
  /** Backend-owned capability boundary: records are persisted, not executed. */
  automation_notice: string;
}

export interface ResearchProjectCreateBody {
  name: string;
  description?: string;
  research_question?: string;
}

export interface ResearchProjectPatchBody {
  name?: string;
  description?: string;
  research_question?: string;
  status?: ResearchProjectStatus;
  stage?: ResearchStage;
}

export interface ProjectSourceCreateBody {
  result_id: string;
  note?: string;
}

export interface ProjectSourcePatchBody {
  note: string | null;
}

export interface ProjectArtifactCreateBody {
  stage: ResearchStage;
  type: ProjectArtifactType;
  title: string;
  body: string;
  status?: ProjectArtifactStatus;
}

export interface ProjectArtifactPatchBody {
  stage?: ResearchStage;
  type?: ProjectArtifactType;
  title?: string;
  body?: string;
  status?: ProjectArtifactStatus;
}

/* ------------------------------------------------------------------ admin */

/** One account as the administrator console lists it. */
export interface AdminUser {
  id: string;
  email: string;
  display_name: string | null;
  is_admin: boolean;
  is_active: boolean;
  pdf_translation: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface AdminUserPage {
  users: AdminUser[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminStats {
  users: number;
  admins: number;
  inactive_users: number;
  allow_registration: boolean;
}

/**
 * A model provider as configured on the server.
 *
 * `key_hint` is the key's last four characters and nothing more — the backend
 * never sends the secret, so the console can distinguish two keys during a
 * rotation without ever holding one.
 */
export interface AdminProvider {
  name: string;
  label: string;
  base_url: string | null;
  model: string;
  configured: boolean;
  key_hint: string | null;
  /** Which jobs this provider serves: "translate" and/or "chat". */
  roles: string[];
}

export interface AdminProviders {
  providers: AdminProvider[];
  translator: string;
  chat_provider: string;
  /** The engine actually in force after the missing-key fallback.
   *
   *  NOT comparable to `translator` by equality: for `openai` and `custom` this
   *  holds the wire format's name (`openai_compatible`), which never equals the
   *  provider name. Use `isTranslationDegraded` in `lib/providers.ts` —
   *  assuming the two are comparable is what put a permanent false
   *  "translation degraded" banner in the admin console. */
  effective_translator: string;
}

export interface AdminProbeResult {
  name: string;
  ok: boolean;
  latency_ms: number | null;
  detail: string | null;
}

/** Fields an administrator may change on another account. Email and password
 *  are deliberately absent — changing them is an account takeover. */
export interface AdminUserPatch {
  is_admin?: boolean;
  is_active?: boolean;
  pdf_translation?: boolean;
  display_name?: string;
}

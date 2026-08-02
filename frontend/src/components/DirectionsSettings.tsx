/**
 * 每日论文 settings — the caller's own research directions and sweep config.
 *
 * The architecture this renders is worth stating, because it explains why the
 * screen says what it says:
 *
 *   - The daily sweep is GLOBAL. One arXiv fetch and one LLM reading serve every
 *     user, because a paper's Chinese summary is a fact about the paper, not
 *     about who reads it.
 *   - MATCHING is per-user and happens at QUERY time. Which papers you see,
 *     which direction they fall under, and how relevant they are is derived when
 *     the feed is requested, from the directions below.
 *
 * Two consequences the UI has to be honest about, and both are told to the user
 * rather than left to be discovered:
 *
 *   1. Editing a direction re-ranks the feed immediately — nothing is re-fetched
 *      and nothing is re-read. So this component invalidates the feed queries
 *      and never asks for a sweep.
 *   2. A newly added arXiv category cannot back-fill. It widens the shared net
 *      from the NEXT sweep onward; days already fetched will never contain a
 *      paper nobody was fetching at the time.
 *
 * Rendered only while the 每日论文 tab is showing (SettingsModal mounts it
 * conditionally), which is also how its queries stay off the network the rest of
 * the time and how every draft below is discarded on close.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { Icons } from "../design/icons";
import { api } from "../api/client";
import type { UserDirection } from "../api/types";
import "./DirectionsSettings.css";

/* ------------------------------------------------------------------ limits */

/**
 * Mirrors of the service's ceilings in `pharos/daily/user_directions.py`.
 *
 * Copied so the editor can warn *while typing* instead of after a round trip.
 * They are advisory here and authoritative there: every one of these is
 * re-checked by the backend, and this file never blocks a submit on its own
 * reading of a limit — see `DirectionEditor`.
 */
const MAX_DIRECTIONS = 40;
const MAX_NAME_CHARS = 64;
const MAX_KEYWORDS = 80;
const MAX_KEYWORD_CHARS = 80;
const MAX_KEYWORDS_TOTAL_CHARS = 2000;
const MAX_CATEGORIES = 24;
const MIN_PER_DAY = 1;
const MAX_PER_DAY = 200;

/**
 * The seven defaults, mirrored from `pharos/daily/directions.py`.
 *
 * Duplicated deliberately and narrowly: the backend seeds an account exactly
 * once (`UserDailyConfig.seeded`), on purpose, so a user who deletes every
 * direction is not handed them back on the next request. That is the right
 * behaviour and it leaves one gap — a user who deletes everything and changes
 * their mind has no way back. This list fills only that gap; it is posted
 * through the ordinary create endpoint, so the backend still parses, validates
 * and orders it.
 *
 * Nothing else reads this. If it ever drifts from the backend's hand-tuned
 * lists, the only thing affected is what the 恢复默认方向 button restores —
 * matching is done server-side against the stored rows, never against this.
 */
const DEFAULT_DIRECTIONS: readonly { name: string; keywords: readonly string[] }[] = [
  {
    name: "VLA",
    keywords: [
      "vision-language-action",
      "vision language action",
      "vla model",
      "vla policy",
      "robot policy",
      "embodied policy",
      "manipulation policy",
      "robotic manipulation",
      "openvla",
      "rt-2",
      "rt2",
      "pi-0",
      "pi0",
      "language-conditioned policy",
      "instruction-following manipulation",
    ],
  },
  {
    name: "World Model",
    keywords: [
      "world model",
      "world models",
      "neural simulator",
      "latent dynamics",
      "dynamics model",
      "video prediction",
      "video generation for robotics",
      "video world model",
      "genie",
      "navworld",
      "dreamerv3",
      "policy world model",
    ],
  },
  {
    name: "WAM",
    keywords: [
      "world action model",
      "wam ",
      "action world model",
      "joint action prediction",
      "unified action model",
    ],
  },
  {
    name: "VGGT",
    keywords: [
      "vggt",
      "vggsfm",
      "dust3r",
      "mast3r",
      "feed-forward 3d",
      "feedforward 3d",
      "3d foundation model",
      "monocular 3d reconstruction",
      "novel view synthesis",
      "neural radiance",
      "gaussian splatting",
      "3d scene reconstruction",
      "geometry grounded",
      "visual geometry",
    ],
  },
  {
    name: "Agent",
    keywords: [
      "llm agent",
      "llm-based agent",
      "llm-powered agent",
      "embodied agent",
      "multi-agent",
      "multi agent",
      "agentic",
      "agent framework",
      "react agent",
      "reasoning and acting",
      "tool-use agent",
      "tool use agent",
      "gui agent",
      "web agent",
      "planning agent",
      "language agent",
      "foundation model agent",
      "agentic workflow",
      "autonomous agent",
      "agent benchmark",
    ],
  },
  {
    name: "Diffusion",
    keywords: [
      "diffusion policy",
      "diffusion model",
      "diffusion transformer",
      " dit ",
      "denoising diffusion",
      "flow matching",
      "latent diffusion",
      "consistency model",
      "score-based",
      "score based generative",
      "rectified flow",
      "video diffusion",
      "stable diffusion",
      "diffusion-based",
      "diffusion based policy",
      "image diffusion",
      "guided diffusion",
      "classifier-free guidance",
    ],
  },
  {
    name: "Multi-modal",
    keywords: [
      "multimodal large language model",
      "multi-modal large language model",
      "mllm",
      "vision-language model",
      "vision language model",
      "vlm",
      "video-llm",
      "video llm",
      "audio-visual",
      "embodied chain-of-thought",
      "spatial reasoning",
      "embodied reasoning",
      "long-horizon planning",
    ],
  },
];

/* ------------------------------------------------------------------ parsing */

const cx = (...parts: (string | false)[]): string => parts.filter(Boolean).join(" ");

/** Error text from a rejected mutation, without leaking a stack trace into the UI. */
const errText = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/**
 * The client's reading of `parse_keywords` in `pharos/daily/user_directions.py`:
 * split on newlines and commas, trim, lower-case, drop blanks, de-duplicate
 * preserving first-seen order.
 *
 * This exists to be SHOWN, not to be trusted. Silently normalising what someone
 * typed and then matching against something they never saw is how you get "why
 * didn't this match" — so the editor renders this list back as the terms that
 * will actually be searched for. The backend re-parses the same raw text and its
 * answer is the one that gets stored; if the two ever disagree (Python's
 * `str.lower` and JS's `toLowerCase` differ on a handful of exotic characters)
 * the stored value wins and the list re-renders from the server's response.
 */
function parseKeywords(raw: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(/[\n,]/)) {
    const term = part.trim().toLowerCase();
    if (term === "" || seen.has(term)) continue;
    seen.add(term);
    out.push(term);
  }
  return out;
}

/** arXiv's category grammar, mirroring `_CATEGORY_RE`: an archive, optionally
 *  followed by `.subject`. A shape check, not an allow-list — `cond-mat.stat-mech`
 *  and `econ.EM` are a user, not a typo. */
const CATEGORY_RE =
  /^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*(?:\.[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)?$/;

/** Mirrors `_canonical_category`: two-letter subject classes upper-case
 *  (`cs.RO`), longer hyphenated ones lower-case (`cond-mat.stat-mech`). */
function canonicalCategory(value: string): string {
  const dot = value.indexOf(".");
  if (dot < 0) return value.toLowerCase();
  const archive = value.slice(0, dot).toLowerCase();
  const subject = value.slice(dot + 1);
  return `${archive}.${subject.length === 2 ? subject.toUpperCase() : subject.toLowerCase()}`;
}

/** What the categories field parses to, plus whatever it could not make sense
 *  of — surfaced rather than dropped, so a typo is visible before the save. */
interface ParsedCategories {
  categories: string[];
  invalid: string[];
}

/* Both parsers are module-private, and stay that way: a non-component export
   from a file that also exports a component defeats React Fast Refresh (Vite
   reports "export is incompatible" and falls back to a full reload), which on
   this screen means losing whatever direction was half-typed on every save of
   the file. If they are ever needed elsewhere, move them to their own module
   rather than exporting them from here. */

/** Mirrors `parse_categories`. Splits on commas, newlines and any whitespace. */
function parseCategories(raw: string): ParsedCategories {
  const seen = new Set<string>();
  const categories: string[] = [];
  const invalid: string[] = [];
  for (const part of raw.split(/[\n,\s]+/)) {
    const token = part.trim();
    if (token === "") continue;
    if (token.length > 32 || !CATEGORY_RE.test(token)) {
      if (!invalid.includes(token)) invalid.push(token);
      continue;
    }
    const canonical = canonicalCategory(token);
    if (seen.has(canonical)) continue;
    seen.add(canonical);
    categories.push(canonical);
  }
  return { categories, invalid };
}

/* --------------------------------------------------------------- feed cache */

/**
 * Everything derived from directions, invalidated by name.
 *
 * Precise on purpose. Invalidating the whole `["daily"]` prefix would also
 * refetch `["daily","directions"]` and `["daily","config"]` — the very queries
 * the mutation just updated — turning every save into two extra round trips for
 * data it already has. Called only from mutation success handlers, never from
 * an effect: an unconditional effect that invalidates has previously cost this
 * codebase an infinite refetch loop.
 */
function invalidateFeed(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: ["daily", "day"] });
  void qc.invalidateQueries({ queryKey: ["daily", "dates"] });
  void qc.invalidateQueries({ queryKey: ["daily", "status"] });
}

/* -------------------------------------------------------------------- editor */

interface EditorProps {
  /** Prefilled for an edit; empty strings for a new direction. */
  initialName: string;
  initialKeywords: string;
  submitLabel: string;
  pending: boolean;
  /** The backend's refusal, if the last submit was refused. */
  error: unknown;
  onSubmit: (values: { name: string; keywords: string }) => void;
  onCancel: () => void;
}

/**
 * The add/edit form. One component for both, because they are the same form —
 * the only difference is what it starts with and what the submit button says.
 *
 * Mounted with a `key` tied to the row being edited, so switching rows resets
 * the draft rather than carrying half of one direction into another.
 */
function DirectionEditor({
  initialName,
  initialKeywords,
  submitLabel,
  pending,
  error,
  onSubmit,
  onCancel,
}: EditorProps): JSX.Element {
  const [name, setName] = useState(initialName);
  const [keywords, setKeywords] = useState(initialKeywords);

  const parsed = useMemo(() => parseKeywords(keywords), [keywords]);
  const totalChars = parsed.reduce((n, k) => n + k.length, 0);
  const tooLong = parsed.filter((k) => k.length > MAX_KEYWORD_CHARS);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    // The RAW text goes to the server, not `parsed`: the backend's parse is the
    // one that decides what gets stored, and posting our interpretation instead
    // would quietly make this file the authority on matching.
    onSubmit({ name: name.trim(), keywords });
  };

  return (
    <form className="ph-dir-editor" onSubmit={submit}>
      <input
        className="ph-set-input"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="方向名称，例如 VLA"
        aria-label="方向名称"
        maxLength={MAX_NAME_CHARS}
        autoFocus
      />
      <textarea
        className="ph-dir-textarea"
        value={keywords}
        onChange={(e) => setKeywords(e.target.value)}
        placeholder={"关键词，一行一个或用逗号分隔\nvision-language-action\nopenvla, rt-2"}
        aria-label="关键词"
        rows={5}
      />

      {/* Show the parse back. A term only fires if it appears verbatim in the
          title or abstract, so the user needs to see the exact strings — not a
          tidied-up version of what they typed. */}
      <div className="ph-dir-parsed">
        <div className="ph-dir-parsed-head">
          {parsed.length === 0
            ? "还没有关键词 —— 没有关键词的方向什么都匹配不到"
            : `将匹配这 ${parsed.length} 个词（标题或摘要中出现任意一个即命中）`}
        </div>
        {parsed.length > 0 && (
          <div className="ph-dir-chips">
            {parsed.map((k) => (
              <span key={k} className={cx("ph-dir-chip", k.length > MAX_KEYWORD_CHARS && "ph-dir-chip--bad")}>
                {/* Leading and trailing spaces are load-bearing in terms like
                    "wam " — render them visibly rather than letting the chip
                    collapse them into a word the user did not write. */}
                {k.replace(/^ | $/g, "␣")}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Advisory, not enforced. The submit stays live so the backend remains
          the authority on its own limits — but the user finds out here. */}
      {parsed.length > MAX_KEYWORDS && (
        <div className="ph-dir-warn">关键词太多了（最多 {MAX_KEYWORDS} 个，现在 {parsed.length} 个）</div>
      )}
      {totalChars > MAX_KEYWORDS_TOTAL_CHARS && (
        <div className="ph-dir-warn">
          关键词总长度超出上限（最多 {MAX_KEYWORDS_TOTAL_CHARS} 字，现在 {totalChars} 字）
        </div>
      )}
      {tooLong.length > 0 && (
        <div className="ph-dir-warn">
          有 {tooLong.length} 个关键词超过 {MAX_KEYWORD_CHARS} 字 —— 一整句话作为子串几乎不可能命中
        </div>
      )}
      {error !== null && error !== undefined && <div className="ph-set-err">{errText(error)}</div>}

      <div className="ph-dir-editor-acts">
        <button
          type="submit"
          className="ph-set-btn ph-set-btn--on"
          disabled={pending || name.trim() === "" || parsed.length === 0}
        >
          {pending ? "保存中…" : submitLabel}
        </button>
        <button type="button" className="ph-set-btn" onClick={onCancel} disabled={pending}>
          取消
        </button>
      </div>
    </form>
  );
}

/* ----------------------------------------------------------------- the tab */

export function DirectionsSettings(): JSX.Element {
  const qc = useQueryClient();

  const directionsQuery = useQuery({
    queryKey: ["daily", "directions"],
    queryFn: () => api.daily.directions.list(),
  });
  const configQuery = useQuery({
    queryKey: ["daily", "config"],
    queryFn: () => api.daily.config.get(),
  });

  const directions = directionsQuery.data;
  const config = configQuery.data;

  /** Which row is open in the editor; "new" means the add form. */
  const [editing, setEditing] = useState<string | null>(null);
  /** Two-step delete, matching 断开 → 确认断开 on the 账户 tab. */
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const afterWrite = () => {
    void qc.invalidateQueries({ queryKey: ["daily", "directions"] });
    invalidateFeed(qc);
  };

  const create = useMutation({
    mutationFn: (values: { name: string; keywords: string }) =>
      api.daily.directions.create(values),
    onSuccess: () => {
      setEditing(null);
      afterWrite();
    },
  });

  const update = useMutation({
    mutationFn: (vars: { id: string; name: string; keywords: string }) =>
      api.daily.directions.update(vars.id, { name: vars.name, keywords: vars.keywords }),
    onSuccess: () => {
      setEditing(null);
      afterWrite();
    },
  });

  /* Its own mutation rather than a reuse of `update`, so a failed rename cannot
     leave its error text sitting under an unrelated row's toggle. */
  const toggle = useMutation({
    mutationFn: (vars: { id: string; enabled: boolean }) =>
      api.daily.directions.update(vars.id, { enabled: vars.enabled }),
    onSuccess: afterWrite,
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.daily.directions.remove(id),
    onSuccess: () => {
      setConfirmDelete(null);
      afterWrite();
    },
  });

  /* Up/down rather than drag-and-drop. Position is not decoration — it is the
     tie-break when a paper matches several directions, so it decides which
     badge a paper wears. Two buttons that move one step are exact and
     keyboard-reachable; a half-built drag would be neither. */
  const reorder = useMutation({
    mutationFn: (ids: string[]) => api.daily.directions.reorder(ids),
    onSuccess: (rows) => {
      qc.setQueryData(["daily", "directions"], rows);
      invalidateFeed(qc);
    },
  });

  /**
   * The module's own switch.
   *
   * Its own mutation, saved on click rather than joined to the 抓取设置 draft
   * further down: a switch that silently needs a separate 保存 is a switch that
   * does not work. Defaults to `true` while the config is loading, because the
   * only thing this flag can do is hide the whole module, and a request that has
   * not come back yet is not an account that turned it off.
   */
  const digestEnabled = config?.enabled ?? true;
  const toggleDigest = useMutation({
    mutationFn: (enabled: boolean) => api.daily.config.update({ enabled }),
    onSuccess: (saved) => {
      qc.setQueryData(["daily", "config"], saved);
      // 每日论文 triages its whole empty state off this flag, and the shared
      // sweep pools its net only from the accounts that have it on.
      invalidateFeed(qc);
    },
  });

  /**
   * Re-create the defaults, one create call each.
   *
   * Sequential, not `Promise.all`: creates land in call order and position is
   * assigned on insert, so parallel requests would restore the seven defaults
   * in an arbitrary order — and order is the tie-break. A create that fails
   * (a name the user still has) is skipped rather than aborting the rest, and
   * the count of what actually landed is reported back.
   */
  const restore = useMutation({
    mutationFn: async (): Promise<number> => {
      let added = 0;
      for (const preset of DEFAULT_DIRECTIONS) {
        try {
          await api.daily.directions.create({
            name: preset.name,
            keywords: preset.keywords.join("\n"),
          });
          added += 1;
        } catch {
          /* already present, or refused — keep going and report the total */
        }
      }
      return added;
    },
    onSuccess: afterWrite,
  });

  /* ------------------------------------------------------------- sweep config */

  /* Drafts are `null` until the user types, and the field renders
     `draft ?? server`. That is deliberately not a `useState` seeded from the
     query plus an effect to re-seed it: the effect version re-runs on every
     refetch and either clobbers what is being typed or, if it invalidates,
     loops. Here there is no effect at all — a successful save clears the draft
     back to null and the field follows the server again. */
  const [catDraft, setCatDraft] = useState<string | null>(null);
  const [maxDraft, setMaxDraft] = useState<string | null>(null);

  const catText = catDraft ?? config?.categories.join(", ") ?? "";
  const maxText = maxDraft ?? (config === undefined ? "" : String(config.max_per_day));
  const parsedCats = useMemo(() => parseCategories(catText), [catText]);

  /* An emptied field is "leave it alone", not zero. `Number("")` is 0 and
     `Number.isInteger(0)` is true, so testing the parse alone would post
     `max_per_day: 0` for a cleared box — a 400 from the backend ("must be
     between 1 and 200") for something the user never typed. Blank is checked
     first and the key is then omitted entirely. */
  const maxBlank = maxText.trim() === "";
  const maxNumber = Number(maxText);
  const maxValid = !maxBlank && Number.isInteger(maxNumber);
  const maxOutOfRange =
    maxValid && (maxNumber < MIN_PER_DAY || maxNumber > MAX_PER_DAY);
  const maxUnparseable = !maxBlank && !Number.isInteger(maxNumber);
  const configDirty = catDraft !== null || maxDraft !== null;

  const saveConfig = useMutation({
    mutationFn: () =>
      api.daily.config.update({
        categories: catText,
        max_per_day: maxValid ? maxNumber : undefined,
      }),
    onSuccess: (saved) => {
      // Back to following the server — which also shows the user the canonical
      // spelling it settled on ("CS.ro" comes back as "cs.RO").
      setCatDraft(null);
      setMaxDraft(null);
      qc.setQueryData(["daily", "config"], saved);
      // Categories change what gets FETCHED, from the next sweep on. Today's
      // listing cannot gain a paper nobody fetched, but max_per_day does cap
      // what the feed returns, so the feed is refreshed either way.
      invalidateFeed(qc);
    },
  });

  /* ------------------------------------------------------------------ render */

  const move = (index: number, delta: number) => {
    if (directions === undefined) return;
    const target = index + delta;
    if (target < 0 || target >= directions.length) return;
    const ids = directions.map((d) => d.id);
    const [moved] = ids.splice(index, 1);
    ids.splice(target, 0, moved);
    reorder.mutate(ids);
  };

  const busy = create.isPending || update.isPending || remove.isPending || restore.isPending;

  return (
    <>
      <div className="ph-set-h">每日论文</div>

      {/* Said once, at the top, because it is the one thing about this screen
          that is not guessable from the controls. */}
      <div className="ph-dir-note">
        <span className="ph-set-ic ph-set-ic--tx2">
          <Icons.alert size={16} />
        </span>
        <div>
          <strong>方向是你自己的，抓取是大家共享的。</strong>
          改动方向立刻生效 —— 下次打开每日论文就会按新方向重新匹配和排序，不需要重新抓取，也不会重新花一次阅读的钱。
          但 arXiv 分类决定的是每天到底把哪些论文抓回来，这一步是全站共用一次请求：
          新加的分类要等下一次抓取才会带回论文，之前的日期不会补上。
        </div>
      </div>

      {/* The switch that governs the whole module. It lives here because the
          每日论文 view can only REPORT being switched off — it offers 前往设置
          as the fix, and a settings page with no switch on it is not one. */}
      <div className="ph-dir-row">
        <div className="ph-dir-main">
          <div className="ph-dir-name">
            每日论文
            {config !== undefined && !config.enabled && (
              <span className="ph-dir-badge">已关闭</span>
            )}
          </div>
          <div className="ph-dir-kws">
            关闭后论文不再进入「每日论文」，方向、抓取设置和历史日期都保留，重新开启即可恢复。
          </div>
        </div>
        <div className="ph-dir-acts">
          <button
            type="button"
            className={cx("ph-set-btn", digestEnabled && "ph-dir-btn--on")}
            onClick={() => toggleDigest.mutate(!digestEnabled)}
            disabled={configQuery.isPending || toggleDigest.isPending}
            aria-pressed={digestEnabled}
          >
            {digestEnabled ? "启用中" : "已关闭"}
          </button>
        </div>
      </div>
      {toggleDigest.isError && <div className="ph-set-err">{errText(toggleDigest.error)}</div>}

      {/* ------------------------------------------------------------ 方向 */}

      <div className="ph-set-sec ph-dir-sec">
        <div className="ph-set-sec-head">
          <span className="ph-set-ic ph-set-ic--tx2">
            <Icons.daily />
          </span>
          <div className="ph-set-sec-title">研究方向</div>
          {directions !== undefined && directions.length > 0 && (
            <div className="ph-dir-count">
              {directions.length} / {MAX_DIRECTIONS}
            </div>
          )}
        </div>

        {directionsQuery.isPending && <div className="ph-dir-muted">正在读取…</div>}
        {directionsQuery.isError && (
          <div className="ph-set-err">无法读取研究方向：{errText(directionsQuery.error)}</div>
        )}

        {/* A user who deleted everything. The backend will not re-seed them —
            `seeded` stays true on purpose — so this is a dead end unless the
            screen says so and offers the way out. */}
        {directions !== undefined && directions.length === 0 && editing !== "new" && (
          <div className="ph-dir-empty">
            <div className="ph-dir-empty-title">还没有任何研究方向</div>
            <div className="ph-dir-empty-desc">
              每日论文完全靠方向来筛选：一个方向都没有，你的每日论文就是空的 ——
              抓回来的论文一篇也不会显示。你可以自己新建一个，或者先把默认的七个方向恢复回来再慢慢改。
            </div>
            <div className="ph-dir-empty-acts">
              <button
                type="button"
                className="ph-set-btn ph-set-btn--on"
                onClick={() => restore.mutate()}
                disabled={restore.isPending}
              >
                {restore.isPending ? "恢复中…" : "恢复默认方向"}
              </button>
              <button type="button" className="ph-set-btn" onClick={() => setEditing("new")}>
                新建方向
              </button>
            </div>
            {restore.isError && <div className="ph-set-err">{errText(restore.error)}</div>}
            {restore.isSuccess && restore.data === 0 && (
              <div className="ph-set-err">默认方向一个也没能恢复，请改用「新建方向」。</div>
            )}
          </div>
        )}

        {directions !== undefined &&
          directions.map((d: UserDirection, i: number) =>
            editing === d.id ? (
              <DirectionEditor
                key={d.id}
                initialName={d.name}
                initialKeywords={d.keywords.join("\n")}
                submitLabel="保存"
                pending={update.isPending}
                error={update.isError ? update.error : null}
                onSubmit={(v) => update.mutate({ id: d.id, ...v })}
                onCancel={() => {
                  update.reset();
                  setEditing(null);
                }}
              />
            ) : (
              <div key={d.id} className={cx("ph-dir-row", !d.enabled && "ph-dir-row--off")}>
                <div className="ph-dir-order">
                  <button
                    type="button"
                    className="ph-dir-icb ph-dir-icb--up"
                    title="上移"
                    aria-label={`把 ${d.name} 上移`}
                    onClick={() => move(i, -1)}
                    disabled={i === 0 || reorder.isPending}
                  >
                    <Icons.caretD size={12} />
                  </button>
                  <button
                    type="button"
                    className="ph-dir-icb"
                    title="下移"
                    aria-label={`把 ${d.name} 下移`}
                    onClick={() => move(i, 1)}
                    disabled={i === directions.length - 1 || reorder.isPending}
                  >
                    <Icons.caretD size={12} />
                  </button>
                </div>

                <div className="ph-dir-main">
                  <div className="ph-dir-name">
                    {d.name}
                    {!d.enabled && <span className="ph-dir-badge">已停用</span>}
                  </div>
                  {/* The exact stored terms, so what is matched is never a mystery. */}
                  <div className="ph-dir-kws" title={d.keywords.join("\n")}>
                    {d.keywords.join(" · ")}
                  </div>
                </div>

                <div className="ph-dir-acts">
                  <button
                    type="button"
                    className={cx("ph-set-btn", d.enabled && "ph-dir-btn--on")}
                    onClick={() => toggle.mutate({ id: d.id, enabled: !d.enabled })}
                    disabled={toggle.isPending}
                    aria-pressed={d.enabled}
                  >
                    {d.enabled ? "启用中" : "已停用"}
                  </button>
                  <button
                    type="button"
                    className="ph-set-btn"
                    onClick={() => {
                      update.reset();
                      setConfirmDelete(null);
                      setEditing(d.id);
                    }}
                  >
                    编辑
                  </button>
                  {confirmDelete === d.id ? (
                    <button
                      type="button"
                      className="ph-set-btn ph-set-btn--danger"
                      onClick={() => remove.mutate(d.id)}
                      disabled={remove.isPending}
                    >
                      {remove.isPending ? "删除中…" : "确认删除"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="ph-set-btn"
                      onClick={() => setConfirmDelete(d.id)}
                    >
                      删除
                    </button>
                  )}
                </div>
              </div>
            ),
          )}

        {toggle.isError && <div className="ph-set-err">{errText(toggle.error)}</div>}
        {remove.isError && <div className="ph-set-err">{errText(remove.error)}</div>}

        {editing === "new" ? (
          <DirectionEditor
            key="new"
            initialName=""
            initialKeywords=""
            submitLabel="新建"
            pending={create.isPending}
            error={create.isError ? create.error : null}
            onSubmit={(v) => create.mutate(v)}
            onCancel={() => {
              create.reset();
              setEditing(null);
            }}
          />
        ) : (
          directions !== undefined &&
          directions.length > 0 && (
            <button
              type="button"
              className="ph-dir-add"
              onClick={() => {
                create.reset();
                setEditing("new");
              }}
              disabled={busy || directions.length >= MAX_DIRECTIONS}
            >
              <span className="ph-set-ic">
                <Icons.plus />
              </span>
              新建方向
            </button>
          )
        )}
      </div>

      {/* ------------------------------------------------------------ 抓取 */}

      <div className="ph-set-sec ph-dir-sec">
        <div className="ph-set-sec-head">
          <span className="ph-set-ic ph-set-ic--tx2">
            <Icons.cloud />
          </span>
          <div className="ph-set-sec-title">抓取范围</div>
        </div>

        {configQuery.isError && (
          <div className="ph-set-err">无法读取抓取设置：{errText(configQuery.error)}</div>
        )}

        <div className="ph-set-label">arXiv 分类</div>
        <input
          className="ph-set-input"
          value={catText}
          onChange={(e) => setCatDraft(e.target.value)}
          placeholder="cs.RO, cs.CV, cs.LG"
          aria-label="arXiv 分类"
          autoComplete="off"
          disabled={configQuery.isPending}
        />
        <div className="ph-dir-hint">
          这些分类决定了每天到底有哪些论文被抓回来。方向只能在抓回来的论文里筛选 ——
          分类之外的论文，写再多关键词也不会出现。逗号或空格分隔，最多 {MAX_CATEGORIES} 个。
        </div>

        {parsedCats.categories.length > 0 && (
          <div className="ph-dir-chips ph-dir-chips--cat">
            {parsedCats.categories.map((c) => (
              <span key={c} className="ph-dir-chip">
                {c}
              </span>
            ))}
          </div>
        )}
        {/* Shape-checked here for immediate feedback only. The save is left
            enabled: this regex is a copy, and a copy that has fallen behind
            arXiv must not be able to refuse something the server would accept. */}
        {parsedCats.invalid.length > 0 && (
          <div className="ph-dir-warn">看起来不像 arXiv 分类：{parsedCats.invalid.join("、")}</div>
        )}
        {parsedCats.categories.length > MAX_CATEGORIES && (
          <div className="ph-dir-warn">
            分类太多了（最多 {MAX_CATEGORIES} 个，现在 {parsedCats.categories.length} 个）
          </div>
        )}

        <div className="ph-set-label ph-dir-label2">每日上限</div>
        <input
          className="ph-set-input ph-dir-num"
          type="number"
          min={MIN_PER_DAY}
          max={MAX_PER_DAY}
          step={1}
          value={maxText}
          onChange={(e) => setMaxDraft(e.target.value)}
          aria-label="每日上限"
          disabled={configQuery.isPending}
        />
        <div className="ph-dir-hint">
          一天最多给你留多少篇（{MIN_PER_DAY}–{MAX_PER_DAY}）。命中的论文多于这个数时，保留推荐分最高的那些。
        </div>
        {(maxOutOfRange || maxUnparseable) && (
          <div className="ph-dir-warn">
            每日上限需要是 {MIN_PER_DAY} 到 {MAX_PER_DAY} 之间的整数
          </div>
        )}
        {/* Cleared on purpose reads as "don't change this", which is what the
            request will do — say so rather than let a blank box look broken. */}
        {maxBlank && configDirty && (
          <div className="ph-dir-warn">留空表示不改动每日上限</div>
        )}

        <div className="ph-dir-editor-acts">
          <button
            type="button"
            className="ph-set-btn ph-set-btn--on"
            onClick={() => saveConfig.mutate()}
            disabled={!configDirty || saveConfig.isPending}
          >
            {saveConfig.isPending ? "保存中…" : "保存抓取设置"}
          </button>
          {configDirty && (
            <button
              type="button"
              className="ph-set-btn"
              onClick={() => {
                setCatDraft(null);
                setMaxDraft(null);
                saveConfig.reset();
              }}
              disabled={saveConfig.isPending}
            >
              还原
            </button>
          )}
        </div>
        {saveConfig.isError && <div className="ph-set-err">{errText(saveConfig.error)}</div>}
      </div>
    </>
  );
}

/**
 * 每日论文 — the daily arXiv digest module.
 *
 * Layout mirrors the 文库 module's three-pane rhythm so the two modules read as
 * one app: 196px date rail | flexible list | 280px detail panel.
 *
 * The module's guiding rule is that FETCHING and READING are separate stages
 * with separate failure modes. arXiv metadata always arrives; the Chinese
 * summary / highlights / scores only exist once an LLM provider is configured
 * and has actually run. So every render path below distinguishes "not read yet"
 * from "read and empty", and NOTHING here ever invents a summary or a score —
 * a visibly unread paper is correct, a fabricated one is a defect.
 *
 * A second separation now runs alongside it: the SWEEP is shared, the FEED is
 * personal. One arXiv fetch and one LLM reading serve every account, but which
 * papers reach this view, which direction each falls under, how relevant it is
 * and where it ranks are all resolved against the signed-in user's own
 * directions on the way out. Two consequences shape the code below:
 *
 *   - Every direction and every score rendered here is already the caller's.
 *     `matched_domain`, `matched_keywords`, `scores.relevance` and
 *     `score_recommendation` are overridden per request by the API and are NOT
 *     the stored columns of the same name. There is nothing left in this file
 *     that may be filled from a global table — the filter chips come from the
 *     caller's directions, not a constant.
 *   - "No papers" now has several distinct causes, and they need different
 *     fixes from different people. A day nobody has swept is the operator's
 *     problem; a day that swept 80 papers and matched none of yours is yours,
 *     and is solved in settings. Collapsing the two would leave someone staring
 *     at an empty list with no idea their own configuration caused it, so the
 *     empty states below are deliberately separate and each names its own fix.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { DailyHighlights, DailyPaper, DailyScores } from "../api/types";
import { Icons } from "../design/icons";
import { dash } from "../lib/model";
import { useUI, type SettingsTab } from "../store";
import "./DailyView.css";

/** Poll cadence while a sweep is running, matching the translation job poll. */
const POLL_MS = 1500;

/** A run's error and a read error can both be a whole traceback; panels have a line. */
const ERR_MAX = 160;

/**
 * The settings tab that hosts 研究方向, or `undefined` to open settings on its
 * default tab.
 *
 * Every empty state caused by the user's own configuration offers a way to go
 * fix it, and that is the point of them — but which tab owns the directions
 * editor is the settings module's declaration, not this view's. Naming a tab
 * that does not exist in `SettingsTab` would not compile; guessing one that
 * does but is wrong would land the user on the wrong page. So this stays
 * `undefined` (settings opens where it normally opens) until the directions tab
 * lands, at which point this one constant is the only edit needed here.
 */
const DIRECTIONS_TAB: SettingsTab | undefined = undefined;

type SortKey = "score" | "time";

/** The four highlight blocks, in the order the user's card schema defines them. */
const HIGHLIGHT_ROWS: { key: keyof DailyHighlights; label: string }[] = [
  { key: "contribution", label: "贡献" },
  { key: "innovation", label: "创新" },
  { key: "method", label: "方法" },
  { key: "results", label: "结果" },
];

/**
 * 推荐 last: it is the weighted overall call, so it reads as the conclusion.
 *
 * `hint` distinguishes the two kinds of number in this list, because they no
 * longer come from the same place and a reader who assumes they do will
 * misread both. 时效/热度/质量 describe the paper and are the same for
 * everyone who opens it. 相关 is computed from THIS account's keywords, and
 * 推荐 is re-weighted from the other four using it — so both move when the
 * user edits a direction, and neither is a judgement the model made about them.
 */
const SCORE_ROWS: { key: keyof DailyScores; label: string; hint: string }[] = [
  { key: "relevance", label: "相关", hint: "与你的研究方向的匹配度，按你的关键词计算" },
  { key: "recency", label: "时效", hint: "论文本身的时效性" },
  { key: "popularity", label: "热度", hint: "论文本身的关注度" },
  { key: "quality", label: "质量", hint: "论文本身的质量" },
  { key: "recommendation", label: "推荐", hint: "综合评分，含你的相关度，因此因人而异" },
];

const clip = (s: string): string => (s.length > ERR_MAX ? `${s.slice(0, ERR_MAX)}…` : s);

/** One decimal, matching the scale the reading prompt scores on. */
const fmtScore = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : v.toFixed(1);

/**
 * Visual weight class for a recommendation score.
 *
 * The scoring rules are emphatic that scores must DISCRIMINATE ("别全给 7"), and
 * that only pays off if the UI makes the difference visible at a glance. A 9
 * therefore gets a solid accent chip, a 7 a soft one, and a 5 stays quiet —
 * so a column of cards can be triaged by scanning the left edge alone.
 */
function scoreTier(v: number | null | undefined): string {
  if (v === null || v === undefined) return "is-none";
  if (v >= 8.5) return "is-high";
  if (v >= 7) return "is-mid";
  return "is-low";
}

/** Sort keys are nullable (an unread paper has no score); missing sinks to the
 *  bottom in either mode rather than masquerading as zero. */
function compare(a: DailyPaper, b: DailyPaper, sort: SortKey): number {
  if (sort === "score") {
    const x = a.score_recommendation;
    const y = b.score_recommendation;
    if (x === null || y === null) {
      if (x !== y) return x === null ? 1 : -1;
    } else if (x !== y) {
      return y - x;
    }
  }
  // Newest first, both as the 时间 mode and as the 推荐分 tie-break.
  const at = a.published_at ?? "";
  const bt = b.published_at ?? "";
  if (at !== bt) return at < bt ? 1 : -1;
  return a.arxiv_id < b.arxiv_id ? 1 : -1;
}

/** Read-state chip shown wherever a summary would otherwise go. */
function ReadChip({ paper }: { paper: DailyPaper }): JSX.Element | null {
  if (paper.read_status === "done") return null;
  if (paper.read_status === "error")
    return <span className="ph-dv-chip is-err">解读失败</span>;
  return <span className="ph-dv-chip is-pending">待解读</span>;
}

/** The caller's directions, listed so an empty feed can be checked against the
 *  configuration that produced it without leaving the page. */
function DirectionList({ names }: { names: string[] }): JSX.Element | null {
  if (names.length === 0) return null;
  return (
    <div className="ph-dv-dirs">
      <span className="ph-dv-dirs-k">当前方向</span>
      {names.map((n) => (
        <span key={n} className="ph-dv-dirs-v">
          {n}
        </span>
      ))}
    </div>
  );
}

export function DailyView(): JSX.Element {
  const qc = useQueryClient();

  const dailyDate = useUI((s) => s.dailyDate);
  const setDailyDate = useUI((s) => s.setDailyDate);
  const dailyPaperId = useUI((s) => s.dailyPaperId);
  const setDailyPaper = useUI((s) => s.setDailyPaper);
  const openSettings = useUI((s) => s.openSettings);

  const [sort, setSort] = useState<SortKey>("score");
  const [domain, setDomain] = useState<string | null>(null);

  // Declared before the queries because the status poll reads `isPending` to
  // keep polling across the gap between "POST accepted" and "run row says running".
  const refresh = useMutation({
    mutationFn: () => api.daily.refresh(),
    // A sweep changes dates, counts and papers at once; one prefix invalidate
    // covers all three. This is a user-triggered callback, never an effect.
    //
    // The promise is RETURNED, not discarded: react-query keeps the mutation
    // `isPending` until onSuccess settles, which holds the 更新 button disabled
    // across the gap between "202 accepted" and "status says a sweep is live".
    // Dropping it re-enables the button for one render and lets an eager second
    // click fire a request the backend can only answer with 409.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["daily"] }),
  });

  const statusQuery = useQuery({
    queryKey: ["daily", "status"],
    queryFn: api.daily.status,
    // Self-terminating: `sweeping` is the live sweeper's own state, so it goes
    // null the moment the sweep ends and the timer is cleared on the next tick.
    // Deliberately NOT last_run.status — a run row orphaned by a backend restart
    // stays "running" in the database forever and would poll forever with it.
    refetchInterval: (q) =>
      refresh.isPending || q.state.data?.sweeping != null ? POLL_MS : false,
  });

  const run = statusQuery.data?.last_run ?? null;
  const llmConfigured = statusQuery.data?.llm_configured ?? true;
  /** A sweep is in flight — the only condition under which anything polls. */
  const busy = refresh.isPending || statusQuery.data?.sweeping != null;

  /** The CALLER's own enabled directions, in their declared priority order.
   *  The single source for the filter chips and for telling an empty feed
   *  "nothing matched you" apart from "you match nothing". */
  const directions = useMemo(
    () => statusQuery.data?.directions ?? [],
    [statusQuery.data],
  );

  const datesQuery = useQuery({
    queryKey: ["daily", "dates"],
    queryFn: api.daily.dates,
    refetchInterval: busy ? POLL_MS : false,
  });

  const dates = useMemo(() => datesQuery.data ?? [], [datesQuery.data]);
  // No explicit selection yet -> show the newest digest, which is what someone
  // opening this module in the morning wants.
  const activeDate = dailyDate ?? dates[0]?.date ?? null;

  const papersQuery = useQuery({
    queryKey: ["daily", "day", activeDate ?? ""],
    // `enabled` below guarantees activeDate is non-null here, but the narrowing
    // is done with a local rather than a cast so the compiler proves it.
    queryFn: () => api.daily.day(activeDate ?? ""),
    enabled: activeDate !== null,
    refetchInterval: busy ? POLL_MS : false,
  });

  const papers = useMemo(() => papersQuery.data?.papers ?? [], [papersQuery.data]);
  /** The sweep row for the shown date. Non-null means this date WAS swept, which
   *  is what makes an empty list attributable to the filter rather than to the
   *  digest never having run. `/dates` only lists dates the caller matches, so
   *  the same question at the whole-rail level is answered by `last_run`. */
  const dayRun = papersQuery.data?.run ?? null;

  const read = useMutation({
    mutationFn: (id: string) => api.daily.read(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["daily", "day", activeDate ?? ""] });
      void qc.invalidateQueries({ queryKey: ["daily", "dates"] });
    },
  });

  const importPaper = useMutation({
    mutationFn: (id: string) => api.daily.import(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["daily", "day", activeDate ?? ""] });
      // The paper now exists in 文库 too.
      void qc.invalidateQueries({ queryKey: ["papers"] });
    },
  });

  /**
   * Filter chips: the caller's own directions that actually matched today.
   *
   * Ordered by the user's declared priority rather than by the order papers
   * happen to arrive in, so the chip row matches the list they see in settings.
   * Still intersected with what is present, because a chip that filters to zero
   * papers is a dead control — and a direction that matched nothing today is
   * information the empty states carry, not something to click.
   */
  const domains = useMemo(() => {
    const present: string[] = [];
    for (const p of papers) {
      if (p.matched_domain !== null && !present.includes(p.matched_domain)) {
        present.push(p.matched_domain);
      }
    }
    const ranked = directions.filter((d) => present.includes(d));
    // Anything matched but no longer in the direction list — a direction renamed
    // or disabled since this day was rendered. Shown rather than dropped: the
    // papers under it are on screen and need a chip that reaches them.
    for (const d of present) if (!ranked.includes(d)) ranked.push(d);
    return ranked;
  }, [papers, directions]);

  // Derived, not stored: editing directions in settings can retire the chip the
  // user last clicked, and a stale selection would silently filter the whole day
  // away. Falling back to 全部 means the feed re-ranks into view on the next
  // fetch instead of going blank. Also makes "a chip is on but matches nothing"
  // unrepresentable — every member of `domains` has at least one paper.
  const activeDomain = domain !== null && domains.includes(domain) ? domain : null;

  const visible = useMemo(() => {
    const list =
      activeDomain === null ? papers : papers.filter((p) => p.matched_domain === activeDomain);
    return [...list].sort((a, b) => compare(a, b, sort));
  }, [papers, activeDomain, sort]);

  // Selected from the whole day, not from `visible`: narrowing the filter
  // should not blank the detail panel out from under the reader.
  const selected = papers.find((p) => p.id === dailyPaperId) ?? null;

  const datesReady = !datesQuery.isPending && !datesQuery.isError;
  const statusReady = statusQuery.isSuccess;

  /* ----------------------------------------------------- empty-state triage
     Three different causes, three different fixes, resolved once here so the
     JSX below reads as a list of cases rather than a nest of conditions.

     `noDirections` wins over everything: with no enabled directions the backend
     matches nothing by construction, so every other emptiness downstream is a
     consequence of it and saying "nothing matched" would describe the symptom
     while hiding the cause. It is reachable only deliberately — a new account is
     seeded with defaults on first read — so it means "you deleted them all",
     not "you never set them up", and the copy says so. */
  const noDirections = statusReady && directions.length === 0;
  /** The install has swept at least once, so an empty rail is the filter's doing
   *  and not a digest that has never run. */
  const everSwept = statusReady && run !== null;
  const railEmpty = datesReady && dates.length === 0;
  const showFirstUse = railEmpty && !noDirections && !everSwept;
  const showNothingMatched = railEmpty && !noDirections && everSwept;

  const toSettings = (): void => openSettings(DIRECTIONS_TAB);

  return (
    <div className="ph-dv">
      {/* -------------------------------------------------------- date rail */}
      <aside className="ph-dv-rail ph-scroll">
        <div className="ph-dv-rail-head">日期</div>
        {datesQuery.isPending && <div className="ph-dv-rail-note">载入中…</div>}
        {datesQuery.isError && <div className="ph-dv-rail-note is-err">无法连接</div>}
        {/* The rail lists dates that matched YOU, so "empty" here says the same
            thing the centre panel spells out — kept terse to match its width. */}
        {railEmpty && (
          <div className="ph-dv-rail-note">
            {noDirections ? "未配置方向" : everSwept ? "无匹配日期" : "暂无记录"}
          </div>
        )}
        {dates.map((d) => (
          <div
            key={d.date}
            className={d.date === activeDate ? "ph-dv-date is-active" : "ph-dv-date"}
            onClick={() => setDailyDate(d.date)}
          >
            <span className="ph-dv-date-text">{d.date}</span>
            {d.pending > 0 && (
              <span className="ph-dv-date-pending" title={`${d.pending} 篇待解读`}>
                {d.pending}
              </span>
            )}
            <span className="ph-dv-date-count">{d.total}</span>
          </div>
        ))}
      </aside>

      {/* ----------------------------------------------------------- centre */}
      <section className="ph-dv-main">
        <div className="ph-dv-bar">
          <span className="ph-dv-bar-date">{activeDate ?? "每日论文"}</span>
          {domains.length > 0 && (
            <div className="ph-dv-filters">
              <button
                className={activeDomain === null ? "ph-dv-fchip is-on" : "ph-dv-fchip"}
                onClick={() => setDomain(null)}
              >
                全部
              </button>
              {domains.map((d) => (
                <button
                  key={d}
                  className={activeDomain === d ? "ph-dv-fchip is-on" : "ph-dv-fchip"}
                  onClick={() => setDomain(activeDomain === d ? null : d)}
                >
                  {d}
                </button>
              ))}
            </div>
          )}
          <div className="ph-dv-spacer" />
          <div className="ph-dv-sort">
            <button
              className={sort === "score" ? "ph-dv-sbtn is-on" : "ph-dv-sbtn"}
              onClick={() => setSort("score")}
            >
              推荐分
            </button>
            <button
              className={sort === "time" ? "ph-dv-sbtn is-on" : "ph-dv-sbtn"}
              onClick={() => setSort("time")}
            >
              时间
            </button>
          </div>
          <button
            className="ph-dv-refresh"
            disabled={busy}
            onClick={() => refresh.mutate()}
            title="抓取今日 arXiv 并解读"
          >
            <span className={busy ? "ph-dv-refresh-ic is-spin" : "ph-dv-refresh-ic"}>
              <Icons.sync />
            </span>
            {busy ? "更新中" : "更新"}
          </button>
        </div>

        {/* Live counters while a sweep runs — the reason the poll exists. The
            fetched count is the SHARED sweep's, not this reader's: the sweep
            fetches the union of everyone's categories, so it will usually
            exceed the number of cards that end up in this list. */}
        {busy && (
          <div className="ph-dv-note">
            正在更新 · 已抓取 {run?.fetched ?? 0} 篇
            {run ? ` · 已解读 ${run.read_done}` : ""}
            {run && run.read_failed > 0 ? ` · 失败 ${run.read_failed}` : ""}
          </div>
        )}

        {!busy && run?.status === "error" && (
          <div className="ph-dv-note is-err">
            上次更新失败{run.error ? `：${clip(run.error)}` : ""}
          </div>
        )}

        {refresh.isError && (
          <div className="ph-dv-note is-err">
            更新失败：{refresh.error instanceof Error ? refresh.error.message : "未知错误"}
          </div>
        )}

        {/* The honest statement of what this module can and cannot do right now.
            Deliberately not an error: fetching genuinely works without a key.
            Papers keep arriving and stay 待解读 — visibly unread, never blank. */}
        {statusQuery.isSuccess && !llmConfigured && (
          <div className="ph-dv-banner">
            <span className="ph-dv-banner-ic">
              <Icons.alert size={16} />
            </span>
            <span>
              尚未配置 LLM API Key —— 论文可以正常抓取并按你的方向筛选排序，
              但无法生成中文解读与评分。这些论文会保持「待解读」状态，配置后可随时重新解读。
            </span>
          </div>
        )}

        <div className="ph-dv-list ph-scroll">
          {papersQuery.isPending && activeDate !== null && (
            <div className="ph-dv-empty">
              <div className="ph-dv-empty-text">载入中…</div>
            </div>
          )}

          {(papersQuery.isError || (datesQuery.isError && activeDate === null)) && (
            <div className="ph-dv-empty">
              <div className="ph-dv-empty-text is-err">
                无法连接到后端服务。
                <br />
                请确认 Pharos 服务已启动后重试。
              </div>
            </div>
          )}

          {/* ---- cause 1: no directions at all. Nothing can match; say that. */}
          {noDirections && (
            <div className="ph-dv-firstuse">
              <div className="ph-dv-firstuse-inner">
                <div className="ph-dv-firstuse-mark">
                  <Icons.daily size={26} sw={1.2} />
                </div>
                <div className="ph-dv-firstuse-title">尚未配置研究方向</div>
                <div className="ph-dv-firstuse-desc">
                  每日论文按你自己定义的研究方向筛选 arXiv。
                  当前没有任何启用的方向，因此不会有论文进入这里。
                  <br />
                  在设置中添加一个方向并填入关键词即可开始。
                </div>
                <button className="ph-dv-cta" onClick={toSettings}>
                  前往设置
                </button>
              </div>
            </div>
          )}

          {/* ---- cause 2: never swept. The digest itself has not run yet. */}
          {showFirstUse && (
            <div className="ph-dv-firstuse">
              <div className="ph-dv-firstuse-inner">
                <div className="ph-dv-firstuse-mark">
                  <Icons.daily size={26} sw={1.2} />
                </div>
                <div className="ph-dv-firstuse-title">每日论文</div>
                <div className="ph-dv-firstuse-desc">
                  每天自动扫描 arXiv，按你在设置里定义的研究方向筛出相关论文，
                  逐篇生成中文速览、要点与评分。
                  <br />
                  点「更新」抓取第一份日报。
                </div>
                <DirectionList names={directions} />
                <div className="ph-dv-firstuse-btns">
                  <button
                    className="ph-dv-cta"
                    disabled={busy}
                    onClick={() => refresh.mutate()}
                  >
                    {busy ? "更新中…" : "更新"}
                  </button>
                  <button className="ph-dv-cta-ghost" onClick={toSettings}>
                    调整方向
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ---- cause 3: swept, but nothing matched THIS reader. The fix is
                  the user's, so the settings action leads and 更新 follows. */}
          {showNothingMatched && (
            <div className="ph-dv-firstuse">
              <div className="ph-dv-firstuse-inner">
                <div className="ph-dv-firstuse-mark">
                  <Icons.daily size={26} sw={1.2} />
                </div>
                <div className="ph-dv-firstuse-title">没有论文匹配你的方向</div>
                <div className="ph-dv-firstuse-desc">
                  抓取已经运行过，但目前没有任何一篇论文命中你的方向关键词。
                  <br />
                  可以在设置中放宽关键词、增加方向或分类，改动会立即重新筛选，无需重新抓取。
                </div>
                <DirectionList names={directions} />
                <div className="ph-dv-firstuse-btns">
                  <button className="ph-dv-cta" onClick={toSettings}>
                    调整方向
                  </button>
                  <button
                    className="ph-dv-cta-ghost"
                    disabled={busy}
                    onClick={() => refresh.mutate()}
                  >
                    {busy ? "更新中…" : "重新抓取"}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ---- the same triage for one date the user navigated to. Reachable
                  when a date is selected and then the directions change under
                  it, since `/dates` would no longer list it. */}
          {papersQuery.isSuccess && papers.length === 0 && !noDirections && (
            <div className="ph-dv-empty">
              {dayRun === null ? (
                <div className="ph-dv-empty-text">
                  该日尚未抓取
                  <br />
                  <span className="ph-dv-empty-hint">周末与公告间隔期通常没有更新</span>
                </div>
              ) : (
                <div className="ph-dv-empty-text">
                  该日无匹配你方向的论文
                  <br />
                  <span className="ph-dv-empty-hint">
                    当日共抓取 {dayRun.fetched} 篇，均未命中你的关键词
                  </span>
                  <br />
                  <button className="ph-dv-empty-link" onClick={toSettings}>
                    调整方向
                  </button>
                </div>
              )}
            </div>
          )}

          {/* No "this chip is empty" state: `activeDomain` is derived from
              `domains`, every member of which has at least one paper today. */}

          {visible.map((p) => {
            const summary = p.summary_zh?.trim() ?? "";
            return (
              <article
                key={p.id}
                className={p.id === dailyPaperId ? "ph-dv-card is-selected" : "ph-dv-card"}
                onClick={() => setDailyPaper(p.id)}
              >
                <div className="ph-dv-card-top">
                  <span
                    className={`ph-dv-score ${scoreTier(p.score_recommendation)}`}
                    title="推荐分 · 含你的方向相关度"
                  >
                    {fmtScore(p.score_recommendation)}
                  </span>
                  {p.matched_domain && <span className="ph-dv-domain">{p.matched_domain}</span>}
                  <span className="ph-dv-card-title">{p.title}</span>
                </div>
                {/* Nothing at all when a paper is read but produced no summary:
                    an empty bordered row would read as "no data" rather than as
                    "there is simply nothing more to say about this one". */}
                {p.read_status === "done" ? (
                  summary !== "" && <div className="ph-dv-card-sum">{summary}</div>
                ) : (
                  <div className="ph-dv-card-sum">
                    <ReadChip paper={p} />
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </section>

      {/* ------------------------------------------------------------ detail */}
      <aside className="ph-dv-detail ph-scroll">
        {selected === null ? (
          <div className="ph-dv-detail-empty">
            选择一篇论文
            <br />
            查看解读
          </div>
        ) : (
          <PaperDetail
            paper={selected}
            onRead={() => read.mutate(selected.id)}
            reading={read.isPending}
            canRead={llmConfigured}
            readError={read.error instanceof Error ? read.error.message : null}
            onImport={() => importPaper.mutate(selected.id)}
            importing={importPaper.isPending}
            importError={importPaper.error instanceof Error ? importPaper.error.message : null}
          />
        )}
      </aside>
    </div>
  );
}

interface DetailProps {
  paper: DailyPaper;
  onRead: () => void;
  reading: boolean;
  /** False when no LLM provider is configured: reading would 503, so the button
   *  says so up front instead of letting the click fail silently. */
  canRead: boolean;
  /** The message from a failed 解读 request — a 503 "not configured", a network
   *  error. Without this the button would just spring back with no explanation. */
  readError: string | null;
  onImport: () => void;
  importing: boolean;
  importError: string | null;
}

/** The 280px right pane. Split out so the selected-paper branch stays flat. */
function PaperDetail({
  paper,
  onRead,
  reading,
  canRead,
  readError,
  onImport,
  importing,
  importError,
}: DetailProps): JSX.Element {
  const imported = paper.imported_paper_id !== null;
  const summary = paper.summary_zh?.trim() ?? "";
  const highlights = paper.highlights;
  const scores = paper.scores;
  const done = paper.read_status === "done";

  return (
    <div className="ph-dv-detail-body">
      <div className="ph-dv-d-title">{paper.title}</div>
      <div className="ph-dv-d-sub">
        {paper.arxiv_id}
        {paper.venue ? ` · ${paper.venue}` : ""}
      </div>

      <div className="ph-dv-d-actions">
        <button
          type="button"
          className="ph-dv-d-primary"
          disabled={imported || importing}
          onClick={onImport}
        >
          <span className="ph-dv-d-ic">
            <Icons.plus />
          </span>
          {imported ? "已在文库" : importing ? "导入中…" : "导入文库"}
        </button>
      </div>

      {importError !== null && <div className="ph-dv-d-err">导入失败：{clip(importError)}</div>}

      {/* Read state is stated before any content, so an empty section is never
          ambiguous between "not read" and "read, nothing to say". */}
      {paper.read_status === "pending" && (
        <div className="ph-dv-d-state is-col">
          <div className="ph-dv-d-state-row">
            <span className="ph-dv-chip is-pending">待解读</span>
            <button
              type="button"
              className="ph-dv-d-ghost"
              disabled={reading || !canRead}
              title={canRead ? undefined : "尚未配置 LLM API Key"}
              onClick={onRead}
            >
              {reading ? "解读中…" : "解读"}
            </button>
          </div>
          {readError !== null && <div className="ph-dv-d-err">解读失败：{clip(readError)}</div>}
        </div>
      )}

      {paper.read_status === "error" && (
        <div className="ph-dv-d-state is-col">
          <div className="ph-dv-d-state-row">
            <span className="ph-dv-chip is-err">解读失败</span>
            <button
              type="button"
              className="ph-dv-d-ghost"
              disabled={reading || !canRead}
              title={canRead ? undefined : "尚未配置 LLM API Key"}
              onClick={onRead}
            >
              {reading ? "重试中…" : "重试"}
            </button>
          </div>
          {/* The stored failure, then the failure of the retry itself — they are
              different events and collapsing them would hide the newer one. */}
          {paper.read_error && <div className="ph-dv-d-err">{clip(paper.read_error)}</div>}
          {readError !== null && <div className="ph-dv-d-err">重试失败：{clip(readError)}</div>}
        </div>
      )}

      {done && summary !== "" && (
        <div className="ph-dv-d-sec">
          <div className="ph-dv-d-label">中文速览</div>
          <div className="ph-dv-d-summary">{summary}</div>
        </div>
      )}

      {done && highlights && (
        <div className="ph-dv-d-sec">
          <div className="ph-dv-d-label">要点</div>
          {HIGHLIGHT_ROWS.map(({ key, label }) => {
            const text = highlights[key]?.trim() ?? "";
            if (text === "") return null;
            return (
              <div key={key} className="ph-dv-hl">
                <div className="ph-dv-hl-k">{label}</div>
                <div className="ph-dv-hl-v">{text}</div>
              </div>
            );
          })}
        </div>
      )}

      {done && scores && (
        <div className="ph-dv-d-sec">
          <div className="ph-dv-d-label">评分</div>
          {SCORE_ROWS.map(({ key, label, hint }) => {
            const v = scores[key];
            // 0–10 scale; the bar makes the spread between dimensions legible.
            const pct = typeof v === "number" ? Math.max(0, Math.min(100, v * 10)) : 0;
            const isRec = key === "recommendation";
            return (
              <div key={key} className={isRec ? "ph-dv-sc is-rec" : "ph-dv-sc"} title={hint}>
                <span className="ph-dv-sc-k">{label}</span>
                <span className="ph-dv-sc-track">
                  <span className="ph-dv-sc-bar" style={{ width: `${pct}%` }} />
                </span>
                <span className="ph-dv-sc-v">{fmtScore(v)}</span>
              </div>
            );
          })}
          {/* Said once, in the one place both numbers are on screen together. */}
          <div className="ph-dv-sc-note">相关与推荐按你的研究方向计算</div>
        </div>
      )}

      <div className="ph-dv-d-sec">
        <div className="ph-dv-d-label">信息</div>
        <div className="ph-dv-d-grid">
          <span className="ph-dv-d-k">作者</span>
          <span className="ph-dv-d-v">{dash(paper.authors)}</span>
          {/* 方向 and 命中 are the caller's own direction and the caller's own
              keywords that fired — resolved per request, not the shared row. */}
          <span className="ph-dv-d-k" title="命中的是你的哪个研究方向">
            方向
          </span>
          <span className="ph-dv-d-v">{dash(paper.matched_domain)}</span>
          <span className="ph-dv-d-k">分类</span>
          <span className="ph-dv-d-v">{dash(paper.categories)}</span>
          <span className="ph-dv-d-k" title="你的哪些关键词命中了这篇论文">
            命中
          </span>
          <span className="ph-dv-d-v">{dash(paper.matched_keywords)}</span>
        </div>
      </div>

      {paper.abstract && (
        <div className="ph-dv-d-sec">
          <div className="ph-dv-d-label">英文摘要</div>
          <div className="ph-dv-d-abstract">{paper.abstract}</div>
        </div>
      )}

      <div className="ph-dv-d-sec-last">
        <div className="ph-dv-d-links">
          {paper.arxiv_url && (
            <a className="ph-dv-d-link" href={paper.arxiv_url} target="_blank" rel="noreferrer">
              <span className="ph-dv-d-ic">
                <Icons.link />
              </span>
              arXiv 摘要页
            </a>
          )}
          {paper.pdf_url && (
            <a className="ph-dv-d-link" href={paper.pdf_url} target="_blank" rel="noreferrer">
              <span className="ph-dv-d-ic">
                <Icons.file />
              </span>
              PDF
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

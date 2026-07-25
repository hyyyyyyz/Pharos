import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import type {
  DiscoverySource,
  LiteratureResult,
  LiteratureSearch,
  LiteratureSearchBody,
  ResearchProject,
  ResearchProjectCreateBody,
} from "../api/types";
import { Icons } from "../design/icons";
import { useUI } from "../store";
import "./DiscoveryView.css";

const SOURCE_OPTIONS: { id: DiscoverySource; name: string; note: string }[] = [
  { id: "arxiv", name: "arXiv", note: "预印本与最新工作" },
  { id: "openalex", name: "OpenAlex", note: "跨出版源与引用信息" },
];

const STATUS_TEXT: Record<LiteratureSearch["status"], string> = {
  running: "检索中",
  complete: "已完成",
  partial: "部分完成",
  error: "失败",
};

const cx = (...parts: (string | false | null | undefined)[]): string =>
  parts.filter(Boolean).join(" ");

function errText(error: unknown): string {
  return error instanceof Error && error.message !== "" ? error.message : "请求失败，请稍后重试";
}

function fmtTime(value: string | null): string {
  if (value === null) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function paperMeta(result: LiteratureResult): string {
  const bits = [result.year?.toString(), result.venue, result.citation_count === null ? null : `被引 ${result.citation_count}`];
  return bits.filter((v): v is string => v !== null && v !== undefined && v !== "").join(" · ");
}

interface ArchiveAction {
  resultIds: string[];
  existingProject: ResearchProject | null;
  newProject: ResearchProjectCreateBody | null;
}

interface ArchiveOutcome {
  project: ResearchProject;
  added: number;
  skipped: number;
  failed: number;
  failedIds: string[];
}

function analysisErrText(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "尚未配置 Chat Provider；规则提取结果已保留。配置服务端模型后可再次深读。";
  }
  if (error instanceof ApiError && error.status === 503) {
    return "AI 深读服务暂时不可用；规则提取结果未被覆盖，可稍后重试。";
  }
  return errText(error);
}

function SearchHistoryItem({
  search,
  active,
  onOpen,
}: {
  search: LiteratureSearch;
  active: boolean;
  onOpen: () => void;
}): JSX.Element {
  return (
    <button
      type="button"
      className={cx("ph-disc-history-item", active && "is-active")}
      onClick={onOpen}
    >
      <span className="ph-disc-history-top">
        <span className={cx("ph-disc-status-dot", `is-${search.status}`)} />
        <span className="ph-disc-history-query">{search.query}</span>
      </span>
      <span className="ph-disc-history-meta">
        {fmtTime(search.created_at)} · {search.result_count} 篇
      </span>
      <span className="ph-disc-history-sources">{search.sources.join(" + ") || "未记录来源"}</span>
    </button>
  );
}

function ResultCard({
  result,
  selected,
  filed,
  analyzing,
  analysisError,
  onToggle,
  onAnalyze,
}: {
  result: LiteratureResult;
  selected: boolean;
  filed: boolean;
  analyzing: boolean;
  analysisError: string | null;
  onToggle: () => void;
  onAnalyze: () => void;
}): JSX.Element {
  const analysisLabel = result.analysis_mode === "llm" ? "AI 深读" : "仅基于摘要提取";
  const warningText =
    result.analysis_warning === null
      ? null
      : result.analysis_mode === "rules"
        ? "当前仅基于题名和摘要做规则提取；配置 Chat Provider 后可运行 AI 深读。"
        : result.analysis_warning;
  return (
    <article className={cx("ph-disc-result", selected && "is-selected")}>
      <div className="ph-disc-result-select">
        <label className="ph-disc-check" title={filed ? "仍可加入其他项目" : "选择论文"}>
          <input type="checkbox" checked={selected} onChange={onToggle} />
          <span />
        </label>
      </div>

      <div className="ph-disc-result-body">
        <div className="ph-disc-result-eyebrow">
          <span className="ph-disc-rank">#{result.rank}</span>
          {result.sources.map((source) => (
            <span key={source} className="ph-disc-source-chip">
              {source}
            </span>
          ))}
          <span className={cx("ph-disc-analysis-chip", result.analysis_mode === "llm" && "is-ai")}>
            {result.analysis_model === null ? analysisLabel : `${analysisLabel} · ${result.analysis_model}`}
          </span>
          {filed && <span className="ph-disc-filed-chip">已在当前项目</span>}
          <button
            type="button"
            className="ph-disc-analyze-btn"
            onClick={onAnalyze}
            disabled={analyzing}
          >
            <Icons.spark size={13} />
            {analyzing ? "深读中…" : result.analysis_mode === "llm" ? "重新深读" : "AI 深读"}
          </button>
        </div>

        <div className="ph-disc-result-title-row">
          {result.url !== null ? (
            <a href={result.url} target="_blank" rel="noreferrer" className="ph-disc-result-title">
              {result.title}
            </a>
          ) : (
            <h3 className="ph-disc-result-title">{result.title}</h3>
          )}
          {result.pdf_url !== null && (
            <a className="ph-disc-pdf-link" href={result.pdf_url} target="_blank" rel="noreferrer">
              PDF
            </a>
          )}
        </div>

        <div className="ph-disc-paper-meta">
          <span>{result.authors.length > 0 ? result.authors.slice(0, 4).join("、") : "作者未知"}</span>
          {paperMeta(result) !== "" && <span>{paperMeta(result)}</span>}
        </div>

        {result.summary_zh !== "" && <p className="ph-disc-summary-zh">{result.summary_zh}</p>}

        {result.abstract !== "" && <p className="ph-disc-abstract">{result.abstract}</p>}

        {warningText !== null && (
          <div className="ph-disc-analysis-warning">
            <Icons.alert size={15} />
            <span title={result.analysis_warning ?? undefined}>{warningText}</span>
          </div>
        )}

        {analysisError !== null && (
          <div className="ph-disc-analysis-warning is-error">
            <Icons.alert size={15} />
            <span>{analysisError}</span>
          </div>
        )}

        <div className="ph-disc-insight-grid">
          <section className="ph-disc-insight is-trick">
            <span>核心 Trick</span>
            <p>{result.core_trick || "尚未提取"}</p>
          </section>
          <section className="ph-disc-insight">
            <span>主要贡献</span>
            <p>{result.contribution || "尚未提取"}</p>
          </section>
          <section className="ph-disc-insight">
            <span>方法特点</span>
            <p>{result.method || "尚未提取"}</p>
          </section>
          <section className="ph-disc-insight">
            <span>关键结果</span>
            <p>{result.results || "尚未提取"}</p>
          </section>
          <section className="ph-disc-insight is-limit">
            <span>局限</span>
            <p>{result.limitations || "尚未提取"}</p>
          </section>
        </div>

        {(result.doi !== null || Object.keys(result.source_ids).length > 0) && (
          <details className="ph-disc-identifiers">
            <summary>来源标识</summary>
            {result.doi !== null && <span>DOI · {result.doi}</span>}
            {Object.entries(result.source_ids).map(([source, id]) => (
              <span key={source}>{source} · {id}</span>
            ))}
          </details>
        )}
      </div>
    </article>
  );
}

export function DiscoveryView(): JSX.Element {
  const qc = useQueryClient();
  const activeProjectId = useUI((s) => s.activeProjectId);
  const setActiveProject = useUI((s) => s.setActiveProject);

  const [query, setQuery] = useState("");
  const [sources, setSources] = useState<DiscoverySource[]>(["arxiv", "openalex"]);
  const [limit, setLimit] = useState(12);
  const [activeSearchId, setActiveSearchId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [newProjectQuestion, setNewProjectQuestion] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [analysisError, setAnalysisError] = useState<{ id: string; message: string } | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["research-projects"],
    queryFn: api.projects.list,
  });
  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);
  const activeProject = projects.find((project) => project.id === activeProjectId) ?? null;

  const searchesQuery = useQuery({
    queryKey: ["discovery-searches"],
    queryFn: api.discovery.listSearches,
    refetchInterval: (state) =>
      state.state.data?.some((search) => search.status === "running") === true ? 2000 : false,
  });
  const searches = useMemo(() => searchesQuery.data ?? [], [searchesQuery.data]);

  useEffect(() => {
    if (activeSearchId === null && searches.length > 0) setActiveSearchId(searches[0].id);
    if (activeSearchId !== null && searches.length > 0 && !searches.some((s) => s.id === activeSearchId)) {
      setActiveSearchId(searches[0].id);
    }
  }, [activeSearchId, searches]);

  useEffect(() => {
    setSelectedIds([]);
    setNotice(null);
    setNewProjectOpen(false);
  }, [activeSearchId]);

  useEffect(() => {
    if (
      projectsQuery.isSuccess &&
      activeProjectId !== null &&
      !projects.some((project) => project.id === activeProjectId)
    ) {
      setActiveProject(null);
    }
  }, [activeProjectId, projects, projectsQuery.isSuccess, setActiveProject]);

  const searchDetailQuery = useQuery({
    queryKey: ["discovery-search", activeSearchId ?? ""],
    queryFn: () => api.discovery.getSearch(activeSearchId ?? ""),
    enabled: activeSearchId !== null,
    refetchInterval: (state) => state.state.data?.status === "running" ? 2000 : false,
  });

  const activeSearch =
    searchDetailQuery.data ?? searches.find((search) => search.id === activeSearchId) ?? null;
  const results = activeSearch?.results ?? [];
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const activeProjectResultIds = useMemo(
    () => new Set(activeProject?.sources.map((source) => source.result_id) ?? []),
    [activeProject],
  );

  const searchMutation = useMutation({
    mutationFn: (data: LiteratureSearchBody) => api.discovery.search(data),
    onSuccess: async (created) => {
      qc.setQueryData<LiteratureSearch[]>(["discovery-searches"], (current) => [
        created,
        ...(current?.filter((item) => item.id !== created.id) ?? []),
      ]);
      setActiveSearchId(created.id);
      setSelectedIds([]);
      setNotice(
        created.status === "error"
          ? null
          : created.status === "partial"
            ? `部分来源完成，已保留 ${created.result_count} 篇可用结果`
            : `检索完成，找到 ${created.result_count} 篇候选文献`,
      );
      await qc.invalidateQueries({ queryKey: ["discovery-searches"] });
      qc.setQueryData(["discovery-search", created.id], created);
    },
  });

  const archiveMutation = useMutation({
    mutationFn: async (action: ArchiveAction): Promise<ArchiveOutcome> => {
      const project =
        action.newProject === null
          ? action.existingProject
          : await api.projects.create(action.newProject);
      if (project === null) throw new Error("请先选择一个项目");

      const existing = new Set(project.sources.map((source) => source.result_id));
      const toAdd = action.resultIds.filter((id) => !existing.has(id));
      const saved = await Promise.allSettled(
        toAdd.map((resultId) => api.projects.addSource(project.id, { result_id: resultId })),
      );
      const added = saved.filter((item) => item.status === "fulfilled").length;
      return {
        project,
        added,
        skipped: action.resultIds.length - toAdd.length,
        failed: saved.length - added,
        failedIds: saved.flatMap((item, index) => item.status === "rejected" ? [toAdd[index]] : []),
      };
    },
    onSuccess: async ({ project, added, skipped, failed, failedIds }) => {
      qc.setQueryData<ResearchProject[]>(["research-projects"], (current) =>
        current?.some((item) => item.id === project.id) === true
          ? current
          : [project, ...(current ?? [])],
      );
      setActiveProject(project.id);
      setNewProjectOpen(false);
      setNewProjectName("");
      setNewProjectQuestion("");
      setSelectedIds(failedIds);
      setNotice(
        `已处理「${project.name}」：新增 ${added} 篇${skipped > 0 ? `，${skipped} 篇已存在` : ""}${failed > 0 ? `，${failed} 篇加入失败` : ""}`,
      );
      await qc.invalidateQueries({ queryKey: ["research-projects"] });
      await qc.invalidateQueries({ queryKey: ["research-project", project.id] });
    },
  });

  const analyzeMutation = useMutation({
    mutationFn: (resultId: string) => api.discovery.analyzeResult(resultId),
    onMutate: (resultId) => {
      setAnalysisError((current) => current?.id === resultId ? null : current);
    },
    onSuccess: (updated) => {
      qc.setQueryData<LiteratureSearch>(["discovery-search", updated.search_id], (current) =>
        current === undefined
          ? current
          : { ...current, results: current.results.map((item) => item.id === updated.id ? updated : item) },
      );
      qc.setQueryData<LiteratureSearch[]>(["discovery-searches"], (current) =>
        current?.map((search) =>
          search.id === updated.search_id
            ? { ...search, results: search.results.map((item) => item.id === updated.id ? updated : item) }
            : search,
        ),
      );
      void qc.invalidateQueries({ queryKey: ["research-projects"] });
      void qc.invalidateQueries({ queryKey: ["research-project"] });
      setAnalysisError(null);
      setNotice(`「${updated.title}」已完成 AI 深读`);
    },
    onError: (error, resultId) => {
      setAnalysisError({ id: resultId, message: analysisErrText(error) });
    },
  });

  const toggleSource = (source: DiscoverySource): void => {
    setSources((current) =>
      current.includes(source) ? current.filter((item) => item !== source) : [...current, source],
    );
  };

  const submitSearch = (event: FormEvent): void => {
    event.preventDefault();
    const clean = query.trim();
    if (clean.length < 2 || sources.length === 0) return;
    setNotice(null);
    searchMutation.mutate({
      query: clean,
      sources,
      limit,
      ...(activeProjectId === null ? {} : { project_id: activeProjectId }),
    });
  };

  const toggleResult = (id: string): void => {
    setSelectedIds((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  };

  const archiveExisting = (): void => {
    if (selectedIds.length === 0 || activeProject === null) return;
    archiveMutation.mutate({ resultIds: selectedIds, existingProject: activeProject, newProject: null });
  };

  const archiveNew = (event: FormEvent): void => {
    event.preventDefault();
    const name = newProjectName.trim();
    if (selectedIds.length === 0 || name === "") return;
    archiveMutation.mutate({
      resultIds: selectedIds,
      existingProject: null,
      newProject: {
        name,
        ...(newProjectQuestion.trim() === "" ? {} : { research_question: newProjectQuestion.trim() }),
      },
    });
  };

  const reuseSearch = (): void => {
    if (activeSearch === null) return;
    setQuery(activeSearch.query);
    const reusable = activeSearch.sources.filter(
      (source): source is DiscoverySource => source === "arxiv" || source === "openalex",
    );
    if (reusable.length > 0) setSources(reusable);
    if (activeSearch.project_id !== null) setActiveProject(activeSearch.project_id);
    setNotice("已将历史条件放回检索框，可调整后重新运行");
  };

  const allSelected = results.length > 0 && results.every((result) => selectedSet.has(result.id));

  return (
    <div className="ph-disc">
      <aside className="ph-disc-history ph-scroll">
        <div className="ph-disc-history-head">
          <span>探索记录</span>
          <span>{searches.length}</span>
        </div>

        {searchesQuery.isPending ? (
          <div className="ph-disc-side-state">正在读取历史…</div>
        ) : searchesQuery.isError ? (
          <div className="ph-disc-side-state is-error">
            <span>{errText(searchesQuery.error)}</span>
            <button type="button" onClick={() => void searchesQuery.refetch()}>重试</button>
          </div>
        ) : searches.length === 0 ? (
          <div className="ph-disc-side-state">
            <Icons.search size={20} />
            <span>完成一次检索后，可随时重开结果。</span>
          </div>
        ) : (
          <div className="ph-disc-history-list">
            {searches.map((search) => (
              <SearchHistoryItem
                key={search.id}
                search={search}
                active={search.id === activeSearchId}
                onOpen={() => setActiveSearchId(search.id)}
              />
            ))}
          </div>
        )}
      </aside>

      <main className="ph-disc-main ph-scroll">
        <header className="ph-disc-titlebar">
          <div>
            <h1>文献探索</h1>
            <p>从研究问题出发，保留每次检索的来源、失败和分析边界。</p>
          </div>
          {activeProject !== null && (
            <div className="ph-disc-context">
              <span>当前项目</span>
              <strong>{activeProject.name}</strong>
            </div>
          )}
        </header>

        <form className="ph-disc-composer" onSubmit={submitSearch}>
          <label className="ph-disc-query-field">
            <span>研究问题或 Idea</span>
            <textarea
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="e.g. KV cache compression for long-context video generation"
              maxLength={500}
              rows={3}
            />
          </label>

          <div className="ph-disc-composer-foot">
            <fieldset className="ph-disc-source-picker">
              <legend>检索来源</legend>
              {SOURCE_OPTIONS.map((source) => (
                <label key={source.id} className={cx(sources.includes(source.id) && "is-on")}>
                  <input
                    type="checkbox"
                    checked={sources.includes(source.id)}
                    onChange={() => toggleSource(source.id)}
                  />
                  <span>
                    <strong>{source.name}</strong>
                    <small>{source.note}</small>
                  </span>
                </label>
              ))}
            </fieldset>

            <label className="ph-disc-compact-field">
              <span>关联项目</span>
              <select
                value={activeProjectId ?? ""}
                onChange={(event) => setActiveProject(event.target.value === "" ? null : event.target.value)}
              >
                <option value="">暂不关联</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}{project.status === "archived" ? "（已归档）" : ""}
                  </option>
                ))}
              </select>
            </label>

            <label className="ph-disc-compact-field is-limit">
              <span>数量</span>
              <input
                type="number"
                min={1}
                max={50}
                value={limit}
                onChange={(event) => setLimit(Math.min(50, Math.max(1, Number(event.target.value) || 1)))}
              />
            </label>

            <button
              type="submit"
              className="ph-disc-search-btn"
              disabled={query.trim().length < 2 || sources.length === 0 || searchMutation.isPending}
            >
              <Icons.search size={15} />
              {searchMutation.isPending ? "正在检索…" : "运行检索"}
            </button>
          </div>

          {sources.length === 0 && <p className="ph-disc-form-hint is-error">至少选择一个检索来源。</p>}
          {query.trim() !== "" && query.trim().length < 2 && <p className="ph-disc-form-hint is-error">检索词至少需要 2 个字符。</p>}
          <p className="ph-disc-form-hint">arXiv 建议使用英文关键词；中文会原样发送，当前不会自动翻译。</p>
          {projectsQuery.isError && <p className="ph-disc-form-hint is-error">项目列表加载失败；仍可检索，但暂时不能归档结果。</p>}
          {searchMutation.isError && <p className="ph-disc-form-hint is-error">{errText(searchMutation.error)}</p>}
        </form>

        {notice !== null && <div className="ph-disc-notice"><Icons.check size={14} />{notice}</div>}

        {activeSearch === null ? (
          <section className="ph-disc-empty">
            <span className="ph-disc-empty-mark"><Icons.spark size={24} /></span>
            <h2>从一个可讨论的问题开始</h2>
            <p>结果会保留原始来源、结构化摘要和分析方式；没有模型配置时会明确回退为摘要规则提取。</p>
          </section>
        ) : (
          <section className="ph-disc-results-section">
            <div className="ph-disc-results-head">
              <div>
                <span className={cx("ph-disc-status", `is-${activeSearch.status}`)}>
                  {STATUS_TEXT[activeSearch.status]}
                </span>
                <h2>{activeSearch.query}</h2>
                <p>
                  {activeSearch.result_count} 篇 · {activeSearch.sources.join(" + ") || "来源未记录"} · {fmtTime(activeSearch.completed_at ?? activeSearch.created_at)}
                </p>
              </div>
              <button type="button" className="ph-disc-quiet-btn" onClick={reuseSearch}>复用条件</button>
            </div>

            {Object.keys(activeSearch.errors).length > 0 && (
              <div className="ph-disc-source-errors">
                <strong>{activeSearch.status === "partial" ? "部分来源未返回" : "来源错误"}</strong>
                {Object.entries(activeSearch.errors).map(([source, message]) => (
                  <p key={source}><span>{source}</span>{message}</p>
                ))}
              </div>
            )}

            {activeSearch.status === "running" && (
              <div className="ph-disc-running">
                <span className="ph-disc-running-beam" />
                正在合并来源并提取论文结构，页面会自动更新。
              </div>
            )}

            {results.length > 0 && (
              <div className="ph-disc-selection-bar">
                <label className="ph-disc-select-all">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={() => setSelectedIds(allSelected ? [] : results.map((result) => result.id))}
                  />
                  选择全部
                </label>
                <span>{selectedIds.length} 篇已选择</span>
                <div className="ph-disc-selection-spacer" />
                <select
                  value={activeProjectId ?? ""}
                  onChange={(event) => setActiveProject(event.target.value === "" ? null : event.target.value)}
                  aria-label="归档到项目"
                >
                  <option value="">选择项目</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}{project.status === "archived" ? "（已归档）" : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="ph-disc-archive-btn"
                  disabled={selectedIds.length === 0 || activeProject === null || archiveMutation.isPending}
                  onClick={archiveExisting}
                >
                  加入项目
                </button>
                <button
                  type="button"
                  className="ph-disc-new-project-btn"
                  disabled={selectedIds.length === 0}
                  onClick={() => setNewProjectOpen((open) => !open)}
                >
                  新建项目
                </button>
              </div>
            )}

            {newProjectOpen && (
              <form className="ph-disc-new-project" onSubmit={archiveNew}>
                <div>
                  <strong>新建项目并归档所选文献</strong>
                  <span>项目建立后会成为两个模块共享的当前项目。</span>
                </div>
                <input
                  value={newProjectName}
                  onChange={(event) => setNewProjectName(event.target.value)}
                  placeholder="项目名称"
                  autoFocus
                />
                <input
                  value={newProjectQuestion}
                  onChange={(event) => setNewProjectQuestion(event.target.value)}
                  placeholder="研究问题（可选）"
                />
                <button
                  type="submit"
                  disabled={selectedIds.length === 0 || newProjectName.trim() === "" || archiveMutation.isPending}
                >
                  {archiveMutation.isPending ? "正在创建…" : `创建并加入 ${selectedIds.length} 篇`}
                </button>
              </form>
            )}

            {archiveMutation.isError && (
              <div className="ph-disc-inline-error">{errText(archiveMutation.error)}</div>
            )}

            {searchDetailQuery.isPending && results.length === 0 ? (
              <div className="ph-disc-loading">正在打开检索结果…</div>
            ) : searchDetailQuery.isError ? (
              <div className="ph-disc-loading is-error">
                <span>{errText(searchDetailQuery.error)}</span>
                <button type="button" onClick={() => void searchDetailQuery.refetch()}>重试</button>
              </div>
            ) : results.length === 0 && activeSearch.status !== "running" ? (
              <div className="ph-disc-empty is-small">
                <h2>没有找到可用结果</h2>
                <p>查看上方来源错误，或复用条件后放宽关键词与来源。</p>
              </div>
            ) : (
              <div className="ph-disc-results">
                {results.map((result) => (
                  <ResultCard
                    key={result.id}
                    result={result}
                    selected={selectedSet.has(result.id)}
                    filed={activeProjectResultIds.has(result.id)}
                    analyzing={analyzeMutation.isPending && analyzeMutation.variables === result.id}
                    analysisError={analysisError?.id === result.id ? analysisError.message : null}
                    onToggle={() => toggleResult(result.id)}
                    onAnalyze={() => analyzeMutation.mutate(result.id)}
                  />
                ))}
              </div>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

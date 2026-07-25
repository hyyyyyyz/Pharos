import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type {
  ProjectArtifact,
  ProjectArtifactCreateBody,
  ProjectArtifactPatchBody,
  ProjectArtifactStatus,
  ProjectArtifactType,
  ProjectSource,
  ResearchProject,
  ResearchProjectCreateBody,
  ResearchProjectPatchBody,
  ResearchStage,
} from "../api/types";
import { Icons } from "../design/icons";
import { useUI } from "../store";
import "./ProjectsView.css";

interface StageDef {
  id: ResearchStage;
  label: string;
  short: string;
  note: string;
}

const STAGES: StageDef[] = [
  { id: "discovery", label: "文献探索", short: "探索", note: "建立问题边界和证据池" },
  { id: "ideation", label: "Idea 构思", short: "构思", note: "形成候选假设与机制" },
  { id: "planning", label: "实验规划", short: "规划", note: "冻结指标、基线和停止条件" },
  { id: "experimentation", label: "实验执行", short: "实验", note: "记录真实运行与产物" },
  { id: "analysis", label: "结果分析", short: "分析", note: "解释结果和替代原因" },
  { id: "claims", label: "主张整理", short: "主张", note: "把结果约束成可追溯主张" },
  { id: "drafting", label: "论文草稿", short: "草稿", note: "组织叙事、引用和图表" },
  { id: "review", label: "反方审阅", short: "审阅", note: "找出证据缺口与过度声称" },
  { id: "complete", label: "项目完成", short: "完成", note: "冻结当前研究版本" },
];

const TYPE_LABEL: Record<ProjectArtifactType, string> = {
  hypothesis: "研究假设",
  experiment_plan: "实验计划",
  result: "实验结果",
  claim: "论文主张",
  draft: "写作草稿",
  review: "审阅记录",
};

const STATUS_LABEL: Record<ProjectArtifactStatus, string> = {
  draft: "草稿",
  ready: "可使用",
  verified: "人工核验",
  rejected: "已否决",
};

const DEFAULT_TYPE: Record<ResearchStage, ProjectArtifactType> = {
  discovery: "hypothesis",
  ideation: "hypothesis",
  planning: "experiment_plan",
  experimentation: "result",
  analysis: "result",
  claims: "claim",
  drafting: "draft",
  review: "review",
  complete: "claim",
};

const cx = (...parts: (string | false | null | undefined)[]): string =>
  parts.filter(Boolean).join(" ");

function errText(error: unknown): string {
  return error instanceof Error && error.message !== "" ? error.message : "请求失败，请稍后重试";
}

function fmtDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function stageDef(stage: ResearchStage): StageDef {
  return STAGES.find((item) => item.id === stage) ?? STAGES[0];
}

interface ProjectDraft {
  name: string;
  description: string;
  research_question: string;
}

interface ArtifactDraft {
  id: string | null;
  stage: ResearchStage;
  type: ProjectArtifactType;
  title: string;
  body: string;
  status: ProjectArtifactStatus;
}

interface ArtifactSaveAction {
  id: string | null;
  create: ProjectArtifactCreateBody;
  patch: ProjectArtifactPatchBody;
}

interface SourceNoteAction {
  sourceId: string;
  note: string | null;
}

function ProjectListItem({
  project,
  active,
  onClick,
}: {
  project: ResearchProject;
  active: boolean;
  onClick: () => void;
}): JSX.Element {
  return (
    <button type="button" className={cx("ph-proj-item", active && "is-active")} onClick={onClick}>
      <span className="ph-proj-item-top">
        <strong>{project.name}</strong>
        <span className={cx("ph-proj-state-dot", project.status === "archived" && "is-archived")} />
      </span>
      <span className="ph-proj-item-stage">{stageDef(project.stage).label}</span>
      <span className="ph-proj-item-meta">
        {project.source_count} 篇文献 · {project.artifact_count} 条记录
      </span>
    </button>
  );
}

function SourceCard({
  source,
  editing,
  noteValue,
  confirmingRemove,
  busy,
  onStartNote,
  onNoteChange,
  onSaveNote,
  onCancelNote,
  onAskRemove,
  onRemove,
  onCancelRemove,
}: {
  source: ProjectSource;
  editing: boolean;
  noteValue: string;
  confirmingRemove: boolean;
  busy: boolean;
  onStartNote: () => void;
  onNoteChange: (value: string) => void;
  onSaveNote: () => void;
  onCancelNote: () => void;
  onAskRemove: () => void;
  onRemove: () => void;
  onCancelRemove: () => void;
}): JSX.Element {
  const result = source.result;
  return (
    <article className="ph-proj-source-card">
      <div className="ph-proj-source-head">
        <div>
          {result.url === null ? (
            <h4>{result.title}</h4>
          ) : (
            <a href={result.url} target="_blank" rel="noreferrer">{result.title}</a>
          )}
          <p>
            {[result.year?.toString(), result.venue, result.sources.join(" + ")]
              .filter((value): value is string => value !== null && value !== undefined && value !== "")
              .join(" · ")}
          </p>
        </div>
        <span className={cx("ph-proj-analysis", result.analysis_mode === "llm" && "is-ai")}>
          {result.analysis_model ?? (result.analysis_mode === "llm" ? "AI 深读" : "摘要提取")}
        </span>
      </div>

      {(result.summary_zh !== "" || result.core_trick !== "") && (
        <div className="ph-proj-source-insight">
          {result.summary_zh !== "" && <p>{result.summary_zh}</p>}
          {result.core_trick !== "" && <span><strong>核心 Trick</strong>{result.core_trick}</span>}
        </div>
      )}

      {editing ? (
        <div className="ph-proj-note-editor">
          <textarea
            value={noteValue}
            onChange={(event) => onNoteChange(event.target.value)}
            placeholder="为什么把这篇论文纳入项目？它支持或反驳了什么？"
            rows={3}
          />
          <div>
            <button type="button" onClick={onCancelNote}>取消</button>
            <button type="button" className="is-primary" onClick={onSaveNote} disabled={busy}>
              {busy ? "保存中…" : "保存备注"}
            </button>
          </div>
        </div>
      ) : (
        <button type="button" className="ph-proj-source-note" onClick={onStartNote}>
          <span>证据备注</span>
          <p>{source.note ?? "添加纳入理由或证据关系"}</p>
        </button>
      )}

      <div className="ph-proj-source-foot">
        <span>加入于 {fmtDate(source.added_at)}</span>
        {confirmingRemove ? (
          <span className="ph-proj-inline-confirm">
            从项目移除？
            <button type="button" onClick={onCancelRemove}>取消</button>
            <button type="button" className="is-danger" onClick={onRemove} disabled={busy}>移除</button>
          </span>
        ) : (
          <button type="button" onClick={onAskRemove}>移除</button>
        )}
      </div>
    </article>
  );
}

function ArtifactCard({
  artifact,
  confirmingRemove,
  busy,
  onEdit,
  onAskRemove,
  onRemove,
  onCancelRemove,
}: {
  artifact: ProjectArtifact;
  confirmingRemove: boolean;
  busy: boolean;
  onEdit: () => void;
  onAskRemove: () => void;
  onRemove: () => void;
  onCancelRemove: () => void;
}): JSX.Element {
  return (
    <article className="ph-proj-artifact-card">
      <div className="ph-proj-artifact-top">
        <span>{TYPE_LABEL[artifact.type]}</span>
        <span className={cx("ph-proj-artifact-status", `is-${artifact.status}`)}>
          {STATUS_LABEL[artifact.status]}
        </span>
      </div>
      <h4>{artifact.title}</h4>
      <p>{artifact.body}</p>
      <div className="ph-proj-artifact-foot">
        <span>更新于 {fmtDate(artifact.updated_at ?? artifact.created_at)}</span>
        <button type="button" onClick={onEdit}>编辑</button>
        {confirmingRemove ? (
          <span className="ph-proj-inline-confirm">
            确认删除？
            <button type="button" onClick={onCancelRemove}>取消</button>
            <button type="button" className="is-danger" onClick={onRemove} disabled={busy}>删除</button>
          </span>
        ) : (
          <button type="button" onClick={onAskRemove}>删除</button>
        )}
      </div>
    </article>
  );
}

export function ProjectsView(): JSX.Element {
  const qc = useQueryClient();
  const activeProjectId = useUI((s) => s.activeProjectId);
  const setActiveProject = useUI((s) => s.setActiveProject);

  const [showArchived, setShowArchived] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState("");
  const [createQuestion, setCreateQuestion] = useState("");
  const [createDescription, setCreateDescription] = useState("");
  const [editOpen, setEditOpen] = useState(false);
  const [projectDraft, setProjectDraft] = useState<ProjectDraft>({ name: "", description: "", research_question: "" });
  const [stageDraft, setStageDraft] = useState<ResearchStage>("discovery");
  const [viewedStage, setViewedStage] = useState<ResearchStage>("discovery");
  const [artifactDraft, setArtifactDraft] = useState<ArtifactDraft | null>(null);
  const [sourceNote, setSourceNote] = useState<{ id: string; value: string } | null>(null);
  const [confirmSourceId, setConfirmSourceId] = useState<string | null>(null);
  const [confirmArtifactId, setConfirmArtifactId] = useState<string | null>(null);
  const [confirmProject, setConfirmProject] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["research-projects"],
    queryFn: api.projects.list,
  });
  const projects = useMemo(() => projectsQuery.data ?? [], [projectsQuery.data]);

  useEffect(() => {
    if (projects.length === 0) {
      if (activeProjectId !== null) setActiveProject(null);
      return;
    }
    if (activeProjectId === null || !projects.some((project) => project.id === activeProjectId)) {
      setActiveProject(projects[0].id);
    }
  }, [activeProjectId, projects, setActiveProject]);

  const projectQuery = useQuery({
    queryKey: ["research-project", activeProjectId ?? ""],
    queryFn: () => api.projects.get(activeProjectId ?? ""),
    enabled: activeProjectId !== null,
  });
  const project =
    projectQuery.data ?? projects.find((item) => item.id === activeProjectId) ?? null;

  useEffect(() => {
    if (project === null) return;
    setStageDraft(project.stage);
    setViewedStage(project.stage);
    setEditOpen(false);
    setArtifactDraft(null);
    setSourceNote(null);
    setConfirmProject(false);
    setNotice(null);
  }, [project?.id]);

  const visibleProjects = useMemo(
    () => projects.filter((item) => showArchived || item.status !== "archived"),
    [projects, showArchived],
  );

  const storeProject = (updated: ResearchProject): void => {
    qc.setQueryData(["research-project", updated.id], updated);
    qc.setQueryData<ResearchProject[]>(["research-projects"], (current) =>
      current?.map((item) => item.id === updated.id ? updated : item),
    );
  };

  const refreshProject = async (id: string): Promise<void> => {
    await qc.invalidateQueries({ queryKey: ["research-project", id] });
    await qc.invalidateQueries({ queryKey: ["research-projects"] });
  };

  const createMutation = useMutation({
    mutationFn: (data: ResearchProjectCreateBody) => api.projects.create(data),
    onSuccess: async (created) => {
      qc.setQueryData<ResearchProject[]>(["research-projects"], (current) => [
        created,
        ...(current?.filter((item) => item.id !== created.id) ?? []),
      ]);
      setActiveProject(created.id);
      setCreateOpen(false);
      setCreateName("");
      setCreateQuestion("");
      setCreateDescription("");
      setNotice(`项目「${created.name}」已创建`);
      await qc.invalidateQueries({ queryKey: ["research-projects"] });
      qc.setQueryData(["research-project", created.id], created);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: ResearchProjectPatchBody }) =>
      api.projects.update(id, patch),
    onSuccess: (updated, { patch }) => {
      storeProject(updated);
      setStageDraft(updated.stage);
      // A deliberate stage PATCH changes the workflow's current position, so
      // the records pane must follow it. Ordinary metadata/status edits do not
      // disturb a stage the user clicked only to browse historical records.
      if (patch.stage !== undefined) setViewedStage(updated.stage);
      setEditOpen(false);
      setNotice("项目已更新");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.projects.remove(id),
    onSuccess: async (_, deletedId) => {
      qc.setQueryData<ResearchProject[]>(["research-projects"], (current) =>
        current?.filter((item) => item.id !== deletedId),
      );
      qc.removeQueries({ queryKey: ["research-project", deletedId] });
      setActiveProject(null);
      setConfirmProject(false);
      await qc.invalidateQueries({ queryKey: ["research-projects"] });
    },
  });

  const advanceMutation = useMutation({
    mutationFn: (id: string) => api.projects.advance(id),
    onSuccess: (updated) => {
      storeProject(updated);
      setStageDraft(updated.stage);
      setViewedStage(updated.stage);
      setNotice(`已推进到「${stageDef(updated.stage).label}」`);
    },
  });

  const sourceNoteMutation = useMutation({
    mutationFn: ({ sourceId, note }: SourceNoteAction) => {
      if (project === null) throw new Error("项目不存在");
      return api.projects.updateSource(project.id, sourceId, { note });
    },
    onSuccess: async () => {
      if (project !== null) await refreshProject(project.id);
      setSourceNote(null);
      setNotice("证据备注已保存");
    },
  });

  const removeSourceMutation = useMutation({
    mutationFn: (sourceId: string) => {
      if (project === null) throw new Error("项目不存在");
      return api.projects.removeSource(project.id, sourceId);
    },
    onSuccess: async () => {
      if (project !== null) await refreshProject(project.id);
      setConfirmSourceId(null);
      setNotice("文献已从项目移除，探索历史仍保留");
    },
  });

  const saveArtifactMutation = useMutation({
    mutationFn: ({ id, create, patch }: ArtifactSaveAction) => {
      if (project === null) throw new Error("项目不存在");
      return id === null
        ? api.projects.createArtifact(project.id, create)
        : api.projects.updateArtifact(project.id, id, patch);
    },
    onSuccess: async (saved) => {
      if (project !== null) await refreshProject(project.id);
      setArtifactDraft(null);
      setViewedStage(saved.stage);
      setNotice(saved.status === "verified" ? "记录已保存并标记为人工核验" : "研究记录已保存");
    },
  });

  const removeArtifactMutation = useMutation({
    mutationFn: (artifactId: string) => {
      if (project === null) throw new Error("项目不存在");
      return api.projects.removeArtifact(project.id, artifactId);
    },
    onSuccess: async () => {
      if (project !== null) await refreshProject(project.id);
      setConfirmArtifactId(null);
      setNotice("研究记录已删除");
    },
  });

  const createProject = (event: FormEvent): void => {
    event.preventDefault();
    const name = createName.trim();
    if (name === "") return;
    createMutation.mutate({
      name,
      ...(createQuestion.trim() === "" ? {} : { research_question: createQuestion.trim() }),
      ...(createDescription.trim() === "" ? {} : { description: createDescription.trim() }),
    });
  };

  const startEdit = (): void => {
    if (project === null) return;
    setProjectDraft({
      name: project.name,
      description: project.description,
      research_question: project.research_question,
    });
    setEditOpen(true);
  };

  const saveProject = (event: FormEvent): void => {
    event.preventDefault();
    if (project === null || projectDraft.name.trim() === "") return;
    updateMutation.mutate({
      id: project.id,
      patch: {
        name: projectDraft.name.trim(),
        description: projectDraft.description.trim(),
        research_question: projectDraft.research_question.trim(),
      },
    });
  };

  const openNewArtifact = (): void => {
    setArtifactDraft({
      id: null,
      stage: viewedStage,
      type: DEFAULT_TYPE[viewedStage],
      title: "",
      body: "",
      status: "draft",
    });
  };

  const openArtifact = (artifact: ProjectArtifact): void => {
    setArtifactDraft({
      id: artifact.id,
      stage: artifact.stage,
      type: artifact.type,
      title: artifact.title,
      body: artifact.body,
      status: artifact.status,
    });
  };

  const saveArtifact = (event: FormEvent): void => {
    event.preventDefault();
    if (artifactDraft === null) return;
    const title = artifactDraft.title.trim();
    const body = artifactDraft.body.trim();
    if (title === "" || body === "") return;
    saveArtifactMutation.mutate({
      id: artifactDraft.id,
      create: {
        stage: artifactDraft.stage,
        type: artifactDraft.type,
        title,
        body,
        status: artifactDraft.status,
      },
      patch: {
        stage: artifactDraft.stage,
        type: artifactDraft.type,
        title,
        body,
        status: artifactDraft.status,
      },
    });
  };

  const projectArtifacts = project?.artifacts ?? [];
  const viewedArtifacts = projectArtifacts.filter((artifact) => artifact.stage === viewedStage);
  const stageCounts = useMemo(() => {
    const counts = new Map<ResearchStage, number>();
    for (const artifact of projectArtifacts) counts.set(artifact.stage, (counts.get(artifact.stage) ?? 0) + 1);
    return counts;
  }, [projectArtifacts]);
  const currentStageIndex = project === null ? 0 : STAGES.findIndex((stage) => stage.id === project.stage);

  const busyError =
    createMutation.error ?? updateMutation.error ?? deleteMutation.error ?? advanceMutation.error ??
    sourceNoteMutation.error ?? removeSourceMutation.error ?? saveArtifactMutation.error ?? removeArtifactMutation.error;

  return (
    <div className="ph-proj">
      <aside className="ph-proj-sidebar ph-scroll">
        <div className="ph-proj-side-head">
          <div>
            <span>研究项目</span>
            <small>{projects.length}</small>
          </div>
          <button type="button" onClick={() => setCreateOpen((open) => !open)} title="新建项目">
            <Icons.plus size={14} />
          </button>
        </div>

        {createOpen && (
          <form className="ph-proj-create" onSubmit={createProject}>
            <strong>新建研究项目</strong>
            <input value={createName} onChange={(event) => setCreateName(event.target.value)} placeholder="项目名称" autoFocus />
            <textarea value={createQuestion} onChange={(event) => setCreateQuestion(event.target.value)} placeholder="核心研究问题（可选）" rows={2} />
            <textarea value={createDescription} onChange={(event) => setCreateDescription(event.target.value)} placeholder="项目说明（可选）" rows={2} />
            {createMutation.isError && <span className="ph-proj-form-error">{errText(createMutation.error)}</span>}
            <div>
              <button type="button" onClick={() => setCreateOpen(false)}>取消</button>
              <button type="submit" className="is-primary" disabled={createName.trim() === "" || createMutation.isPending}>
                {createMutation.isPending ? "创建中…" : "创建项目"}
              </button>
            </div>
          </form>
        )}

        <label className="ph-proj-archive-filter">
          <input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />
          显示已归档
        </label>

        {projectsQuery.isPending ? (
          <div className="ph-proj-side-state">正在读取项目…</div>
        ) : projectsQuery.isError ? (
          <div className="ph-proj-side-state is-error">
            <span>{errText(projectsQuery.error)}</span>
            <button type="button" onClick={() => void projectsQuery.refetch()}>重试</button>
          </div>
        ) : visibleProjects.length === 0 ? (
          <div className="ph-proj-side-state">
            <Icons.kb size={20} />
            <span>{projects.length === 0 ? "还没有研究项目" : "没有符合筛选的项目"}</span>
          </div>
        ) : (
          <div className="ph-proj-list">
            {visibleProjects.map((item) => (
              <ProjectListItem
                key={item.id}
                project={item}
                active={item.id === activeProjectId}
                onClick={() => setActiveProject(item.id)}
              />
            ))}
          </div>
        )}
      </aside>

      <main className="ph-proj-main ph-scroll">
        {project === null ? (
          <div className="ph-proj-empty">
            <span><Icons.kb size={26} /></span>
            <h1>建立一个可持续推进的研究项目</h1>
            <p>项目把探索结果、证据备注、实验计划、真实结果和论文主张放在同一条可追溯路径上。</p>
            <button type="button" onClick={() => setCreateOpen(true)}>新建项目</button>
          </div>
        ) : projectQuery.isError ? (
          <div className="ph-proj-empty is-error">
            <h1>项目加载失败</h1>
            <p>{errText(projectQuery.error)}</p>
            <button type="button" onClick={() => void projectQuery.refetch()}>重试</button>
          </div>
        ) : (
          <div className="ph-proj-desk">
            <header className="ph-proj-header">
              <div className="ph-proj-header-title">
                <div className="ph-proj-header-kicker">
                  <span className={cx("ph-proj-status", project.status === "archived" && "is-archived")}>
                    {project.status === "active" ? "进行中" : "已归档"}
                  </span>
                  <span>{stageDef(project.stage).label}</span>
                </div>
                <h1>{project.name}</h1>
                {project.description !== "" && <p>{project.description}</p>}
              </div>
              <div className="ph-proj-header-actions">
                <button type="button" onClick={startEdit}>编辑</button>
                <button
                  type="button"
                  onClick={() => updateMutation.mutate({
                    id: project.id,
                    patch: { status: project.status === "active" ? "archived" : "active" },
                  })}
                  disabled={updateMutation.isPending}
                >
                  {project.status === "active" ? "归档" : "恢复"}
                </button>
                {confirmProject ? (
                  <span className="ph-proj-delete-confirm">
                    删除整个项目？
                    <button type="button" onClick={() => setConfirmProject(false)}>取消</button>
                    <button type="button" className="is-danger" onClick={() => deleteMutation.mutate(project.id)} disabled={deleteMutation.isPending}>确认删除</button>
                  </span>
                ) : (
                  <button type="button" className="is-danger-text" onClick={() => setConfirmProject(true)}>删除</button>
                )}
              </div>
            </header>

            {editOpen && (
              <form className="ph-proj-edit" onSubmit={saveProject}>
                <label>
                  <span>项目名称</span>
                  <input value={projectDraft.name} onChange={(event) => setProjectDraft((draft) => ({ ...draft, name: event.target.value }))} />
                </label>
                <label>
                  <span>研究问题</span>
                  <textarea value={projectDraft.research_question} onChange={(event) => setProjectDraft((draft) => ({ ...draft, research_question: event.target.value }))} rows={3} />
                </label>
                <label>
                  <span>项目说明</span>
                  <textarea value={projectDraft.description} onChange={(event) => setProjectDraft((draft) => ({ ...draft, description: event.target.value }))} rows={3} />
                </label>
                <div className="ph-proj-edit-actions">
                  <button type="button" onClick={() => setEditOpen(false)}>取消</button>
                  <button type="submit" className="is-primary" disabled={projectDraft.name.trim() === "" || updateMutation.isPending}>
                    {updateMutation.isPending ? "保存中…" : "保存项目"}
                  </button>
                </div>
              </form>
            )}

            {project.research_question !== "" && !editOpen && (
              <section className="ph-proj-question">
                <span>核心研究问题</span>
                <p>{project.research_question}</p>
              </section>
            )}

            {notice !== null && <div className="ph-proj-notice"><Icons.check size={14} />{notice}</div>}
            {busyError !== null && <div className="ph-proj-error"><Icons.alert size={15} />{errText(busyError)}</div>}

            <section className="ph-proj-stage-section">
              <div className="ph-proj-section-head">
                <div>
                  <span>研究路径</span>
                  <h2>{stageDef(project.stage).note}</h2>
                </div>
                <div className="ph-proj-stage-controls">
                  <select value={stageDraft} onChange={(event) => setStageDraft(event.target.value as ResearchStage)} aria-label="修改项目阶段">
                    {STAGES.map((stage) => <option key={stage.id} value={stage.id}>{stage.label}</option>)}
                  </select>
                  <button
                    type="button"
                    disabled={stageDraft === project.stage || updateMutation.isPending}
                    onClick={() => updateMutation.mutate({ id: project.id, patch: { stage: stageDraft } })}
                  >
                    保存阶段
                  </button>
                  <button
                    type="button"
                    className="is-primary"
                    disabled={project.stage === "complete" || project.status === "archived" || advanceMutation.isPending}
                    onClick={() => advanceMutation.mutate(project.id)}
                  >
                    {advanceMutation.isPending ? "推进中…" : "进入下一阶段"}
                  </button>
                </div>
              </div>

              <div className="ph-proj-timeline ph-scroll">
                {STAGES.map((stage, index) => {
                  const count = stageCounts.get(stage.id) ?? 0;
                  return (
                    <button
                      key={stage.id}
                      type="button"
                      className={cx(
                        "ph-proj-stage",
                        index < currentStageIndex && "is-past",
                        stage.id === project.stage && "is-current",
                        stage.id === viewedStage && "is-viewed",
                      )}
                      onClick={() => setViewedStage(stage.id)}
                    >
                      <span className="ph-proj-stage-node">{index + 1}</span>
                      <strong>{stage.short}</strong>
                      <small>{count === 0 ? "无记录" : `${count} 条`}</small>
                    </button>
                  );
                })}
              </div>
              <p className="ph-proj-stage-help">点击阶段查看对应记录；阶段下拉可显式回退，所有调整只改变项目状态，不会触发自动实验。</p>
            </section>

            <div className="ph-proj-workgrid">
              <section className="ph-proj-panel">
                <div className="ph-proj-panel-head">
                  <div>
                    <span>项目文献</span>
                    <h2>{project.sources.length} 篇证据来源</h2>
                  </div>
                </div>

                {project.sources.length === 0 ? (
                  <div className="ph-proj-panel-empty">
                    <Icons.file size={20} />
                    <strong>还没有项目文献</strong>
                    <span>前往「文献探索」检索并选择论文加入这个项目。</span>
                  </div>
                ) : (
                  <div className="ph-proj-source-list">
                    {project.sources.map((source) => (
                      <SourceCard
                        key={source.id}
                        source={source}
                        editing={sourceNote?.id === source.id}
                        noteValue={sourceNote?.id === source.id ? sourceNote.value : ""}
                        confirmingRemove={confirmSourceId === source.id}
                        busy={sourceNoteMutation.isPending || removeSourceMutation.isPending}
                        onStartNote={() => setSourceNote({ id: source.id, value: source.note ?? "" })}
                        onNoteChange={(value) => setSourceNote({ id: source.id, value })}
                        onSaveNote={() => sourceNoteMutation.mutate({ sourceId: source.id, note: sourceNote?.value.trim() || null })}
                        onCancelNote={() => setSourceNote(null)}
                        onAskRemove={() => setConfirmSourceId(source.id)}
                        onRemove={() => removeSourceMutation.mutate(source.id)}
                        onCancelRemove={() => setConfirmSourceId(null)}
                      />
                    ))}
                  </div>
                )}
              </section>

              <section className="ph-proj-panel is-artifacts">
                <div className="ph-proj-panel-head">
                  <div>
                    <span>{stageDef(viewedStage).label}</span>
                    <h2>{viewedArtifacts.length} 条研究记录</h2>
                  </div>
                  <button type="button" className="is-primary" onClick={openNewArtifact}>
                    <Icons.plus size={13} />新建记录
                  </button>
                </div>

                <div className="ph-proj-automation-note">
                  <Icons.alert size={15} />
                  <span title={project.automation_notice}>当前仅保存科研记录，不会运行代码或实验；实验结果与人工核验状态必须由研究者根据真实证据填写。</span>
                </div>

                {artifactDraft !== null && (
                  <form className="ph-proj-artifact-editor" onSubmit={saveArtifact}>
                    <div className="ph-proj-artifact-editor-head">
                      <strong>{artifactDraft.id === null ? "新建研究记录" : "编辑研究记录"}</strong>
                      <button type="button" onClick={() => setArtifactDraft(null)}><Icons.close size={11} /></button>
                    </div>
                    <div className="ph-proj-artifact-fields">
                      <label>
                        <span>阶段</span>
                        <select value={artifactDraft.stage} onChange={(event) => setArtifactDraft((draft) => draft === null ? null : { ...draft, stage: event.target.value as ResearchStage })}>
                          {STAGES.map((stage) => <option key={stage.id} value={stage.id}>{stage.label}</option>)}
                        </select>
                      </label>
                      <label>
                        <span>类型</span>
                        <select value={artifactDraft.type} onChange={(event) => setArtifactDraft((draft) => draft === null ? null : { ...draft, type: event.target.value as ProjectArtifactType })}>
                          {Object.entries(TYPE_LABEL).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                        </select>
                      </label>
                      <label>
                        <span>状态</span>
                        <select value={artifactDraft.status} onChange={(event) => setArtifactDraft((draft) => draft === null ? null : { ...draft, status: event.target.value as ProjectArtifactStatus })}>
                          {Object.entries(STATUS_LABEL).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
                        </select>
                      </label>
                    </div>
                    <label>
                      <span>标题</span>
                      <input value={artifactDraft.title} onChange={(event) => setArtifactDraft((draft) => draft === null ? null : { ...draft, title: event.target.value })} placeholder="一句话说明这条记录" autoFocus />
                    </label>
                    <label>
                      <span>正文</span>
                      <textarea value={artifactDraft.body} onChange={(event) => setArtifactDraft((draft) => draft === null ? null : { ...draft, body: event.target.value })} placeholder="记录假设、实验约束、真实结果、主张或审阅意见。不要把计划写成已经执行。" rows={9} />
                    </label>
                    <div className="ph-proj-artifact-actions">
                      <button type="button" onClick={() => setArtifactDraft(null)}>取消</button>
                      <button type="submit" className="is-primary" disabled={artifactDraft.title.trim() === "" || artifactDraft.body.trim() === "" || saveArtifactMutation.isPending}>
                        {saveArtifactMutation.isPending ? "保存中…" : "保存记录"}
                      </button>
                    </div>
                  </form>
                )}

                {viewedArtifacts.length === 0 && artifactDraft === null ? (
                  <div className="ph-proj-panel-empty">
                    <Icons.spark size={20} />
                    <strong>这个阶段还没有记录</strong>
                    <span>新建一条真实的科研记录；系统不会替你声称实验已经执行。</span>
                  </div>
                ) : (
                  <div className="ph-proj-artifact-list">
                    {viewedArtifacts.map((artifact) => (
                      <ArtifactCard
                        key={artifact.id}
                        artifact={artifact}
                        confirmingRemove={confirmArtifactId === artifact.id}
                        busy={removeArtifactMutation.isPending}
                        onEdit={() => openArtifact(artifact)}
                        onAskRemove={() => setConfirmArtifactId(artifact.id)}
                        onRemove={() => removeArtifactMutation.mutate(artifact.id)}
                        onCancelRemove={() => setConfirmArtifactId(null)}
                      />
                    ))}
                  </div>
                )}
              </section>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

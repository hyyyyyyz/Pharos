import { useCallback, useEffect, useRef, useState } from "react";
import {
  harnessApi,
  type HarnessApproval,
  type HarnessArtifact,
  type HarnessEvent,
  type HarnessRun,
  type HarnessRunDetail,
  type HarnessRunState,
} from "../api/harness";
import { ApiError } from "../api/client";
import "./HarnessRunCenter.css";

/** Poll while any visible run may still move; terminal runs stop the clock. */
const ACTIVE_STATES: HarnessRunState[] = [
  "queued",
  "running",
  "waiting_for_approval",
  "waiting_for_input",
  "paused",
];

const STATE_LABEL: Record<HarnessRunState, string> = {
  queued: "排队中",
  running: "运行中",
  waiting_for_approval: "等待批准",
  waiting_for_input: "等待输入",
  paused: "已暂停",
  succeeded: "已成功",
  failed: "已失败",
  cancelled: "已取消",
  indeterminate: "结果待定",
};

const OUTCOME_LABEL: Record<string, string> = {
  complete: "完整",
  partial: "部分",
  incomplete: "不完整",
};

function formatUs(us: number | null): string {
  if (us === null) return "—";
  const date = new Date(us / 1000);
  return date.toLocaleString("zh-CN", { hour12: false });
}

function RunStatePill({ state }: { state: HarnessRunState }): JSX.Element {
  return <span className={`ph-run-state ph-run-state--${state}`}>{STATE_LABEL[state]}</span>;
}

function StepRow({ step }: { step: HarnessRunDetail["steps"][number] }): JSX.Element {
  const detail = step.errorMessage || step.skipReason || step.waitingReason;
  return (
    <li className="ph-run-step" title={detail ?? undefined}>
      <span className={`ph-run-step-dot ph-run-step-dot--${step.state}`} aria-hidden />
      <span className="ph-run-step-key">{step.definitionStepKey}</span>
      <span className="ph-run-step-state">{step.state}</span>
      <span className="ph-run-step-attempts">
        {step.attemptCount > 0 ? `${step.attemptCount} 次尝试` : ""}
      </span>
      {detail && <span className="ph-run-step-detail">{detail}</span>}
    </li>
  );
}

function ApprovalCard({
  approval,
  onDecide,
}: {
  approval: HarnessApproval;
  onDecide: (approval: HarnessApproval, decision: "approved" | "rejected") => void;
}): JSX.Element {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "approved" | "rejected"): Promise<void> => {
    setBusy(true);
    try {
      await onDecide(approval, decision);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="ph-run-approval">
      <div className="ph-run-approval-action">
        {approval.action} · {approval.state}
      </div>
      <div className="ph-run-approval-effect">
        {JSON.stringify(approval.effectSummary, null, 0)}
      </div>
      <div className="ph-run-approval-row">
        <input
          type="text"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="理由（可选）"
          disabled={busy}
        />
        <button type="button" disabled={busy} onClick={() => void decide("approved")}>
          批准
        </button>
        <button type="button" disabled={busy} onClick={() => void decide("rejected")}>
          拒绝
        </button>
      </div>
    </div>
  );
}

function ArtifactBlock({ artifact }: { artifact: HarnessArtifact }): JSX.Element {
  return (
    <div className="ph-run-artifact">
      <div className="ph-run-artifact-head">
        <span>{artifact.artifactType}</span>
        <span className="ph-run-artifact-meta">
          {artifact.schemaName}@{artifact.schemaVersion} · {artifact.producerKind}
        </span>
      </div>
      {artifact.deleted ? (
        <div className="ph-run-artifact-deleted">内容已删除：{artifact.deletionReason ?? "—"}</div>
      ) : (
        <pre className="ph-run-artifact-body">
          {JSON.stringify(artifact.content, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function HarnessRunCenter(): JSX.Element {
  const [runs, setRuns] = useState<HarnessRun[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<HarnessRunDetail | null>(null);
  const [artifacts, setArtifacts] = useState<HarnessArtifact[]>([]);
  const [approvals, setApprovals] = useState<HarnessApproval[]>([]);
  const [events, setEvents] = useState<HarnessEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "active" | HarnessRunState>("all");
  const refreshTimer = useRef<number | null>(null);

  const refreshList = useCallback(async () => {
    try {
      const page = await harnessApi.listRuns();
      setRuns(page.runs);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  const refreshDetail = useCallback(async (runId: string) => {
    try {
      const [nextDetail, nextArtifacts, nextApprovals, eventPage] = await Promise.all([
        harnessApi.getRun(runId),
        harnessApi.artifacts(runId),
        harnessApi.approvals(runId),
        harnessApi.events(runId, 0),
      ]);
      setDetail(nextDetail);
      setArtifacts(nextArtifacts);
      setApprovals(nextApprovals);
      setEvents(eventPage.events);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refreshList();
    const timer = window.setInterval(() => void refreshList(), 4000);
    return () => window.clearInterval(timer);
  }, [refreshList]);

  useEffect(() => {
    if (selected === null) return;
    void refreshDetail(selected);
    const anyActive =
      detail !== null && ACTIVE_STATES.includes(detail.state);
    if (!anyActive && detail !== null) return;
    refreshTimer.current = window.setInterval(() => void refreshDetail(selected), 2500);
    return () => {
      if (refreshTimer.current !== null) window.clearInterval(refreshTimer.current);
    };
  }, [selected, refreshDetail, detail]);

  const visible =
    filter === "all"
      ? runs
      : filter === "active"
        ? runs.filter((run) => ACTIVE_STATES.includes(run.state))
        : runs.filter((run) => run.state === filter);

  const control = async (action: "pause" | "resume" | "cancel"): Promise<void> => {
    if (selected === null) return;
    try {
      if (action === "pause") await harnessApi.pause(selected);
      else if (action === "resume") await harnessApi.resume(selected);
      else await harnessApi.cancel(selected);
      await Promise.all([refreshDetail(selected), refreshList()]);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const decide = async (
    approval: HarnessApproval,
    decision: "approved" | "rejected",
  ): Promise<void> => {
    try {
      await harnessApi.decideApproval(approval.id, decision, "");
      if (selected !== null) await refreshDetail(selected);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <div className="ph-runcenter">
      <div className="ph-runcenter-list">
        <div className="ph-runcenter-list-head">
          <span>运行</span>
          <div className="ph-runcenter-filters">
            {(["all", "active"] as const).map((key) => (
              <button
                key={key}
                type="button"
                className={filter === key ? "is-on" : ""}
                onClick={() => setFilter(key)}
              >
                {key === "all" ? "全部" : "进行中"}
              </button>
            ))}
          </div>
        </div>
        {error && <div className="ph-runcenter-error">{error}</div>}
        <ul className="ph-runcenter-runs">
          {visible.map((run) => (
            <li key={run.id}>
              <button
                type="button"
                className={selected === run.id ? "is-active" : ""}
                onClick={() => setSelected(run.id)}
              >
                <span className="ph-runcenter-run-workflow">{run.workflowKey}</span>
                <RunStatePill state={run.state} />
                <span className="ph-runcenter-run-time">{formatUs(run.createdAtUs)}</span>
              </button>
            </li>
          ))}
          {visible.length === 0 && <li className="ph-runcenter-empty">暂无运行记录</li>}
        </ul>
      </div>

      <div className="ph-runcenter-detail">
        {detail === null ? (
          <div className="ph-runcenter-empty">选择左侧的运行查看详情</div>
        ) : (
          <>
            <div className="ph-runcenter-detail-head">
              <div>
                <h2>
                  {detail.workflowKey} v{detail.workflowVersion}
                </h2>
                <span className="ph-runcenter-detail-id">{detail.id}</span>
              </div>
              <div className="ph-runcenter-detail-actions">
                {detail.state === "running" && (
                  <button type="button" onClick={() => void control("pause")}>
                    暂停
                  </button>
                )}
                {detail.state === "paused" && (
                  <button type="button" onClick={() => void control("resume")}>
                    继续
                  </button>
                )}
                {ACTIVE_STATES.includes(detail.state) && (
                  <button type="button" onClick={() => void control("cancel")}>
                    取消
                  </button>
                )}
              </div>
            </div>
            <div className="ph-runcenter-detail-meta">
              <RunStatePill state={detail.state} />
              {detail.outcome && (
                <span className="ph-runcenter-outcome">
                  结果：{OUTCOME_LABEL[detail.outcome] ?? detail.outcome}
                </span>
              )}
              <span>创建 {formatUs(detail.createdAtUs)}</span>
              <span>结束 {formatUs(detail.finishedAtUs)}</span>
            </div>
            {detail.errorMessage && (
              <div className="ph-runcenter-error">{detail.errorMessage}</div>
            )}

            {approvals.filter((approval) => approval.state === "pending").length > 0 && (
              <section className="ph-runcenter-section">
                <h3>等待批准</h3>
                {approvals
                  .filter((approval) => approval.state === "pending")
                  .map((approval) => (
                    <ApprovalCard key={approval.id} approval={approval} onDecide={decide} />
                  ))}
              </section>
            )}

            <section className="ph-runcenter-section">
              <h3>步骤</h3>
              <ul className="ph-runcenter-steps">
                {detail.steps.map((step) => (
                  <StepRow key={step.id} step={step} />
                ))}
              </ul>
            </section>

            <section className="ph-runcenter-section">
              <h3>产物</h3>
              {artifacts.length === 0 ? (
                <div className="ph-runcenter-empty">暂无产物</div>
              ) : (
                artifacts.map((artifact) => (
                  <ArtifactBlock key={artifact.id} artifact={artifact} />
                ))
              )}
            </section>

            <section className="ph-runcenter-section">
              <h3>事件</h3>
              <ul className="ph-runcenter-events">
                {events.map((event) => (
                  <li key={event.seq}>
                    <span className="ph-runcenter-event-seq">{event.seq}</span>
                    <span className="ph-runcenter-event-type">{event.event_type}</span>
                    <span className="ph-runcenter-event-time">
                      {formatUs(event.created_at_us)}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

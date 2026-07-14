import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Job, Paper } from "../api/types";

const STAGE_LABEL: Record<string, string> = {
  queued: "待译",
  parsing: "解析版面",
  translating: "译经中",
  typesetting: "重制卷帙",
  done: "已成",
  error: "有误",
};

function isActive(job: Job | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

export function PaperCard({ paper }: { paper: Paper }) {
  const [activeJobId, setActiveJobId] = useState<string | null>(
    isActive(paper.latest_job) ? paper.latest_job!.id : null,
  );

  const jobQuery = useQuery({
    queryKey: ["job", activeJobId],
    queryFn: () => api.getJob(activeJobId as string),
    enabled: !!activeJobId,
    refetchInterval: (query) => (isActive(query.state.data) ? 1200 : false),
  });

  // The card's live state comes from the tracked job (if any), else the paper's
  // last known job — so a freshly-finished translation shows its result without
  // needing to refetch the whole library.
  const job: Job | null = jobQuery.data ?? paper.latest_job;
  const status = job?.status;
  const running = isActive(job);
  const progress = job?.progress ?? 0;

  const translate = useMutation({
    mutationFn: () => api.translate(paper.id),
    onSuccess: (j) => setActiveJobId(j.id),
  });

  const tagLabel = running
    ? STAGE_LABEL[job?.stage ?? ""] ?? "译经中"
    : status === "done"
      ? "已成译本"
      : status === "error"
        ? "译经有误"
        : "未译";

  const sealChar = status === "done" ? "译" : status === "error" ? "误" : "待";

  return (
    <article className="xz-card xz-card--hover xz-card--gilt paper xz-ink-in">
      <div className="paper-top">
        <span className={`xz-tag tag-${status ?? "none"}`}>{tagLabel}</span>
        <span className="xz-seal paper-seal">{sealChar}</span>
      </div>

      <h3 className="paper-zh">{paper.title}</h3>
      <p className="paper-meta xz-faint">
        {paper.orig_filename} · {paper.page_count ?? "?"} 页
      </p>

      {running && (
        <div className="paper-progress" aria-label="翻译进度">
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${Math.max(progress, 4)}%` }} />
          </div>
          <span className="progress-label xz-faint">
            {STAGE_LABEL[job?.stage ?? ""] ?? "译经中"} · {progress.toFixed(0)}%
          </span>
        </div>
      )}

      <div className="paper-actions">
        {status === "done" ? (
          <>
            <a
              className="xz-btn xz-btn--primary"
              href={api.pdfUrl(paper.id, "mono")}
              target="_blank"
              rel="noreferrer"
            >
              阅读中文
            </a>
            <a className="xz-btn" href={api.pdfUrl(paper.id, "dual")} target="_blank" rel="noreferrer">
              中英对照
            </a>
          </>
        ) : running ? (
          <button className="xz-btn" disabled>
            译经中…
          </button>
        ) : (
          <>
            <button
              className="xz-btn xz-btn--primary"
              onClick={() => translate.mutate()}
              disabled={translate.isPending}
            >
              {translate.isPending ? "启程…" : "译此篇"}
            </button>
            <a
              className="xz-btn"
              href={api.pdfUrl(paper.id, "original")}
              target="_blank"
              rel="noreferrer"
            >
              原文
            </a>
          </>
        )}
      </div>

      {status === "error" && !running && (
        <p className="paper-error">译经受阻：{job?.error?.slice(0, 140)}</p>
      )}
      {translate.isError && <p className="paper-error">{(translate.error as Error).message}</p>}
    </article>
  );
}

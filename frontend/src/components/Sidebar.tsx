import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { JobStatus, Paper } from "../api/types";
import { useUI } from "../store";
import { useTheme } from "../hooks/useTheme";
import { UploadZone } from "./UploadZone";

const STATUS_DOT: Record<string, { label: string; cls: string }> = {
  done: { label: "已译", cls: "dot-done" },
  running: { label: "翻译中", cls: "dot-run" },
  queued: { label: "待译", cls: "dot-run" },
  error: { label: "有误", cls: "dot-err" },
};

function statusOf(p: Paper): JobStatus | "none" {
  return p.latest_job?.status ?? "none";
}

export function Sidebar() {
  const qc = useQueryClient();
  const { theme, toggle } = useTheme();
  const selectedPaperId = useUI((s) => s.selectedPaperId);
  const select = useUI((s) => s.select);
  const setView = useUI((s) => s.setView);

  const papersQuery = useQuery({ queryKey: ["papers"], queryFn: api.listPapers });
  const upload = useMutation({
    mutationFn: (file: File) => api.upload(file),
    onSuccess: (paper) => {
      qc.invalidateQueries({ queryKey: ["papers"] });
      select(paper.id);
    },
  });

  const papers = papersQuery.data ?? [];

  return (
    <aside className="sidebar">
      <header className="sidebar-head">
        <button className="brand brand-btn" onClick={() => setView("landing")} title="返回平台首页">
          <span className="xz-seal brand-seal">P</span>
          <span className="brand-name xz-gild">Pharos</span>
        </button>
        <button className="icon-btn" onClick={toggle} aria-label="切换昼夜" title="切换昼夜">
          {theme === "dark" ? "☾" : "☀"}
        </button>
      </header>

      <div className="sidebar-upload">
        <UploadZone onUpload={(f) => upload.mutate(f)} busy={upload.isPending} compact />
      </div>
      {upload.isError && <p className="paper-error side-error">{(upload.error as Error).message}</p>}

      <div className="sidebar-rule">
        <span>文 库</span>
        <span className="count xz-faint">{papers.length}</span>
      </div>

      <nav className="paper-list">
        {papersQuery.isLoading ? (
          <p className="side-empty xz-faint">展开译场…</p>
        ) : papersQuery.isError ? (
          <p className="side-empty paper-error">连不上后端（确认 ROG2 隧道）</p>
        ) : papers.length === 0 ? (
          <p className="side-empty xz-muted">译场尚空，拖入第一篇论文。</p>
        ) : (
          papers.map((p) => {
            const st = statusOf(p);
            const dot = STATUS_DOT[st];
            return (
              <button
                key={p.id}
                className={`paper-item${selectedPaperId === p.id ? " is-active" : ""}`}
                onClick={() => select(p.id)}
              >
                <span className={`status-dot ${dot?.cls ?? "dot-none"}`} />
                <span className="item-body">
                  <span className="item-title">{p.title}</span>
                  <span className="item-meta xz-faint">
                    {dot?.label ?? "未译"} · {p.page_count ?? "?"} 页
                  </span>
                </span>
              </button>
            );
          })
        )}
      </nav>
    </aside>
  );
}

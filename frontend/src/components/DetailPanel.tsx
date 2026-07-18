/**
 * 文库 view — the 280px right-hand detail panel.
 *
 * Ported from the design prototype (Pharos.dc.html lines 154-194 / renderVals
 * lines 606-609). The prototype's mock papers carried authors, venue, year, DOI,
 * abstract and tags; the backend supplies none of those yet, so those rows fall
 * back to `dash()` / muted placeholders rather than invented data.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Paper } from "../api/types";
import { Icons } from "../design/icons";
import { dash, isJobActive, statusOf, toVM } from "../lib/model";
import { useUI } from "../store";
import "./DetailPanel.css";

/** Job error messages can be a whole stack trace; the panel only has room for a line. */
const ERR_MAX = 120;

export function DetailPanel(): JSX.Element {
  const selectedPaperId = useUI((s) => s.selectedPaperId);
  const openPaper = useUI((s) => s.openPaper);
  const qc = useQueryClient();
  const id = selectedPaperId ?? "";

  // The list already holds ["papers"]; reading it here avoids an empty flash
  // while ["paper", id] loads after a selection change.
  const papersQuery = useQuery({ queryKey: ["papers"], queryFn: api.listPapers });
  const detailQuery = useQuery({
    queryKey: ["paper", id],
    queryFn: () => api.getPaper(id),
    enabled: id !== "",
    refetchInterval: (q) =>
      isJobActive((q.state.data as Paper | undefined)?.latest_job) ? 1500 : false,
  });

  const translate = useMutation({
    mutationFn: () => api.translate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["papers"] });
      qc.invalidateQueries({ queryKey: ["paper", id] });
    },
  });

  const paper: Paper | null =
    detailQuery.data ?? papersQuery.data?.find((p) => p.id === id) ?? null;

  if (!paper) {
    return (
      <aside className="ph-dp ph-scroll">
        <div className="ph-dp-empty">
          选择一个条目
          <br />
          查看详情
        </div>
      </aside>
    );
  }

  const job = paper.latest_job;
  const status = statusOf(job);
  const translated = status === "translated";
  const translating = status === "translating";
  const progress = Math.round(job?.progress ?? 0);
  const failedError = status === "failed" ? (job?.error ?? null) : null;
  // Always empty until the backend extracts metadata, but the chip markup is live.
  const tags = toVM(paper).tags;

  return (
    <aside className="ph-dp ph-scroll">
      <div className="ph-dp-body">
        <div className="ph-dp-title">{paper.title || paper.orig_filename}</div>
        <div className="ph-dp-sub">{paper.orig_filename}</div>

        <div className="ph-dp-actions">
          {translated ? (
            <button type="button" className="ph-dp-primary" onClick={() => openPaper(paper.id)}>
              <span className="ph-dp-ic">
                <Icons.open />
              </span>
              打开阅读
            </button>
          ) : (
            <button
              type="button"
              className="ph-dp-primary"
              disabled={translate.isPending || isJobActive(job)}
              onClick={() => translate.mutate()}
            >
              <span className="ph-dp-ic">
                <Icons.spark />
              </span>
              翻译此篇 · 保留排版
            </button>
          )}
          {!translated && (
            <button
              type="button"
              className="ph-dp-secondary"
              title="打开阅读（原文）"
              onClick={() => openPaper(paper.id)}
            >
              <span className="ph-dp-ic">
                <Icons.open />
              </span>
              阅读原文
            </button>
          )}
        </div>

        {translating && (
          <div className="ph-dp-prog">
            <div className="ph-dp-track">
              <span className="ph-dp-bar" style={{ width: `${progress}%` }} />
            </div>
            <div className="ph-dp-prog-label">翻译中 · {progress}%</div>
          </div>
        )}

        {failedError !== null && failedError !== "" && (
          <div className="ph-dp-error">
            {failedError.length > ERR_MAX ? `${failedError.slice(0, ERR_MAX)}…` : failedError}
          </div>
        )}

        <div className="ph-dp-grid">
          <span className="ph-dp-k">作者</span>
          <span className="ph-dp-v">{dash(null)}</span>
          <span className="ph-dp-k">来源</span>
          <span className="ph-dp-v">{dash(null)}</span>
          <span className="ph-dp-k">年份</span>
          <span className="ph-dp-v">{dash(null)}</span>
          <span className="ph-dp-k">页数</span>
          <span className="ph-dp-v">
            {paper.page_count === null ? dash(null) : `${paper.page_count} 页`}
          </span>
          <span className="ph-dp-k">DOI</span>
          <span className="ph-dp-v-doi">{dash(null)}</span>
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label">摘要</div>
          <div className="ph-dp-muted ph-dp-muted-ab">暂无摘要 · 后端尚未提取元数据</div>
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label ph-dp-label-7">附件</div>
          <div className="ph-dp-file">
            <span className="ph-dp-file-ic">
              <Icons.file />
            </span>
            <span className="ph-dp-file-name">{paper.orig_filename}</span>
          </div>
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label ph-dp-label-7">标签</div>
          {tags.length > 0 ? (
            <div className="ph-dp-tags">
              {tags.map((t) => (
                <span key={t} className="ph-dp-tag">
                  {t}
                </span>
              ))}
            </div>
          ) : (
            <div className="ph-dp-muted">暂无标签</div>
          )}
        </div>

        <div className="ph-dp-sec-last">
          <div className="ph-dp-label ph-dp-label-7">笔记</div>
          <div className="ph-dp-muted">暂无笔记 · 点击添加</div>
        </div>
      </div>
    </aside>
  );
}

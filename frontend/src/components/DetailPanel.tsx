/**
 * 文库 view — the 280px right-hand detail panel.
 *
 * Ported from the design prototype (Pharos.dc.html lines 154-194 / renderVals
 * lines 606-609). Every metadata row is now backed by real extraction, but the
 * backend reports nothing when it is unsure, so each row keeps its `dash()` /
 * muted fallback rather than showing an invented value.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Paper } from "../api/types";
import { Icons } from "../design/icons";
import { dash, isJobActive, statusOf, toVM } from "../lib/model";
import {
  isLocalZoteroPaperId,
  localZotero,
  type LocalZoteroAttachment,
  type LocalZoteroPaper,
} from "../lib/localZotero";
import { pdfTranslationEnabled, useSession, useUI } from "../store";
import "./DetailPanel.css";

/** Job error messages can be a whole stack trace; the panel only has room for a line. */
const ERR_MAX = 120;

function LocalZoteroDetail({ paper }: { paper: LocalZoteroPaper }): JSX.Element {
  const openPaper = useUI((s) => s.openPaper);
  const qc = useQueryClient();

  const importPaper = useMutation({
    mutationFn: async (attachment: LocalZoteroAttachment) =>
      api.upload(await localZotero.pdfFile(paper, attachment.id)),
    onSuccess: (imported) => {
      void qc.invalidateQueries({ queryKey: ["papers"] });
      void qc.invalidateQueries({ queryKey: ["collections"] });
      openPaper(imported.id);
    },
  });

  const abstract = paper.abstractText?.trim() ?? "";
  const defaultAttachment = paper.pdfAttachments.find(
    (attachment) => attachment.id === paper.pdfAttachmentId,
  );
  const formatSize = (bytes: number | null): string => {
    if (bytes === null) return "";
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
  };
  return (
    <aside className="ph-dp ph-scroll">
      <div className="ph-dp-body">
        <div className="ph-dp-title">{paper.title}</div>
        <div className="ph-dp-sub">
          本机 Zotero · {paper.libraryName}
        </div>

        <div className="ph-dp-actions">
          <button
            type="button"
            className="ph-dp-primary"
            disabled={!defaultAttachment?.available}
            onClick={() => openPaper(paper.id, defaultAttachment?.id)}
          >
            <span className="ph-dp-ic">
              <Icons.open />
            </span>
            {paper.pdfAvailable ? "打开本地 PDF" : "PDF 未在本机"}
          </button>
        </div>

        {!paper.pdfAvailable && (
          <div className="ph-dp-error">
            Zotero 中有附件记录，但文件尚未下载到这台 Mac。请先在 Zotero 中打开或下载附件，再重新同步。
          </div>
        )}
        {importPaper.isError && (
          <div className="ph-dp-error">导入失败：{String(importPaper.error)}</div>
        )}

        <div className="ph-dp-grid">
          <span className="ph-dp-k">作者</span>
          <span className="ph-dp-v">
            {paper.authors.length > 0 ? paper.authors.join(" · ") : dash(null)}
          </span>
          <span className="ph-dp-k">来源</span>
          <span className="ph-dp-v">{dash(paper.venue ?? paper.libraryName)}</span>
          <span className="ph-dp-k">年份</span>
          <span className="ph-dp-v">{dash(paper.year)}</span>
          <span className="ph-dp-k">附件</span>
          <span className="ph-dp-v">
            {paper.pdfAttachmentCount > 0
              ? `${paper.pdfAvailableCount}/${paper.pdfAttachmentCount} 份可读`
              : "没有 PDF"}
          </span>
          <span className="ph-dp-k">DOI</span>
          {paper.doi ? (
            <a
              className="ph-dp-v-doi"
              href={`https://doi.org/${paper.doi}`}
              target="_blank"
              rel="noreferrer"
              title={paper.doi}
            >
              {paper.doi}
            </a>
          ) : (
            <span className="ph-dp-v-doi">{dash(null)}</span>
          )}
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label">摘要</div>
          {abstract !== "" ? (
            <div className="ph-dp-abstract">{abstract}</div>
          ) : (
            <div className="ph-dp-muted ph-dp-muted-ab">Zotero 中暂无摘要</div>
          )}
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label ph-dp-label-7">附件</div>
          <div className="ph-dp-local-files">
            {paper.pdfAttachments.length > 0 ? (
              paper.pdfAttachments.map((attachment) => {
                const importing =
                  importPaper.isPending && importPaper.variables?.id === attachment.id;
                return (
                  <div className="ph-dp-local-file" key={attachment.id}>
                    <span className="ph-dp-file-ic">
                      <Icons.file />
                    </span>
                    <span className="ph-dp-local-file-main" title={attachment.filename}>
                      <span className="ph-dp-file-name">{attachment.filename}</span>
                      <span className="ph-dp-local-file-meta">
                        {attachment.available
                          ? ["本机可读", formatSize(attachment.sizeBytes)].filter(Boolean).join(" · ")
                          : "尚未下载"}
                      </span>
                    </span>
                    <span className="ph-dp-local-file-actions">
                      <button
                        type="button"
                        disabled={!attachment.available}
                        onClick={() => openPaper(paper.id, attachment.id)}
                      >
                        打开
                      </button>
                      <button
                        type="button"
                        disabled={!attachment.available || importPaper.isPending}
                        title="明确复制并上传这份 PDF，之后可使用翻译、领航与跨设备访问"
                        onClick={() => importPaper.mutate(attachment)}
                      >
                        {importing ? "导入中" : "导入"}
                      </button>
                    </span>
                  </div>
                );
              })
            ) : (
              <div className="ph-dp-muted">这个 Zotero 条目没有 PDF 附件</div>
            )}
          </div>
        </div>

        <div className="ph-dp-sec-last">
          <div className="ph-dp-label ph-dp-label-7">存储策略</div>
          <div className="ph-dp-muted">
            当前直接读取 Zotero 原文件；只有点击“导入 Pharos”才会上传。
          </div>
        </div>
      </div>
    </aside>
  );
}

export function DetailPanel(): JSX.Element {
  const selectedPaperId = useUI((s) => s.selectedPaperId);
  const openPaper = useUI((s) => s.openPaper);
  const pdfTx = useSession(pdfTranslationEnabled);
  const qc = useQueryClient();
  const id = selectedPaperId ?? "";
  const isLocal = isLocalZoteroPaperId(id);

  // The list already holds ["papers"]; reading it here avoids an empty flash
  // while ["paper", id] loads after a selection change.
  const papersQuery = useQuery({
    queryKey: ["papers"],
    queryFn: api.listPapers,
    enabled: !isLocal,
  });
  const detailQuery = useQuery({
    queryKey: ["paper", id],
    queryFn: () => api.getPaper(id),
    enabled: id !== "" && !isLocal,
    refetchInterval: (q) =>
      isJobActive((q.state.data as Paper | undefined)?.latest_job) ? 1500 : false,
  });
  const localDetailQuery = useQuery({
    queryKey: ["zotero-local", "paper", id],
    queryFn: () => localZotero.get(id),
    enabled: id !== "" && isLocal,
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

  if (isLocal) {
    const localPaper = localDetailQuery.data;
    if (localPaper) return <LocalZoteroDetail paper={localPaper} />;
    return (
      <aside className="ph-dp ph-scroll">
        <div className="ph-dp-empty">
          {localDetailQuery.isError ? "无法读取本地 Zotero 条目" : "正在读取本地 Zotero…"}
        </div>
      </aside>
    );
  }

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
  const vm = toVM(paper);
  // Always empty until tagging gets a backend, but the chip markup is live.
  const tags = vm.tags;
  const abstract = vm.abstract?.trim() ?? "";

  return (
    <aside className="ph-dp ph-scroll">
      <div className="ph-dp-body">
        <div className="ph-dp-title">{paper.title || paper.orig_filename}</div>
        <div className="ph-dp-sub">{paper.orig_filename}</div>

        {/* With translation off there is exactly one thing to do with a paper —
            read it — so the two-button split collapses to 打开阅读 for every
            row, translated or not. An already-translated paper still opens with
            its 中文/中英 modes intact; the reader decides that, not this panel. */}
        <div className="ph-dp-actions">
          {translated || !pdfTx ? (
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
          {!translated && pdfTx && (
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

        {/* Progress and failure are reports on a pipeline the user has switched
            off. A job started before the flip still runs to completion server-
            side (and its result appears as reading modes when it lands), but
            narrating it here would be exactly the greyed-out apparatus this is
            meant to remove. */}
        {translating && pdfTx && (
          <div className="ph-dp-prog">
            <div className="ph-dp-track">
              <span className="ph-dp-bar" style={{ width: `${progress}%` }} />
            </div>
            <div className="ph-dp-prog-label">翻译中 · {progress}%</div>
          </div>
        )}

        {pdfTx && failedError !== null && failedError !== "" && (
          <div className="ph-dp-error">
            {failedError.length > ERR_MAX ? `${failedError.slice(0, ERR_MAX)}…` : failedError}
          </div>
        )}

        <div className="ph-dp-grid">
          <span className="ph-dp-k">作者</span>
          <span className="ph-dp-v">
            {vm.authors.length > 0 ? vm.authors.join(" · ") : dash(null)}
          </span>
          <span className="ph-dp-k">来源</span>
          <span className="ph-dp-v">{dash(vm.venue)}</span>
          <span className="ph-dp-k">年份</span>
          <span className="ph-dp-v">{dash(vm.year)}</span>
          <span className="ph-dp-k">页数</span>
          <span className="ph-dp-v">
            {paper.page_count === null ? dash(null) : `${paper.page_count} 页`}
          </span>
          <span className="ph-dp-k">DOI</span>
          {vm.doi ? (
            <a
              className="ph-dp-v-doi"
              href={`https://doi.org/${vm.doi}`}
              target="_blank"
              rel="noreferrer"
              title={vm.doi}
            >
              {vm.doi}
            </a>
          ) : (
            <span className="ph-dp-v-doi">{dash(null)}</span>
          )}
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label">摘要</div>
          {abstract !== "" ? (
            <div className="ph-dp-abstract">{abstract}</div>
          ) : (
            <div className="ph-dp-muted ph-dp-muted-ab">暂无摘要 · 未能从该 PDF 提取</div>
          )}
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

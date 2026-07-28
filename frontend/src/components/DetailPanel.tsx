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
import { dash, isJobActive, statusOf, toVM, zoteroDetailToVM } from "../lib/model";
import {
  isLocalZoteroPaperId,
  localZotero,
  type LocalZoteroAttachment,
  type LocalZoteroPaper,
} from "../lib/localZotero";
import {
  isZoteroPdfAttachment,
  isZoteroSnapshotAttachment,
  parseZoteroItemId,
  zotero,
} from "../lib/zotero";
import type { ZoteroAttachment, ZoteroItemDetail } from "../types/zotero";
import { pdfTranslationEnabled, useSession, useUI } from "../store";
import "./DetailPanel.css";

/** Job error messages can be a whole stack trace; the panel only has room for a line. */
const ERR_MAX = 120;

const formatSize = (bytes: number | null): string => {
  if (bytes === null) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
};

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

function MirrorZoteroDetail({ detail }: { detail: ZoteroItemDetail }): JSX.Element {
  const openPaper = useUI((state) => state.openPaper);
  const qc = useQueryClient();
  const vm = zoteroDetailToVM(detail);
  const libraries = useQuery({
    queryKey: ["zotero-mirror", "libraries"],
    queryFn: zotero.libraries,
  });
  const libraryName =
    libraries.data?.find(
      (library) =>
        library.sourceId === detail.item.sourceId && library.libraryId === detail.item.libraryId,
    )?.name ?? "本地文库";
  const pdfAttachments = detail.attachments.filter(isZoteroPdfAttachment);
  const attachmentTitles = new Map(
    detail.children
      .filter((child) => child.itemType === "attachment")
      .map((child) => [child.key, child.title?.trim() || null]),
  );
  const notes = detail.children.filter((child) => child.itemType === "note");

  const importPaper = useMutation({
    mutationFn: async (attachment: ZoteroAttachment) =>
      api.upload(await zotero.attachmentFile(attachment, vm.title)),
    onSuccess: (imported) => {
      void qc.invalidateQueries({ queryKey: ["papers"] });
      void qc.invalidateQueries({ queryKey: ["collections"] });
      openPaper(imported.id);
    },
  });

  return (
    <aside className="ph-dp ph-scroll">
      <div className="ph-dp-body">
        <div className="ph-dp-title">{vm.title}</div>
        <div className="ph-dp-sub">本机 Zotero · {libraryName}</div>

        {pdfAttachments.length > 0 && !pdfAttachments.some((attachment) => attachment.available) && (
          <div className="ph-dp-error">
            Zotero 已记录 PDF，但文件尚未下载到这台设备；元数据、笔记和标注仍可离线查看。
          </div>
        )}
        {importPaper.isError && (
          <div className="ph-dp-error">导入失败：{String(importPaper.error)}</div>
        )}

        <div className="ph-dp-grid">
          <span className="ph-dp-k">作者</span>
          <span className="ph-dp-v">
            {vm.authors.length > 0 ? vm.authors.join(" · ") : dash(null)}
          </span>
          <span className="ph-dp-k">来源</span>
          <span className="ph-dp-v">{dash(vm.venue ?? libraryName)}</span>
          <span className="ph-dp-k">年份</span>
          <span className="ph-dp-v">{dash(vm.year)}</span>
          <span className="ph-dp-k">附件</span>
          <span className="ph-dp-v">
            {detail.attachments.length > 0
              ? `${detail.attachments.filter((attachment) => attachment.available).length}/${detail.attachments.length} 份本机可用`
              : "没有附件"}
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
          {vm.abstract?.trim() ? (
            <div className="ph-dp-abstract">{vm.abstract}</div>
          ) : (
            <div className="ph-dp-muted ph-dp-muted-ab">Zotero 中暂无摘要</div>
          )}
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label ph-dp-label-7">附件</div>
          <div className="ph-dp-local-files">
            {detail.attachments.length > 0 ? (
              detail.attachments.map((attachment) => {
                const pdf = isZoteroPdfAttachment(attachment);
                const snapshot = isZoteroSnapshotAttachment(attachment);
                const importing =
                  importPaper.isPending && importPaper.variables?.publicId === attachment.publicId;
                const displayName =
                  attachmentTitles.get(attachment.key) ??
                  attachment.filename ??
                  (snapshot ? "网页快照" : `Zotero 附件 ${attachment.key}`);
                return (
                  <div className="ph-dp-local-file" key={attachment.publicId}>
                    <span className="ph-dp-file-ic">
                      <Icons.file />
                    </span>
                    <span className="ph-dp-local-file-main" title={attachment.filename ?? undefined}>
                      <span className="ph-dp-file-name">
                        {displayName}
                      </span>
                      <span className="ph-dp-local-file-meta">
                        {attachment.available
                          ? [
                              pdf ? "PDF" : snapshot ? "网页快照" : attachment.contentType ?? "附件",
                              formatSize(attachment.sizeBytes),
                            ]
                              .filter(Boolean)
                              .join(" · ")
                          : "尚未下载"}
                      </span>
                    </span>
                    <span className="ph-dp-local-file-actions">
                      {pdf && (
                        <>
                          <button
                            type="button"
                            disabled={!attachment.available}
                            onClick={() => openPaper(vm.id, attachment.publicId)}
                          >
                            打开
                          </button>
                          <button
                            type="button"
                            disabled={!attachment.available || importPaper.isPending}
                            title="复制这份 PDF 到 Pharos，之后可翻译并跨设备访问"
                            onClick={() => importPaper.mutate(attachment)}
                          >
                            {importing ? "导入中" : "导入"}
                          </button>
                        </>
                      )}
                    </span>
                  </div>
                );
              })
            ) : (
              <div className="ph-dp-muted">这个 Zotero 条目没有附件</div>
            )}
          </div>
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label ph-dp-label-7">标签</div>
          {vm.tags.length > 0 ? (
            <div className="ph-dp-tags">
              {vm.tags.map((tag) => (
                <span key={tag} className="ph-dp-tag">{tag}</span>
              ))}
            </div>
          ) : (
            <div className="ph-dp-muted">暂无标签</div>
          )}
        </div>

        <div className="ph-dp-sec">
          <div className="ph-dp-label ph-dp-label-7">笔记</div>
          {notes.length > 0 ? (
            <div className="ph-dp-local-files">
              {notes.map((note) => (
                <div className="ph-dp-local-file" key={note.key}>
                  <span className="ph-dp-file-ic"><Icons.file /></span>
                  <span className="ph-dp-local-file-main">
                    <span className="ph-dp-file-name">{note.title || "Zotero 笔记"}</span>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="ph-dp-muted">暂无笔记</div>
          )}
        </div>

        <div className="ph-dp-sec-last">
          <div className="ph-dp-label ph-dp-label-7">PDF 标注</div>
          {detail.annotations.length > 0 ? (
            <div className="ph-dp-local-files">
              {detail.annotations.map((annotation) => (
                <div className="ph-dp-local-file" key={annotation.key}>
                  <span className="ph-dp-file-ic"><Icons.spark /></span>
                  <span className="ph-dp-local-file-main">
                    <span className="ph-dp-file-name">
                      {annotation.title || "Zotero PDF 标注"}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="ph-dp-muted">暂无 PDF 标注</div>
          )}
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
  const mirrorRef = parseZoteroItemId(id);
  const isLegacyLocal = isLocalZoteroPaperId(id);
  const isLocal = isLegacyLocal || mirrorRef !== null;

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
    enabled: id !== "" && isLegacyLocal,
  });
  const mirrorDetailQuery = useQuery({
    queryKey: ["zotero-mirror", "item", mirrorRef?.sourceId, mirrorRef?.libraryId, mirrorRef?.itemKey],
    queryFn: () => zotero.item(mirrorRef!),
    enabled: mirrorRef !== null,
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
    if (mirrorRef !== null) {
      if (mirrorDetailQuery.data) return <MirrorZoteroDetail detail={mirrorDetailQuery.data} />;
      return (
        <aside className="ph-dp ph-scroll">
          <div className="ph-dp-empty">
            {mirrorDetailQuery.isError ? "无法读取 Zotero 镜像条目" : "正在读取 Zotero 条目…"}
          </div>
        </aside>
      );
    }
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

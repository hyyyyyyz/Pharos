import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Job, Paper, ReaderKind } from "../api/types";
import { useUI } from "../store";
import { PdfViewer } from "./PdfViewer";

const STAGE_LABEL: Record<string, string> = {
  queued: "待译",
  parsing: "解析版面",
  translating: "翻译中",
  typesetting: "重排版面",
};

function isActive(job: Job | null | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

const KINDS: { key: ReaderKind; label: string; needsTranslation: boolean }[] = [
  { key: "mono", label: "中文", needsTranslation: true },
  { key: "dual", label: "中英对照", needsTranslation: true },
  { key: "original", label: "原文", needsTranslation: false },
];

export function Reader() {
  const qc = useQueryClient();
  const selectedId = useUI((s) => s.selectedPaperId);
  const readerKind = useUI((s) => s.readerKind);
  const setReaderKind = useUI((s) => s.setReaderKind);
  const chatOpen = useUI((s) => s.chatOpen);
  const toggleChat = useUI((s) => s.toggleChat);

  const paperQuery = useQuery({
    queryKey: ["paper", selectedId],
    queryFn: () => api.getPaper(selectedId as string),
    enabled: !!selectedId,
    refetchInterval: (q) => (isActive((q.state.data as Paper | undefined)?.latest_job) ? 1500 : false),
  });
  const paper = paperQuery.data;
  const job = paper?.latest_job ?? null;
  const running = isActive(job);
  const translated = Boolean(job?.status === "done" && job.has_mono);

  // Refresh the sidebar once when a translation finishes (transition-guarded → no loop).
  const prev = useRef<string | undefined>(undefined);
  useEffect(() => {
    const s = job?.status;
    if (s && s !== prev.current) {
      prev.current = s;
      if (s === "done" || s === "error") qc.invalidateQueries({ queryKey: ["papers"] });
    }
  }, [job?.status, qc]);

  const translate = useMutation({
    mutationFn: () => api.translate(selectedId as string),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["paper", selectedId] });
      qc.invalidateQueries({ queryKey: ["papers"] });
    },
  });

  if (!selectedId) {
    return (
      <section className="reader reader-empty">
        <span className="xz-seal" style={{ fontSize: "1.4rem" }}>阅</span>
        <p className="xz-muted">自左侧择一卷，于此展读。</p>
      </section>
    );
  }
  if (!paper) {
    return <section className="reader reader-empty"><p className="xz-faint">展开卷帙…</p></section>;
  }

  const effectiveKind: ReaderKind = translated ? readerKind : "original";
  const showViewer = translated || readerKind === "original" || (!running && !translated);

  return (
    <section className="reader">
      <header className="reader-bar">
        <div className="reader-title">
          <h2 className="reader-h">{paper.title}</h2>
          <span className="reader-meta xz-faint">{paper.page_count ?? "?"} 页 · {paper.orig_filename}</span>
        </div>
        <div className="reader-tools">
          <div className="seg">
            {KINDS.map((k) => (
              <button
                key={k.key}
                className={`seg-btn${effectiveKind === k.key ? " is-on" : ""}`}
                disabled={k.needsTranslation && !translated}
                onClick={() => setReaderKind(k.key)}
                title={k.needsTranslation && !translated ? "尚未译出" : k.label}
              >
                {k.label}
              </button>
            ))}
          </div>
          <button className="icon-btn" onClick={toggleChat} title="问玄奘" aria-label="问玄奘">
            {chatOpen ? "▷" : "◁"} 领航
          </button>
        </div>
      </header>

      {!translated && !running && (
        <div className="translate-cta">
          <button
            className="xz-btn xz-btn--primary"
            onClick={() => translate.mutate()}
            disabled={translate.isPending}
          >
            {translate.isPending ? "启程…" : "翻译此篇 · 保留排版"}
          </button>
          {job?.status === "error" && <span className="paper-error">上次翻译失败：{job.error?.slice(0, 100)}</span>}
          {translate.isError && <span className="paper-error">{(translate.error as Error).message}</span>}
        </div>
      )}

      {running && (
        <div className="translate-progress">
          <div className="progress-track big">
            <div className="progress-fill" style={{ width: `${Math.max(job?.progress ?? 0, 4)}%` }} />
          </div>
          <p className="progress-label">
            {STAGE_LABEL[job?.stage ?? ""] ?? "译经中"} · {(job?.progress ?? 0).toFixed(0)}%
          </p>
        </div>
      )}

      <div className="reader-viewer">
        {showViewer ? (
          <PdfViewer key={`${paper.id}-${effectiveKind}`} url={api.pdfUrl(paper.id, effectiveKind)} />
        ) : (
          <div className="reader-empty">
            <p className="xz-faint">翻译完成后，中文将在此展读。</p>
          </div>
        )}
      </div>
    </section>
  );
}

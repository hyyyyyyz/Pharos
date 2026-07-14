import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Paper } from "../api/types";
import { UploadZone } from "./UploadZone";
import { PaperCard } from "./PaperCard";

export function Library() {
  const qc = useQueryClient();

  const papersQuery = useQuery({ queryKey: ["papers"], queryFn: api.listPapers });

  const upload = useMutation({
    mutationFn: (file: File) => api.upload(file),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["papers"] }),
  });

  const papers = papersQuery.data ?? [];

  return (
    <div className="library">
      <UploadZone onUpload={(f) => upload.mutate(f)} busy={upload.isPending} />
      {upload.isError && <p className="paper-error center">入场失败：{(upload.error as Error).message}</p>}

      <div className="xz-rule">
        <span>译 场</span>
      </div>

      {papersQuery.isLoading ? (
        <p className="lib-empty xz-faint">正在展开译场…</p>
      ) : papersQuery.isError ? (
        <div className="lib-empty">
          <p className="paper-error">连不上译场后端：{(papersQuery.error as Error).message}</p>
          <p className="xz-faint">请确认 ROG2 后端与 SSH 隧道在运行。</p>
        </div>
      ) : papers.length === 0 ? (
        <div className="lib-empty">
          <span className="xz-seal empty-seal">空</span>
          <p className="xz-muted">译场尚空——拖入第一篇论文，即启西行。</p>
        </div>
      ) : (
        <div className="grid">
          {papers.map((p: Paper) => (
            <PaperCard key={p.id} paper={p} />
          ))}
        </div>
      )}
    </div>
  );
}

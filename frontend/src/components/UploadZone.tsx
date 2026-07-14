import { useRef, useState } from "react";

interface Props {
  onUpload: (file: File) => void;
  busy?: boolean;
}

/** Drag-and-drop (or click) a PDF to bring it into the 译场. */
export function UploadZone({ onUpload, busy }: Props) {
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const take = (files: FileList | null) => {
    const f = files?.[0];
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      setError("只支持 PDF 文件");
      return;
    }
    setError(null);
    onUpload(f);
  };

  return (
    <div
      className={`upload-zone${over ? " is-over" : ""}${busy ? " is-busy" : ""}`}
      role="button"
      tabIndex={0}
      aria-label="上传 PDF 论文"
      onClick={() => !busy && inputRef.current?.click()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        take(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        hidden
        onChange={(e) => take(e.target.files)}
      />
      <span className="xz-seal upload-seal">译</span>
      <p className="upload-title">{busy ? "正在迎入译场…" : "拖入英文论文，或点此择卷"}</p>
      <p className="upload-hint xz-faint">PDF · 完全保留排版，译成中文</p>
      {error && <p className="paper-error">{error}</p>}
    </div>
  );
}

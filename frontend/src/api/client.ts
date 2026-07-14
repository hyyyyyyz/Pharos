import type { Job, Paper, PdfKind } from "./types";

// Dev: "/api" is proxied by Vite to the backend (127.0.0.1:8848 → SSH tunnel → ROG2).
// Prod (github.io): set VITE_API_BASE to the backend's public URL.
const BASE = import.meta.env.VITE_API_BASE ?? "/api";

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: (): Promise<{ status: string; engine: string; translator: string }> =>
    fetch(`${BASE}/health`).then(unwrap),

  listPapers: (): Promise<Paper[]> => fetch(`${BASE}/papers`).then(unwrap),

  getPaper: (id: string): Promise<Paper> => fetch(`${BASE}/papers/${id}`).then(unwrap),

  upload: (file: File): Promise<Paper> => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/papers`, { method: "POST", body: form }).then(unwrap);
  },

  translate: (paperId: string, pages?: string): Promise<Job> => {
    const qs = pages ? `?pages=${encodeURIComponent(pages)}` : "";
    return fetch(`${BASE}/papers/${paperId}/translate${qs}`, { method: "POST" }).then(unwrap);
  },

  getJob: (jobId: string): Promise<Job> => fetch(`${BASE}/jobs/${jobId}`).then(unwrap),

  pdfUrl: (paperId: string, kind: PdfKind): string => `${BASE}/papers/${paperId}/pdf/${kind}`,
};

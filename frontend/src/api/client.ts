import type { ChatMessage, Job, Paper, PdfKind } from "./types";

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
    fetch(`${BASE}/health`).then(unwrap<{ status: string; engine: string; translator: string }>),

  listPapers: (): Promise<Paper[]> => fetch(`${BASE}/papers`).then(unwrap<Paper[]>),

  getPaper: (id: string): Promise<Paper> => fetch(`${BASE}/papers/${id}`).then(unwrap<Paper>),

  upload: (file: File): Promise<Paper> => {
    const form = new FormData();
    form.append("file", file);
    return fetch(`${BASE}/papers`, { method: "POST", body: form }).then(unwrap<Paper>);
  },

  translate: (paperId: string, pages?: string): Promise<Job> => {
    const qs = pages ? `?pages=${encodeURIComponent(pages)}` : "";
    return fetch(`${BASE}/papers/${paperId}/translate${qs}`, { method: "POST" }).then(unwrap<Job>);
  },

  getJob: (jobId: string): Promise<Job> => fetch(`${BASE}/jobs/${jobId}`).then(unwrap<Job>),

  pdfUrl: (paperId: string, kind: PdfKind): string => `${BASE}/papers/${paperId}/pdf/${kind}`,

  /** Ask a question about a paper; streams the answer token-by-token. */
  chatStream: async (
    paperId: string,
    messages: ChatMessage[],
    onToken: (t: string) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const res = await fetch(`${BASE}/papers/${paperId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
      signal,
    });
    if (!res.ok || !res.body) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      onToken(decoder.decode(value, { stream: true }));
    }
  },
};

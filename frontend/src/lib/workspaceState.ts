/**
 * Which module is open and which papers are open as tabs, kept across a reload.
 *
 * localStorage rather than the URL hash: this app has no router, the tab list
 * can be long, and a hash would leak paper ids into history, referrers and
 * shared links. It is also per-browser-profile, which matches how people reload
 * a PWA. It is cleared the moment the session ends (sign-out or a 401) so the
 * next account never inherits the previous account's open papers.
 */

export const WORKSPACE_KEY = "ph-workspace-v1";

const MODULES = ["library", "daily", "search", "kb", "runs", "admin"] as const;
export type PersistedModule = (typeof MODULES)[number];

export interface PersistedTab {
  id: string;
  kind: "library" | "paper";
  paperId?: string;
}

export interface WorkspaceSnapshot {
  activeModule: PersistedModule;
  tabs: PersistedTab[];
  activeTabId: string;
}

export const DEFAULT_WORKSPACE: WorkspaceSnapshot = {
  activeModule: "library",
  tabs: [{ id: "library", kind: "library" }],
  activeTabId: "library",
};

/** Parse and sanity-check a stored value. Anything off → the default workspace. */
export function parseWorkspace(raw: string | null): WorkspaceSnapshot {
  if (raw === null) return DEFAULT_WORKSPACE;
  try {
    const v = JSON.parse(raw) as Partial<WorkspaceSnapshot> | null;
    if (v === null || typeof v !== "object") return DEFAULT_WORKSPACE;
    const activeModule = MODULES.find((m) => m === v.activeModule) ?? "library";
    const tabs: PersistedTab[] = [{ id: "library", kind: "library" }];
    const seen = new Set<string>(["library"]);
    for (const t of Array.isArray(v.tabs) ? v.tabs : []) {
      if (
        t !== null &&
        typeof t === "object" &&
        t.kind === "paper" &&
        typeof t.paperId === "string" &&
        t.paperId !== "" &&
        t.id === `t-${t.paperId}` &&
        !seen.has(t.id)
      ) {
        seen.add(t.id);
        tabs.push({ id: t.id, kind: "paper", paperId: t.paperId });
      }
    }
    const activeTabId = typeof v.activeTabId === "string" && seen.has(v.activeTabId) ? v.activeTabId : "library";
    return { activeModule, tabs, activeTabId };
  } catch {
    return DEFAULT_WORKSPACE;
  }
}

export function loadWorkspace(): WorkspaceSnapshot {
  try {
    return parseWorkspace(typeof localStorage === "undefined" ? null : localStorage.getItem(WORKSPACE_KEY));
  } catch {
    return DEFAULT_WORKSPACE;
  }
}

export function saveWorkspace(s: WorkspaceSnapshot): void {
  try {
    localStorage.setItem(
      WORKSPACE_KEY,
      JSON.stringify({ activeModule: s.activeModule, tabs: s.tabs, activeTabId: s.activeTabId }),
    );
  } catch {
    /* private mode / quota — the workspace just won't survive a reload */
  }
}

export function clearWorkspace(): void {
  try {
    localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    /* nothing to clear */
  }
}

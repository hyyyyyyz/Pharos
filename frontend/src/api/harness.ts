/** Typed client for the Research Harness API. */

import { ApiError, authHeaders } from "./client";

const BASE = "/api";

async function json<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export type HarnessRunState =
  | "queued"
  | "running"
  | "waiting_for_approval"
  | "waiting_for_input"
  | "paused"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "indeterminate";

export interface HarnessWorkflow {
  workflowKey: string;
  version: number;
  inputSchema: string;
  outputSchema: string;
  activationState: "active" | "deprecated" | "disabled";
  executionMode: "legacy" | "shadow" | "harness" | null;
}

export interface HarnessRun {
  id: string;
  workflowKey: string;
  workflowVersion: number;
  state: HarnessRunState;
  outcome: "complete" | "partial" | "incomplete" | null;
  initiator: "user" | "schedule" | "operator" | "child_run";
  projectId: string | null;
  createdAtUs: number;
  startedAtUs: number | null;
  finishedAtUs: number | null;
  errorCode: string | null;
  errorMessage: string | null;
  usage: Record<string, unknown>;
}

export interface HarnessStep {
  id: string;
  definitionStepKey: string;
  instanceKey: string;
  stepKind: "deterministic" | "agent" | "mapped" | "mapped_agent";
  state: string;
  attemptCount: number;
  errorCode: string | null;
  errorMessage: string | null;
  skipReason: string | null;
  waitingReason: string | null;
}

export interface HarnessRunDetail extends HarnessRun {
  steps: HarnessStep[];
}

export interface HarnessArtifact {
  id: string;
  artifactType: string;
  schemaName: string;
  schemaVersion: number;
  sensitivity: string;
  producerKind: string;
  qualityStatus: string | null;
  evidenceLevel: string | null;
  contentSha256: string;
  content: unknown;
  deleted: boolean;
  deletionReason: string | null;
}

export interface HarnessApproval {
  id: string;
  runId: string;
  action: string;
  resource: Record<string, unknown>;
  effectSummary: Record<string, unknown>;
  state: "pending" | "approved" | "rejected" | "expired" | "cancelled";
  expiresAtUs: number;
  resolvedAtUs: number | null;
  resolverReason: string | null;
}

export interface HarnessEvent {
  seq: number;
  event_type: string;
  step_id: string | null;
  attempt_id: string | null;
  payload: Record<string, unknown>;
  created_at_us: number;
}

export const harnessApi = {
  workflows: (): Promise<HarnessWorkflow[]> => json<HarnessWorkflow[]>("/harness/workflows"),

  listRuns: (limit = 100): Promise<{ runs: HarnessRun[]; nextCursor: number | null }> =>
    json<{ runs: HarnessRun[]; nextCursor: number | null }>(`/harness/runs?limit=${limit}`),

  getRun: (runId: string): Promise<HarnessRunDetail> =>
    json<HarnessRunDetail>(`/harness/runs/${runId}`),

  pause: (runId: string): Promise<HarnessRun> =>
    json<HarnessRun>(`/harness/runs/${runId}/pause`, { method: "POST" }),

  resume: (runId: string): Promise<HarnessRun> =>
    json<HarnessRun>(`/harness/runs/${runId}/resume`, { method: "POST" }),

  cancel: (runId: string): Promise<HarnessRun> =>
    json<HarnessRun>(`/harness/runs/${runId}/cancel`, { method: "POST" }),

  events: (runId: string, afterSeq = 0): Promise<{ events: HarnessEvent[]; nextSeq: number }> =>
    json<{ events: HarnessEvent[]; nextSeq: number }>(
      `/harness/runs/${runId}/events?after_seq=${afterSeq}&limit=200`,
    ),

  artifacts: (runId: string): Promise<HarnessArtifact[]> =>
    json<HarnessArtifact[]>(`/harness/runs/${runId}/artifacts`),

  approvals: (runId: string): Promise<HarnessApproval[]> =>
    json<HarnessApproval[]>(`/harness/runs/${runId}/approvals`),

  decideApproval: (
    approvalId: string,
    decision: "approved" | "rejected",
    reason: string,
  ): Promise<HarnessApproval> =>
    json<HarnessApproval>(`/harness/approvals/${approvalId}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, reason }),
    }),
};

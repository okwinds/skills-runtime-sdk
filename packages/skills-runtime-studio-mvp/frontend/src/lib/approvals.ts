/**
 * Approvals API Client - Skills Runtime Studio
 *
 * Handles approval_requested / approval_decided event flow:
 * - listPendingApprovals: GET pending approvals for a run
 * - decideApproval: POST decision (approved/denied/etc.) for an approval
 */

import { APIError } from './api';

// ===== TYPES =====

export type ApprovalDecision = 'approved' | 'approved_for_session' | 'denied' | 'abort';

export interface PendingApproval {
  approval_key: string;
  // Legacy fields (frontend may still send these)
  title?: string;
  prompt?: string;
  requested_at?: string;
  metadata?: {
    origin?: string;
    url?: string;
    [key: string]: unknown;
  };
  // Extended fields (backend may add)
  tool?: string;
  summary?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  request?: Record<string, any>;
  age_ms?: number;
  run_id?: string;
}

export interface PendingApprovalsResponse {
  run_id?: string;
  /** @deprecated Use `approvals` instead */
  pending?: PendingApproval[];
  approvals?: PendingApproval[];
  [key: string]: unknown;
}

export interface DecideApprovalResponse {
  ok: boolean;
  [key: string]: unknown;
}

// ===== HELPERS =====

async function readErrorBody(res: Response): Promise<unknown> {
  const contentType = res.headers.get('content-type') ?? '';
  try {
    if (contentType.includes('application/json')) return await res.json();
    return await res.text();
  } catch {
    return undefined;
  }
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    const details = await readErrorBody(res);
    throw new APIError(res.status, `Request failed: ${res.status} ${res.statusText}`, details);
  }

  return (await res.json()) as T;
}

// ===== API FUNCTIONS =====

/**
 * List pending approvals for a run.
 * Use this on page load/refresh to check if there's any pending approval that needs user action.
 *
 * Note: This function is compatible with both legacy format (response.pending)
 * and new backend format (response.approvals). The response will have both
 * fields populated for maximum compatibility.
 */
export async function listPendingApprovals(runId: string): Promise<PendingApprovalsResponse> {
  const raw = await fetchJson<PendingApprovalsResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/approvals/pending`,
    { method: 'GET' }
  );

  // Normalize response to support both legacy (pending) and new backend (approvals) formats
  const approvals = raw.approvals ?? raw.pending ?? [];
  const pending = raw.pending ?? raw.approvals ?? [];
  const normalizedRunId = typeof raw.run_id === 'string' && raw.run_id ? raw.run_id : runId;

  return {
    ...raw,
    run_id: normalizedRunId,
    approvals,
    pending,
  };
}

/**
 * Submit a decision for an approval.
 * @param runId - The run ID
 * @param approvalKey - The approval key from approval_requested event
 * @param decision - One of: approved, approved_for_session, denied, abort
 */
export async function decideApproval(
  runId: string,
  approvalKey: string,
  decision: ApprovalDecision
): Promise<DecideApprovalResponse> {
  return fetchJson<DecideApprovalResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/approvals/${encodeURIComponent(approvalKey)}`,
    {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }
  );
}

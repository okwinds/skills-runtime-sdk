/**
 * Approvals API Client - Skills Runtime Studio
 *
 * Handles approval_requested / approval_decided event flow:
 * - listPendingApprovals: GET pending approvals for a run
 * - decideApproval: POST decision (approved/denied/etc.) for an approval
 */

import { fetchJson } from './http';

// ===== TYPES =====

export type ApprovalDecision = 'approved' | 'approved_for_session' | 'denied' | 'abort';

export interface PendingApproval {
  approval_key: string;
  tool?: string;
  summary?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  request?: Record<string, any>;
  age_ms?: number;
  run_id?: string;
}

export interface PendingApprovalsResponse {
  run_id?: string;
  approvals?: PendingApproval[];
}

export interface DecideApprovalResponse {
  ok: boolean;
  [key: string]: unknown;
}

// ===== API FUNCTIONS =====

/**
 * List pending approvals for a run.
 * Use this on page load/refresh to check if there's any pending approval that needs user action.
 */
export async function listPendingApprovals(runId: string): Promise<PendingApprovalsResponse> {
  const raw = await fetchJson<PendingApprovalsResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/approvals/pending`,
    { method: 'GET' }
  );

  const approvals = raw.approvals ?? [];
  const normalizedRunId = typeof raw.run_id === 'string' && raw.run_id ? raw.run_id : runId;

  return {
    run_id: normalizedRunId,
    approvals,
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

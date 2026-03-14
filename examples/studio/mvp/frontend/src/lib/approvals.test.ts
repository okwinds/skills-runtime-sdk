import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import {
  decideApproval,
  listPendingApprovals,
  type PendingApprovalsResponse,
} from './approvals';

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
    ...init,
  });
}

describe('approvals.ts API client', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe('decideApproval()', () => {
    it('sends POST with decision=approved and returns ok', async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse({ ok: true }, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await decideApproval('run_123', 'approval_key_abc', 'approved');

      expect(result).toEqual({ ok: true });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/runs/run_123/approvals/approval_key_abc',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({ 'content-type': 'application/json' }),
          body: JSON.stringify({ decision: 'approved' }),
        })
      );
    });

    it('sends POST with decision=approved_for_session', async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse({ ok: true }, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      await decideApproval('run_123', 'key1', 'approved_for_session');

      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body).toEqual({ decision: 'approved_for_session' });
    });

    it('sends POST with decision=denied', async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse({ ok: true }, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      await decideApproval('run_123', 'key1', 'denied');

      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body).toEqual({ decision: 'denied' });
    });

    it('sends POST with decision=abort', async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse({ ok: true }, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      await decideApproval('run_123', 'key1', 'abort');

      const body = JSON.parse(fetchMock.mock.calls[0][1].body);
      expect(body).toEqual({ decision: 'abort' });
    });

    it('throws APIError on 404 (approval not found)', async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: 'Approval not found' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        })
      );
      vi.stubGlobal('fetch', fetchMock);

      await expect(
        decideApproval('run_123', 'non_existent_key', 'approved')
      ).rejects.toThrow('Request failed: 404');
    });

    it('throws APIError on 400 (invalid decision)', async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: 'Invalid decision' }), {
          status: 400,
          headers: { 'content-type': 'application/json' },
        })
      );
      vi.stubGlobal('fetch', fetchMock);

      // Use type assertion to test invalid decision handling
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const invalidDecision: any = 'invalid_decision';
      await expect(
        decideApproval('run_123', 'key1', invalidDecision)
      ).rejects.toThrow('Request failed: 400');
    });
  });

  describe('listPendingApprovals()', () => {
    it('returns approvals array with correct structure', async () => {
      const mockResponse: PendingApprovalsResponse = {
        run_id: 'run_123',
        approvals: [
          {
            run_id: 'run_123',
            approval_key: 'tool.web.approval_1',
            tool: 'web_open',
            summary: 'Open https://example.com',
            request: { url: 'https://example.com' },
            age_ms: 123,
          },
        ],
      };

      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(mockResponse, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await listPendingApprovals('run_123');

      expect(result.run_id).toBe('run_123');
      expect(result.approvals).toHaveLength(1);
      expect(result.approvals![0].approval_key).toBe('tool.web.approval_1');
      expect(result.approvals![0].tool).toBe('web_open');
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/runs/run_123/approvals/pending',
        expect.objectContaining({ method: 'GET' })
      );
    });

    it('returns empty approvals array when no approvals pending', async () => {
      const mockResponse: PendingApprovalsResponse = {
        run_id: 'run_456',
        approvals: [],
      };

      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(mockResponse, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await listPendingApprovals('run_456');

      expect(result.run_id).toBe('run_456');
      expect(result.approvals).toHaveLength(0);
    });

    it('throws APIError on 404 when run not found', async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: 'Run not found' }), {
          status: 404,
          headers: { 'content-type': 'application/json' },
        })
      );
      vi.stubGlobal('fetch', fetchMock);

      await expect(listPendingApprovals('non_existent_run')).rejects.toThrow(
        'Request failed: 404'
      );
    });

    it('handles response with extended fields (backend may add more fields)', async () => {
      const mockResponse = {
        run_id: 'run_789',
        approvals: [
          {
            run_id: 'run_789',
            approval_key: 'tool.shell.approval_2',
            // Extended fields that backend may add
            tool: 'shell_exec',
            summary: 'Run rm -rf /tmp/test',
            request: { argv: ['rm', '-rf', '/tmp/test'] },
            age_ms: 1234,
          },
        ],
        // Backend may add extra top-level fields
        extra_field: 'some_value',
      };

      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(mockResponse, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await listPendingApprovals('run_789');

      expect(result.run_id).toBe('run_789');
      expect(result.approvals).toHaveLength(1);
      expect(result.approvals![0].approval_key).toBe('tool.shell.approval_2');
      // Extended fields should be accessible
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      expect((result.approvals![0] as any).tool).toBe('shell_exec');
      // Top-level extended fields are not guaranteed to be preserved by the client
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      expect((result as any).extra_field).toBeUndefined();
    });

    it('falls back to input runId when backend run_id is missing', async () => {
      const mockResponse = {
        approvals: [
          {
            run_id: 'run_abc',
            approval_key: 'tool.web.approval_1',
            tool: 'web_search',
            summary: 'Search for product info',
            request: { query: 'best laptops 2026' },
            age_ms: 500,
          },
        ],
      };

      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(mockResponse, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await listPendingApprovals('run_abc');

      expect(result.run_id).toBe('run_abc');
      expect(result.approvals).toHaveLength(1);
      expect(result.approvals![0].approval_key).toBe('tool.web.approval_1');
      expect(result.approvals![0].tool).toBe('web_search');
    });

    it('handles empty approvals array from backend', async () => {
      const mockResponse = {
        run_id: 'run_def',
        approvals: [],
      };

      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(mockResponse, { status: 200 })
      );
      vi.stubGlobal('fetch', fetchMock);

      const result = await listPendingApprovals('run_def');

      expect(result.run_id).toBe('run_def');
      expect(result.approvals).toHaveLength(0);
    });
  });
});

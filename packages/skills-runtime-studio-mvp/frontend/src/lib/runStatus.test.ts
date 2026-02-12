import { describe, expect, it } from 'vitest';
import { deriveStatusItem } from './runStatus';

describe('deriveStatusItem', () => {
  it('derives thinking status from llm_request_started', () => {
    const it0 = deriveStatusItem({
      event: 'llm_request_started',
      data: {
        type: 'llm_request_started',
        timestamp: '2026-02-10T10:00:00Z',
        payload: { model: 'ark-code-latest' },
      },
      raw: '{}',
    });
    expect(it0?.message).toContain('模型请求中');
    expect(it0?.force_open).toBe(true);
  });

  it('derives tool request status from tool_call_requested', () => {
    const it0 = deriveStatusItem({
      event: 'tool_call_requested',
      data: {
        type: 'tool_call_requested',
        timestamp: '2026-02-10T10:00:00Z',
        payload: { name: 'shell_exec' },
      },
      raw: '{}',
    });
    expect(it0?.message).toBe('请求工具：shell_exec');
    expect(it0?.force_open).toBe(true);
  });

  it('derives approval status from approval_requested', () => {
    const it0 = deriveStatusItem({
      event: 'approval_requested',
      data: {
        type: 'approval_requested',
        timestamp: '2026-02-10T10:00:00Z',
        payload: { tool: 'shell_exec' },
      },
      raw: '{}',
    });
    expect(it0?.message).toBe('等待审批：shell_exec');
    expect(it0?.force_open).toBe(true);
  });
});


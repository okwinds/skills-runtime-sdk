import { describe, expect, it } from 'vitest';
import type { StreamRunEvent } from './api';
import { extractApprovalEvents, extractConfigSummary, extractToolSandboxEntries, summarizeSandbox } from './runInfo';

describe('runInfo', () => {
  it('extracts config_summary from run_started', () => {
    const events: StreamRunEvent[] = [
      { event: 'run_started', data: { payload: { config_summary: { models: { planner: 'p' }, llm: { base_url: 'http://x' } } } }, raw: '' },
    ];
    const cs = extractConfigSummary(events);
    expect(cs?.models?.planner).toBe('p');
    expect(cs?.llm?.base_url).toBe('http://x');
  });

  it('extracts approval events', () => {
    const events: StreamRunEvent[] = [
      { event: 'approval_requested', data: { timestamp: '2026-01-01T00:00:00Z', payload: { approval_key: 'k1', tool: 'shell_exec', summary: 'need approval' } }, raw: '' },
      { event: 'approval_decided', data: { timestamp: '2026-01-01T00:00:01Z', payload: { approval_key: 'k1', decision: 'approved' } }, raw: '' },
    ];
    const items = extractApprovalEvents(events);
    expect(items).toHaveLength(2);
    expect(items[0].approval_key).toBe('k1');
    expect(items[1].decision).toBe('approved');
  });

  it('extracts tool sandbox entries and summary', () => {
    const events: StreamRunEvent[] = [
      {
        event: 'tool_call_finished',
        data: {
          timestamp: '2026-01-01T00:00:00Z',
          payload: {
            tool: 'shell_exec',
            result: { ok: false, exit_code: 1, error_kind: 'exit_code', data: { sandbox: { active: true, effective: 'restricted', adapter: 'SeatbeltSandboxAdapter' } } },
          },
        },
        raw: '',
      },
    ];
    const entries = extractToolSandboxEntries(events);
    expect(entries).toHaveLength(1);
    expect(entries[0].sandbox.active).toBe(true);
    const summary = summarizeSandbox(entries);
    expect(summary.active).toBe(true);
    expect(summary.failed_tools).toBe(1);
  });
});


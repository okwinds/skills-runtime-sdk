import { describe, expect, it } from 'vitest';
import { extractRunOutputText } from './runOutput';

describe('extractRunOutputText', () => {
  it('renders run_failed payload message', () => {
    const text = extractRunOutputText({
      type: 'run_failed',
      payload: { error_kind: 'config_error', message: '缺少 API key 环境变量：OPENAI_API_KEY' },
    });
    expect(text?.value).toContain('[run_failed] config_error');
    expect(text?.value).toContain('OPENAI_API_KEY');
  });

  it('renders run_failed details.exception_class when present', () => {
    const text = extractRunOutputText({
      type: 'run_failed',
      payload: { error_kind: 'unknown', message: 'boom', details: { exception_class: 'RuntimeError' } },
    });
    expect(text?.value).toContain('exception_class=RuntimeError');
  });

  it('renders run_completed final_output', () => {
    const text = extractRunOutputText({
      type: 'run_completed',
      payload: { final_output: 'hello world' },
    });
    expect(text).toEqual({ kind: 'content', value: 'hello world' });
  });

  it('returns null for unrelated objects', () => {
    expect(extractRunOutputText({ type: 'tool_call_started', payload: {} })).toBeNull();
  });
});

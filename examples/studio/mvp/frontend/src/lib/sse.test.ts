import { describe, expect, it } from 'vitest';
import { parseSseText } from './sse';

describe('parseSseText', () => {
  it('parses multiple SSE messages with id/event/data', () => {
    const text = [
      'id: 1',
      'event: message.delta',
      'data: {"type":"message.delta","delta":"Hel"}',
      '',
      'event: message.delta',
      'data: {"type":"message.delta","delta":"lo"}',
      '',
    ].join('\n');

    expect(parseSseText(text)).toEqual([
      { id: '1', event: 'message.delta', data: '{"type":"message.delta","delta":"Hel"}' },
      { event: 'message.delta', data: '{"type":"message.delta","delta":"lo"}' },
    ]);
  });

  it('joins multi-line data fields with newlines', () => {
    const text = ['event: message.done', 'data: line1', 'data: line2', '', ''].join('\n');

    expect(parseSseText(text)).toEqual([{ event: 'message.done', data: 'line1\nline2' }]);
  });

  it('ignores comments and unknown fields', () => {
    const text = [': keep-alive', 'event: run.succeeded', 'data: {}', '', ''].join('\n');

    expect(parseSseText(text)).toEqual([{ event: 'run.succeeded', data: '{}' }]);
  });

  it('supports CRLF newlines', () => {
    const text = 'event: ping\r\ndata: {}\r\n\r\n';
    expect(parseSseText(text)).toEqual([{ event: 'ping', data: '{}' }]);
  });
});


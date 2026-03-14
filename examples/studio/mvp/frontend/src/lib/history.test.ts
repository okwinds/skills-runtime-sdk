import { beforeEach, describe, expect, it } from 'vitest';
import { appendHistoryEntry, loadHistoryForSession, type HistoryEntry } from './history';

describe('history', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('appends and persists history entries (newest first)', () => {
    const sessionId = 'sess_test';
    const next = appendHistoryEntry([], {
      run_id: 'run_1',
      prompt: 'hello',
      final_text: 'world',
      status: 'success',
      created_at: '2026-02-10T10:00:00Z',
    }, sessionId);

    expect(next).toHaveLength(1);
    expect(next[0].run_id).toBe('run_1');

    const loaded = loadHistoryForSession(sessionId);
    expect(loaded).toHaveLength(1);
    expect(loaded[0].run_id).toBe('run_1');
  });

  it('enforces a limit', () => {
    const sessionId = 'sess_test';
    let cur: HistoryEntry[] = [];
    for (let i = 0; i < 5; i += 1) {
      cur = appendHistoryEntry(cur, {
        run_id: `run_${i}`,
        prompt: `p${i}`,
        final_text: `o${i}`,
        status: 'success',
        created_at: '2026-02-10T10:00:00Z',
      }, sessionId, 3);
    }
    expect(cur).toHaveLength(3);
    expect(cur[0].run_id).toBe('run_4');
    expect(cur[2].run_id).toBe('run_2');
  });
});

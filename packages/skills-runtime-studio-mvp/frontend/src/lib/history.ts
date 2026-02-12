export type HistoryStatus = 'success' | 'error' | 'cancelled' | 'unknown';

export type HistoryEntry = {
  id: string;
  run_id: string;
  prompt: string;
  final_text: string;
  status: HistoryStatus;
  created_at: string; // ISO
};

export type NewHistoryEntry = Omit<HistoryEntry, 'id'>;

function historyStorageKey(sessionId: string): string {
  return `skills_runtime_studio.history.v1.${sessionId}`;
}

function generateId(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cryptoAny: any = globalThis.crypto;
  if (cryptoAny && typeof cryptoAny.randomUUID === 'function') {
    return cryptoAny.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function loadHistoryForSession(sessionId: string): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(historyStorageKey(sessionId));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((it): it is HistoryEntry => typeof it === 'object' && it !== null)
      .map((it) => it as HistoryEntry)
      .filter((it) => typeof it.id === 'string' && typeof it.run_id === 'string');
  } catch {
    return [];
  }
}

export function appendHistoryEntry(
  prev: HistoryEntry[],
  entry: NewHistoryEntry,
  sessionId: string,
  limit: number = 30,
): HistoryEntry[] {
  const next: HistoryEntry[] = [{ ...entry, id: generateId() }, ...prev].slice(0, limit);
  try {
    localStorage.setItem(historyStorageKey(sessionId), JSON.stringify(next));
  } catch {
    // ignore quota / disabled storage
  }
  return next;
}


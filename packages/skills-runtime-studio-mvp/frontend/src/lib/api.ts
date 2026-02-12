/**
 * API Client - Skills Runtime Studio
 *
 * This file intentionally uses `fetch` directly (no axios) and parses SSE streams
 * using `ReadableStream` so it works with custom `event:` fields.
 */

import { parseSseText, type ParsedSseEvent } from './sse';

// ===== TYPES (exported for UI) =====

export interface SkillManifestDependencies {
  env_vars: string[];
}

export interface SkillManifest {
  name: string;
  description: string;
  path: string;
  enabled: boolean;
  dependencies: SkillManifestDependencies;
}

export interface Session {
  id: string;
  createdAt?: string;
  updatedAt?: string;
  title?: string;
  runsCount?: number;
}

export interface Run {
  id: string;
}

export type SSEEventType = string;

export interface SSEEvent {
  event: SSEEventType;
  data: unknown;
}

export class APIError extends Error {
  status: number;
  details?: unknown;

  constructor(status: number, message: string, details?: unknown) {
    super(message);
    this.name = 'APIError';
    this.status = status;
    this.details = details;
  }
}

// ===== LOW-LEVEL HELPERS =====

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

async function fetchNoContent(path: string, init?: RequestInit): Promise<void> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const details = await readErrorBody(res);
    throw new APIError(res.status, `Request failed: ${res.status} ${res.statusText}`, details);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((v) => String(v));
}

function mapSkill(raw: unknown): SkillManifest {
  if (isRecord(raw)) {
    const obj = raw;
    const dependencies = isRecord(obj.dependencies) ? obj.dependencies : null;
    return {
      name: String(obj.name ?? ''),
      description: String(obj.description ?? ''),
      path: String(obj.path ?? ''),
      enabled: Boolean(obj.enabled ?? true),
      dependencies: {
        env_vars: dependencies ? readStringArray(dependencies.env_vars) : [],
      },
    };
  }

  return { name: '', description: '', path: '', enabled: false, dependencies: { env_vars: [] } };
}

function mapSession(raw: { session_id: string; created_at: string }): Session {
  return { id: raw.session_id, createdAt: raw.created_at };
}

function readOptionalString(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  return value;
}

function readOptionalNumber(value: unknown): number | undefined {
  if (typeof value !== 'number' || Number.isNaN(value)) return undefined;
  return value;
}

function mapListedSession(raw: unknown): Session | null {
  if (!isRecord(raw)) return null;
  const sessionId = readOptionalString(raw.session_id);
  if (!sessionId) return null;

  const title = readOptionalString(raw.title);
  const updatedAt = readOptionalString(raw.updated_at);
  const runsCount = readOptionalNumber(raw.runs_count);

  return {
    id: sessionId,
    title,
    updatedAt,
    runsCount,
  };
}

// ===== API: Sessions =====

export interface CreateSessionRequest {
  skills_roots?: string[] | null;
}

export async function listSessions(): Promise<Session[]> {
  const data = await fetchJson<{ sessions: unknown[] }>(`/api/v1/sessions`, {
    method: 'GET',
  });

  const sessions = Array.isArray(data.sessions) ? data.sessions : [];
  return sessions.map(mapListedSession).filter((s): s is Session => s !== null);
}

export async function createSession(req: CreateSessionRequest = {}): Promise<Session> {
  const data = await fetchJson<{ session_id: string; created_at: string }>(`/api/v1/sessions`, {
    method: 'POST',
    body: JSON.stringify(req),
  });
  return mapSession(data);
}

export async function setSessionSkillRoots(sessionId: string, roots: string[]): Promise<void> {
  await fetchNoContent(`/api/v1/sessions/${encodeURIComponent(sessionId)}/skills/roots`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ roots }),
  });
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetchNoContent(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  });
}

export async function getSessionSkills(sessionId: string): Promise<{
  roots: string[];
  disabledPaths: string[];
  skills: SkillManifest[];
}> {
  const data = await fetchJson<{
    roots: string[];
    disabled_paths: string[];
    skills: unknown[];
  }>(`/api/v1/sessions/${encodeURIComponent(sessionId)}/skills`, {
    method: 'GET',
  });

  return {
    roots: readStringArray(data.roots),
    disabledPaths: readStringArray(data.disabled_paths),
    skills: (Array.isArray(data.skills) ? data.skills : []).map(mapSkill),
  };
}

export async function listSessionSkills(sessionId: string): Promise<SkillManifest[]> {
  const data = await getSessionSkills(sessionId);
  return data.skills;
}

// ===== API: Studio Skill Creation =====

export interface CreateStudioSkillBody {
  name: string;
  description: string;
  title?: string;
  body_markdown?: string;
  target_root?: string;
}

export async function createStudioSkill(
  sessionId: string,
  body: CreateStudioSkillBody,
): Promise<void | SkillManifest> {
  const res = await fetch(`/studio/api/v1/sessions/${encodeURIComponent(sessionId)}/skills`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const details = await readErrorBody(res);
    throw new APIError(res.status, `Request failed: ${res.status} ${res.statusText}`, details);
  }

  if (res.status === 204) return;

  const contentType = res.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) return;

  const json = (await res.json()) as unknown;
  if (isRecord(json) && 'skill' in json) return mapSkill(json.skill);
  if (isRecord(json) && 'name' in json) return mapSkill(json);
}

// ===== API: Runs + SSE =====

export async function createRun(
  sessionId: string,
  message: string,
): Promise<{ runId: string }> {
  const data = await fetchJson<Record<string, unknown>>(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/runs`,
    {
      method: 'POST',
      body: JSON.stringify({ message }),
    },
  );

  const runId =
    (typeof data.run_id === 'string' && data.run_id) ||
    (typeof data.runId === 'string' && data.runId) ||
    (typeof data.id === 'string' && data.id) ||
    '';

  if (!runId) throw new Error('createRun: missing run_id in response');
  return { runId };
}

export type StreamRunEvent = {
  id?: string;
  event: string;
  data: unknown;
  raw: string;
};

function splitSseBuffer(buffer: string): { complete: string; rest: string } {
  const boundary = /\r?\n\r?\n/g;
  let lastEnd = 0;
  for (const match of buffer.matchAll(boundary)) {
    if (typeof match.index === 'number') lastEnd = match.index + match[0].length;
  }
  if (lastEnd === 0) return { complete: '', rest: buffer };
  return { complete: buffer.slice(0, lastEnd), rest: buffer.slice(lastEnd) };
}

function toStreamEvent(ev: ParsedSseEvent): StreamRunEvent {
  const raw = ev.data;
  let parsed: unknown = raw;
  if (raw !== '') {
    try {
      parsed = JSON.parse(raw) as unknown;
    } catch {
      parsed = raw;
    }
  }

  return {
    id: ev.id,
    event: ev.event,
    data: parsed,
    raw,
  };
}

export async function streamRunEvents(
  runId: string,
  onEvent: (ev: StreamRunEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(
    `/api/v1/runs/${encodeURIComponent(runId)}/events/stream?since=0`,
    {
      method: 'GET',
      headers: { accept: 'text/event-stream' },
      signal,
    },
  );

  if (!res.ok) {
    const details = await readErrorBody(res);
    throw new APIError(res.status, `Request failed: ${res.status} ${res.statusText}`, details);
  }

  if (!res.body) throw new Error('SSE stream missing response body');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const { complete, rest } = splitSseBuffer(buffer);
      buffer = rest;

      if (!complete) continue;
      const parsed = parseSseText(complete);
      for (const ev of parsed) {
        onEvent(toStreamEvent(ev));
      }
    }
  } catch (err) {
    const name = err instanceof Error ? err.name : '';
    if (name === 'AbortError') return;
    throw err;
  } finally {
    reader.releaseLock();
  }
}

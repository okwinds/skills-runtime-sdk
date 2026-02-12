import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { getSessionSkills, listSessions } from './api';

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'content-type': 'application/json' },
    ...init,
  });
}

describe('api.ts response mapping', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('listSessions() maps {sessions:[{session_id,title,updated_at,runs_count}]} -> Session[]', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        sessions: [
          { session_id: 's1', title: 'First', updated_at: '2026-02-09T00:00:00Z', runs_count: 3 },
          { session_id: 's2', title: null, updated_at: 123, runs_count: '4' },
          { title: 'missing id', updated_at: 'x', runs_count: 1 },
        ],
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(listSessions()).resolves.toEqual([
      { id: 's1', title: 'First', updatedAt: '2026-02-09T00:00:00Z', runsCount: 3 },
      { id: 's2', title: undefined, updatedAt: undefined, runsCount: undefined },
    ]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/sessions', expect.objectContaining({ method: 'GET' }));
  });

  it('getSessionSkills() maps roots/disabled_paths/skills -> roots/disabledPaths/skills', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        roots: ['/skills'],
        disabled_paths: ['/skills/disabled'],
        skills: [
          {
            name: 'example',
            description: 'Example skill',
            path: '/skills/example',
            enabled: false,
            dependencies: { env_vars: ['OPENAI_API_KEY'] },
          },
        ],
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(getSessionSkills('a/b')).resolves.toEqual({
      roots: ['/skills'],
      disabledPaths: ['/skills/disabled'],
      skills: [
        {
          name: 'example',
          description: 'Example skill',
          path: '/skills/example',
          enabled: false,
          dependencies: { env_vars: ['OPENAI_API_KEY'] },
        },
      ],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/sessions/a%2Fb/skills',
      expect.objectContaining({ method: 'GET' })
    );
  });
});


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

export async function readErrorBody(res: Response): Promise<unknown> {
  const contentType = res.headers.get('content-type') ?? '';
  try {
    if (contentType.includes('application/json')) return await res.json();
    return await res.text();
  } catch {
    return undefined;
  }
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
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

export async function fetchNoContent(path: string, init?: RequestInit): Promise<void> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const details = await readErrorBody(res);
    throw new APIError(res.status, `Request failed: ${res.status} ${res.statusText}`, details);
  }
}


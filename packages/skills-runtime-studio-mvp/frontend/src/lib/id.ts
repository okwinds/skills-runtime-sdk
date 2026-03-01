export function generateId(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cryptoAny: any = globalThis.crypto;
  if (cryptoAny && typeof cryptoAny.randomUUID === 'function') {
    return cryptoAny.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}


export type RecordLike = Record<string, unknown>;

export function isRecord(value: unknown): value is RecordLike {
  return typeof value === 'object' && value !== null;
}


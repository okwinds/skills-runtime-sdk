import type { StreamRunEvent } from './api';

export type StatusItem = {
  id: string;
  timestamp: string; // ISO
  message: string;
  force_open: boolean;
  always_open: boolean;
  source_event: string;
};

function generateId(): string {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const cryptoAny: any = globalThis.crypto;
  if (cryptoAny && typeof cryptoAny.randomUUID === 'function') {
    return cryptoAny.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function getStringField(obj: Record<string, unknown>, field: string): string | null {
  const value = obj[field];
  return typeof value === 'string' ? value : null;
}

function readEventTimestampIso(data: unknown): string {
  if (isRecord(data)) {
    const ts = getStringField(data, 'timestamp');
    if (ts) return ts;
  }
  return new Date().toISOString();
}

function readPayload(data: unknown): Record<string, unknown> | null {
  if (!isRecord(data)) return null;
  const payload = data.payload;
  return isRecord(payload) ? payload : null;
}

function readMetadata(payload: Record<string, unknown> | null): Record<string, unknown> | null {
  if (!payload) return null;
  const md = payload.metadata;
  return isRecord(md) ? md : null;
}

function readNotices(metadata: Record<string, unknown> | null): Array<Record<string, unknown>> {
  if (!metadata) return [];
  const notices = metadata.notices;
  if (!Array.isArray(notices)) return [];
  return notices.filter(isRecord) as Array<Record<string, unknown>>;
}

function describeToolFromPayload(payload: Record<string, unknown>): string | null {
  const tool = getStringField(payload, 'tool') ?? getStringField(payload, 'name');
  return tool;
}

export function deriveStatusItem(ev: StreamRunEvent): StatusItem | null {
  const ts = readEventTimestampIso(ev.data);
  const payload = readPayload(ev.data) ?? (isRecord(ev.data) ? (ev.data as Record<string, unknown>) : null);

  if (ev.event === 'run_started') {
    return { id: generateId(), timestamp: ts, message: '开始运行', force_open: false, always_open: false, source_event: ev.event };
  }

  if (ev.event === 'llm_request_started') {
    const model = payload ? getStringField(payload, 'model') : null;
    return {
      id: generateId(),
      timestamp: ts,
      message: model ? `模型请求中（${model}）` : '模型请求中',
      force_open: true,
      always_open: false,
      source_event: ev.event,
    };
  }

  if (ev.event === 'tool_call_requested') {
    const tool = payload ? describeToolFromPayload(payload) : null;
    return {
      id: generateId(),
      timestamp: ts,
      message: tool ? `请求工具：${tool}` : '请求工具',
      force_open: true,
      always_open: false,
      source_event: ev.event,
    };
  }

  if (ev.event === 'approval_requested') {
    const tool = payload ? getStringField(payload, 'tool') : null;
    return {
      id: generateId(),
      timestamp: ts,
      message: tool ? `等待审批：${tool}` : '等待审批',
      force_open: true,
      always_open: true,
      source_event: ev.event,
    };
  }

  if (ev.event === 'tool_call_finished') {
    const tool = payload ? describeToolFromPayload(payload) : null;
    const result = payload && isRecord(payload.result) ? (payload.result as Record<string, unknown>) : null;
    const ok = result ? result.ok : null;
    const okLabel = typeof ok === 'boolean' ? (ok ? 'ok' : 'fail') : '';
    return {
      id: generateId(),
      timestamp: ts,
      message: tool ? `工具完成：${tool}${okLabel ? `（${okLabel}）` : ''}` : '工具完成',
      force_open: false,
      always_open: false,
      source_event: ev.event,
    };
  }

  if (ev.event === 'run_completed') {
    const md = readMetadata(payload);
    const notices = readNotices(md);
    const compacted = notices.find((n) => getStringField(n, 'kind') === 'context_compacted') ?? null;
    if (compacted) {
      const message = getStringField(compacted, 'message') ?? '本次运行发生过上下文压缩；摘要可能遗漏细节。';
      const suggestion = getStringField(compacted, 'suggestion');
      const extra = suggestion ? `${message} 建议：${suggestion}` : message;
      return {
        id: generateId(),
        timestamp: ts,
        message: `完成（注意：${extra}）`,
        force_open: true,
        always_open: true,
        source_event: ev.event,
      };
    }
    return { id: generateId(), timestamp: ts, message: '完成', force_open: false, always_open: false, source_event: ev.event };
  }
  if (ev.event === 'run_failed') {
    const p = payload;
    const kind = p ? getStringField(p, 'error_kind') : null;
    const message = p ? getStringField(p, 'message') : null;
    const text = kind && message ? `失败：${kind}（${message}）` : '失败';
    return { id: generateId(), timestamp: ts, message: text, force_open: true, always_open: true, source_event: ev.event };
  }
  if (ev.event === 'run_cancelled') {
    return { id: generateId(), timestamp: ts, message: '已取消', force_open: true, always_open: true, source_event: ev.event };
  }

  return null;
}

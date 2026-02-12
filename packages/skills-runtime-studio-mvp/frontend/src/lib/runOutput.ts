export type RunOutputTextKind = 'delta' | 'content' | 'text';

export type RunOutputText = {
  kind: RunOutputTextKind;
  value: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function readStringField(obj: Record<string, unknown>, field: string): string | null {
  const value = obj[field];
  return typeof value === 'string' ? value : null;
}

function formatRunFailed(payload: Record<string, unknown>): string | null {
  const message = readStringField(payload, 'message');
  if (!message) return null;
  const errorKind = readStringField(payload, 'error_kind') ?? 'unknown';
  return `[run_failed] ${errorKind}: ${message}`;
}

function formatRunCancelled(payload: Record<string, unknown>): string | null {
  const message = readStringField(payload, 'message');
  if (!message) return null;
  return `[run_cancelled] ${message}`;
}

/**
 * 从“整条 run 事件 JSON object”（JSONL 原样 SSE data）里提取可展示到 Output 的文本。
 *
 * 说明：
 * - UI 的 streaming output 主要来自 `llm_response_delta.payload.text`（此类在 SSETimeline 内部已有提取逻辑）
 * - 但对于 terminal 事件（run_failed/run_completed/run_cancelled），用户需要看到可读的最终信息
 */
export function extractRunOutputText(data: unknown): RunOutputText | null {
  if (!isRecord(data)) return null;

  const type = readStringField(data, 'type');
  const payload = isRecord(data.payload) ? data.payload : null;

  // 常见形态：{type, payload:{...}}
  if (type && payload) {
    if (type === 'run_completed') {
      const finalOutput = readStringField(payload, 'final_output');
      if (finalOutput) return { kind: 'content', value: finalOutput };
      return null;
    }

    if (type === 'run_failed') {
      const v = formatRunFailed(payload);
      return v ? { kind: 'content', value: v } : null;
    }

    if (type === 'run_cancelled') {
      const v = formatRunCancelled(payload);
      return v ? { kind: 'content', value: v } : null;
    }
  }

  // 兼容：若上层只传了 payload（未来协议可能调整），也尽量兜底展示
  if (readStringField(data, 'final_output')) {
    return { kind: 'content', value: readStringField(data, 'final_output') as string };
  }

  const vFailed = formatRunFailed(data);
  if (vFailed) return { kind: 'content', value: vFailed };

  const vCancelled = formatRunCancelled(data);
  if (vCancelled) return { kind: 'content', value: vCancelled };

  return null;
}


import type { StreamRunEvent } from './api';

type RecordLike = Record<string, unknown>;

function isRecord(value: unknown): value is RecordLike {
  return typeof value === 'object' && value !== null;
}

function readPayload(data: unknown): RecordLike | null {
  if (!isRecord(data)) return null;
  const payload = data.payload;
  if (isRecord(payload)) return payload;
  return data;
}

function readTimestampIso(data: unknown): string {
  if (isRecord(data)) {
    const ts = data.timestamp ?? data.ts;
    if (typeof ts === 'string' && ts) return ts;
  }
  return new Date().toISOString();
}

function readString(obj: RecordLike, key: string): string | null {
  const v = obj[key];
  return typeof v === 'string' ? v : null;
}

export type ApprovalEventItem = {
  id: string;
  timestamp: string; // ISO
  approval_key: string;
  tool?: string;
  summary?: string;
  decision?: string;
  reason?: string;
  source_event: 'approval_requested' | 'approval_decided';
};

export function extractApprovalEvents(events: StreamRunEvent[]): ApprovalEventItem[] {
  const out: ApprovalEventItem[] = [];
  for (const ev of events) {
    if (ev.event !== 'approval_requested' && ev.event !== 'approval_decided') continue;
    const payload = readPayload(ev.data);
    if (!payload) continue;
    const approvalKey = readString(payload, 'approval_key');
    if (!approvalKey) continue;
    const ts = readTimestampIso(ev.data);
    out.push({
      id: `${ts}-${approvalKey}-${ev.event}`,
      timestamp: ts,
      approval_key: approvalKey,
      tool: readString(payload, 'tool') ?? undefined,
      summary: readString(payload, 'summary') ?? undefined,
      decision: readString(payload, 'decision') ?? undefined,
      reason: readString(payload, 'reason') ?? undefined,
      source_event: ev.event,
    });
  }
  return out;
}

export type ConfigSummary = {
  models?: RecordLike;
  llm?: RecordLike;
  env_file?: unknown;
  config_overlays?: unknown;
  sources?: RecordLike;
  raw: RecordLike;
};

export function extractConfigSummary(events: StreamRunEvent[]): ConfigSummary | null {
  for (const ev of events) {
    if (ev.event !== 'run_started') continue;
    const payload = readPayload(ev.data);
    if (!payload) continue;
    const cs = payload.config_summary;
    if (!isRecord(cs)) continue;
    return {
      models: isRecord(cs.models) ? (cs.models as RecordLike) : undefined,
      llm: isRecord(cs.llm) ? (cs.llm as RecordLike) : undefined,
      env_file: cs.env_file,
      config_overlays: cs.config_overlays,
      sources: isRecord(cs.sources) ? (cs.sources as RecordLike) : undefined,
      raw: cs as RecordLike,
    };
  }
  return null;
}

export type SandboxMeta = {
  requested?: string;
  effective?: string;
  adapter?: string;
  active?: boolean;
};

export type ToolSandboxEntry = {
  id: string;
  timestamp: string; // ISO
  tool: string;
  ok?: boolean;
  error_kind?: string | null;
  exit_code?: number | null;
  sandbox: SandboxMeta;
  raw_result?: RecordLike;
};

export function extractToolSandboxEntries(events: StreamRunEvent[]): ToolSandboxEntry[] {
  const out: ToolSandboxEntry[] = [];
  for (const ev of events) {
    if (ev.event !== 'tool_call_finished') continue;
    const payload = readPayload(ev.data);
    if (!payload) continue;
    const toolName = readString(payload, 'tool') ?? readString(payload, 'name') ?? 'unknown_tool';
    const result = isRecord(payload.result) ? (payload.result as RecordLike) : null;
    const data = result && isRecord(result.data) ? (result.data as RecordLike) : null;
    const sandbox = data && isRecord(data.sandbox) ? (data.sandbox as RecordLike) : null;
    const ts = readTimestampIso(ev.data);

    const entry: ToolSandboxEntry = {
      id: `${ts}-${toolName}`,
      timestamp: ts,
      tool: toolName,
      ok: typeof result?.ok === 'boolean' ? (result!.ok as boolean) : undefined,
      error_kind: typeof result?.error_kind === 'string' ? (result!.error_kind as string) : null,
      exit_code: typeof result?.exit_code === 'number' ? (result!.exit_code as number) : null,
      sandbox: {
        requested: typeof sandbox?.requested === 'string' ? (sandbox!.requested as string) : undefined,
        effective: typeof sandbox?.effective === 'string' ? (sandbox!.effective as string) : undefined,
        adapter: typeof sandbox?.adapter === 'string' ? (sandbox!.adapter as string) : undefined,
        active: typeof sandbox?.active === 'boolean' ? (sandbox!.active as boolean) : undefined,
      },
      raw_result: result ?? undefined,
    };

    out.push(entry);
  }
  return out;
}

export function summarizeSandbox(entries: ToolSandboxEntry[]): {
  active: boolean;
  adapter?: string;
  effective?: string;
  total_tools: number;
  failed_tools: number;
} {
  const total = entries.length;
  const failed = entries.filter((e) => e.ok === false).length;
  const last = entries[entries.length - 1];
  const active = Boolean(last?.sandbox?.active);
  return {
    active,
    adapter: last?.sandbox?.adapter,
    effective: last?.sandbox?.effective,
    total_tools: total,
    failed_tools: failed,
  };
}


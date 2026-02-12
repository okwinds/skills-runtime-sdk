import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import './SSETimeline.css';
import { Button } from '../ui/Button';
import { Tabs } from '../ui/Tabs';
import { APIError, createRun, streamRunEvents, type StreamRunEvent } from '../../lib/api';
import { extractRunOutputText, type RunOutputText } from '../../lib/runOutput';
import { ApprovalModal } from './ApprovalModal';
import { listPendingApprovals, type PendingApproval } from '../../lib/approvals';
import { appendHistoryEntry, loadHistoryForSession, type HistoryEntry } from '../../lib/history';
import { deriveStatusItem, type StatusItem } from '../../lib/runStatus';
import { extractApprovalEvents, extractConfigSummary, extractToolSandboxEntries, summarizeSandbox } from '../../lib/runInfo';

interface TimelineEvent {
  id: string;
  type: string;
  timestamp: Date;
  data: unknown;
  summary?: string;
}

interface SSETimelineProps {
  sessionId: string;
}

const generateEventId = (): string => `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;

const formatTime = (date: Date): string =>
  date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function getStringField(obj: Record<string, unknown>, field: string): string | null {
  const value = obj[field];
  return typeof value === 'string' ? value : null;
}

function summarizeEvent(ev: StreamRunEvent): string | undefined {
  const runOutput = extractRunOutputText(ev.data) as RunOutputText | null;
  if (runOutput?.value) return runOutput.value;

  if (ev.event === 'llm_response_delta' && isRecord(ev.data)) {
    const payload = isRecord(ev.data.payload) ? (ev.data.payload as Record<string, unknown>) : null;
    const deltaType = payload ? getStringField(payload, 'delta_type') : null;
    if (!deltaType) return 'delta';
    if (deltaType === 'text') {
      const text = payload ? getStringField(payload, 'text') : null;
      // Logs 面板需要能看见 token/text 增量，便于排障与理解流式输出。
      return text ?? 'delta:text';
    }
    return `delta:${deltaType}`;
  }

  if ((ev.event === 'approval_requested' || ev.event === 'approval_decided') && isRecord(ev.data)) {
    const payload = isRecord(ev.data.payload) ? (ev.data.payload as Record<string, unknown>) : null;
    if (payload) {
      const summary = getStringField(payload, 'summary');
      const decision = getStringField(payload, 'decision');
      const reason = getStringField(payload, 'reason');
      if (summary) return summary;
      if (decision) return reason ? `decision=${decision} (${reason})` : `decision=${decision}`;
    }
    return ev.event;
  }

  if ((ev.event === 'tool_call_requested' || ev.event === 'tool_call_finished') && isRecord(ev.data)) {
    const payload = isRecord(ev.data.payload) ? (ev.data.payload as Record<string, unknown>) : null;
    if (payload) {
      const tool = getStringField(payload, 'tool') ?? getStringField(payload, 'name');
      if (tool) return `${ev.event}: ${tool}`;
    }
    return ev.event;
  }

  if (typeof ev.raw === 'string' && ev.raw.trim()) return ev.raw;
  try {
    return JSON.stringify(ev.data);
  } catch {
    return undefined;
  }
}

export const SSETimeline: React.FC<SSETimelineProps> = ({ sessionId }) => {
  const [inputValue, setInputValue] = useState('');
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [status, setStatus] = useState<'idle' | 'running' | 'success' | 'error'>('idle');
  const [isLoading, setIsLoading] = useState(false);
  const [outputText, setOutputText] = useState('');
  const [outputOpen, setOutputOpen] = useState(true);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [statusItems, setStatusItems] = useState<StatusItem[]>([]);
  const lastPromptRef = useRef<string>('');
  const infoDismissedRef = useRef(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [infoTab, setInfoTab] = useState<'status' | 'history' | 'logs' | 'approvals' | 'sandbox' | 'config'>('status');
  const [sandboxView, setSandboxView] = useState<'summary' | 'tools' | 'last'>('summary');

  // Approvals state
  const [pendingApprovals, setPendingApprovals] = useState<PendingApproval[]>([]);
  const [showApprovalModal, setShowApprovalModal] = useState(false);
  const currentRunIdRef = useRef<string | null>(null);

  const logsScrollRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const terminalStatusRef = useRef<'success' | 'error' | null>(null);

  const streamEvents = useMemo<StreamRunEvent[]>(
    () => events.map((e) => ({ event: e.type, data: e.data, raw: '' })),
    [events],
  );

  const approvalEventItems = useMemo(() => extractApprovalEvents(streamEvents), [streamEvents]);
  const configSummary = useMemo(() => extractConfigSummary(streamEvents), [streamEvents]);
  const toolSandboxEntries = useMemo(() => extractToolSandboxEntries(streamEvents), [streamEvents]);
  const sandboxSummary = useMemo(() => summarizeSandbox(toolSandboxEntries), [toolSandboxEntries]);
  const lastSandboxFailure = useMemo(
    () => [...toolSandboxEntries].reverse().find((e) => e.ok === false && e.sandbox?.active === true) ?? null,
    [toolSandboxEntries],
  );

  const openInfo = useCallback(
    (tab?: typeof infoTab) => {
      setInfoOpen(true);
      if (tab) setInfoTab(tab);
      infoDismissedRef.current = false;
    },
    [],
  );

  // Check for pending approvals when run changes
  const checkPendingApprovals = useCallback(async (runId: string) => {
    try {
      const response = await listPendingApprovals(runId);
      const approvals = response.approvals ?? response.pending ?? [];
      if (approvals.length > 0) {
        setPendingApprovals(approvals);
        setShowApprovalModal(true);
      }
    } catch (err) {
      // Silently ignore errors here - they're not critical for the flow
      console.error('Failed to check pending approvals:', err);
    }
  }, []);

  useEffect(() => {
    if (!infoOpen) return;
    if (infoTab !== 'logs') return;
    if (!logsScrollRef.current) return;
    logsScrollRef.current.scrollTop = logsScrollRef.current.scrollHeight;
  }, [events, infoOpen, infoTab]);

  const statusText = useMemo(
    () => ({
      idle: 'Ready',
      running: 'Streaming...',
      success: 'Completed',
      error: 'Error',
    }),
    [],
  );

  const handleCancel = () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setIsLoading(false);
    setStatus('idle');
  };

  const handleClear = () => {
    setInputValue('');
    setEvents([]);
    setOutputText('');
    setStatusItems([]);
    setStatus('idle');
    setPendingApprovals([]);
    setShowApprovalModal(false);
  };

  function readEventPayload(data: unknown): Record<string, unknown> | null {
    if (!isRecord(data)) return null;
    const payload = data.payload;
    if (isRecord(payload)) return payload;
    return null;
  }

  function extractStreamDeltaText(data: unknown): string | null {
    if (!isRecord(data)) return null;
    const payload = readEventPayload(data);
    if (!payload) return null;
    const deltaType = getStringField(payload, 'delta_type');
    if (deltaType !== 'text') return null;
    const text = getStringField(payload, 'text');
    return text ?? null;
  }

  const appendEvent = (ev: StreamRunEvent) => {
    const summary = summarizeEvent(ev);
    setEvents((prev) => [
      ...prev,
      {
        id: generateEventId(),
        type: ev.event,
        timestamp: new Date(),
        data: ev.data,
        summary,
      },
    ]);

    if (ev.event === 'run_failed') terminalStatusRef.current = 'error';
    if (ev.event === 'run_completed') terminalStatusRef.current = 'success';

    const statusItem = deriveStatusItem(ev);
    if (statusItem) {
      setStatusItems((prev) => [...prev, statusItem]);
      if (statusItem.force_open) {
        const shouldOpen = (!infoDismissedRef.current) || statusItem.always_open;
        if (shouldOpen) openInfo('status');
        if (statusItem.always_open) infoDismissedRef.current = false;
      }
    }

    // Handle approval events
    if (ev.event === 'approval_requested') {
      const payload = readEventPayload(ev.data) ?? (isRecord(ev.data) ? (ev.data as Record<string, unknown>) : null);
      if (payload && typeof payload.approval_key === 'string') {
        openInfo('approvals');
        // Refresh pending approvals to get the latest state
        if (currentRunIdRef.current) {
          checkPendingApprovals(currentRunIdRef.current);
        }
      }
    }

    if (ev.event === 'approval_decided') {
      const payload = readEventPayload(ev.data) ?? (isRecord(ev.data) ? (ev.data as Record<string, unknown>) : null);
      if (payload && typeof payload.approval_key === 'string') {
        // Remove this approval from pending list and close modal if empty
        setPendingApprovals((prev) => {
          const next = prev.filter((a) => a.approval_key !== payload.approval_key);
          if (next.length === 0) {
            setShowApprovalModal(false);
          }
          return next;
        });
      }
    }

    // Terminal output should always show up in Output panel
    const terminal = extractRunOutputText(ev.data);
    if (terminal?.value) {
      setOutputText(terminal.value);

      // Write history entry on terminal events (best-effort)
      const runId = currentRunIdRef.current;
      if (runId) {
        const kind =
          ev.event === 'run_completed'
            ? 'success'
            : ev.event === 'run_failed'
              ? 'error'
              : ev.event === 'run_cancelled'
                ? 'cancelled'
                : 'unknown';

        setHistory((prev) =>
          appendHistoryEntry(prev, {
            run_id: runId,
            prompt: lastPromptRef.current,
            final_text: terminal.value,
            status: kind,
            created_at: new Date().toISOString(),
          }, sessionId),
        );
      }
    }

    // Token-stream deltas go to Output panel
    const deltaText = extractStreamDeltaText(ev.data);
    if (deltaText) {
      setOutputText((prev) => prev + deltaText);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputValue.trim() || isLoading) return;

    setEvents([]);
    setOutputText('');
    setStatusItems([]);
    infoDismissedRef.current = false;
    setStatus('running');
    setIsLoading(true);
    terminalStatusRef.current = null;

    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    lastPromptRef.current = inputValue.trim();

    try {
      const { runId } = await createRun(sessionId, inputValue.trim());
      currentRunIdRef.current = runId;
      await streamRunEvents(runId, appendEvent, abortController.signal);
      setStatus(terminalStatusRef.current === 'error' ? 'error' : 'success');
    } catch (err) {
      const name = err instanceof Error ? err.name : '';
      if (name !== 'AbortError') {
        setStatus('error');
        if (err instanceof APIError) {
          const detailsText = (() => {
            try {
              return err.details ? `\n\nDetails: ${JSON.stringify(err.details)}` : '';
            } catch {
              return '';
            }
          })();
          setOutputText(`[request_failed] ${err.status}: ${err.message}${detailsText}`);
        } else if (err instanceof Error && err.message) {
          setOutputText(`[request_failed] ${err.message}`);
        }
      }
    } finally {
      setIsLoading(false);
      abortControllerRef.current = null;
    }
  };

  useEffect(() => {
    setHistory(loadHistoryForSession(sessionId));
  }, [sessionId]);

  return (
    <div className="sse-timeline">
      <div className="sse-timeline__header">
        <div className="sse-timeline__title">
          <h3>Run</h3>
          <span className={`sse-timeline__status sse-timeline__status--${status}`}>
            <span className="sse-timeline__status-dot" />
            {statusText[status]}
          </span>
        </div>
        <div className="sse-timeline__actions">
          {events.length > 0 && (
            <Button
              variant="ghost"
              size="small"
              onClick={() => openInfo('logs')}
              title="Open logs"
            >
              Logs
            </Button>
          )}
          {events.length > 0 && (
            <Button variant="ghost" size="small" onClick={handleClear}>
              Clear
            </Button>
          )}
          {isLoading && (
            <Button variant="secondary" size="small" onClick={handleCancel}>
              Cancel
            </Button>
          )}
        </div>
      </div>

      <form className="sse-timeline__input-section" onSubmit={handleSubmit}>
        <div className="sse-timeline__input-row">
          <input
            type="text"
            className="sse-timeline__input"
            placeholder="Try: $[web:mvp].article-writer 写一篇 500 字科普文章..."
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            disabled={isLoading}
          />
          <Button
            type="submit"
            variant="primary"
            isLoading={isLoading}
            disabled={!inputValue.trim() || isLoading}
          >
            Run
          </Button>
        </div>
      </form>

      <div className="sse-timeline__main">
        {/* Output - Top */}
        <div
          className={`sse-timeline__output sse-timeline__output--primary ${!outputOpen ? 'sse-timeline__output--collapsed' : ''}`}
        >
          <div className="sse-timeline__output-header">
            <h4>Output</h4>
            <div className="sse-timeline__output-actions">
              <Button
                variant="ghost"
                size="small"
                onClick={() => setOutputOpen((v) => !v)}
                title={outputOpen ? 'Hide output' : 'Show output'}
              >
                {outputOpen ? 'Hide' : 'Show'}
              </Button>
            </div>
          </div>
          {outputOpen && (
            <pre className="sse-timeline__output-body">
              {outputText.trim()
                ? outputText
                : events.length === 0
                  ? 'Run a task to see output here.'
                  : 'Waiting for output...'}
            </pre>
          )}
        </div>

        {/* Info dock (collapsed by default) */}
        <div className="sse-timeline__bottom">
          <div className={`sse-timeline__info ${!infoOpen ? 'sse-timeline__info--collapsed' : ''}`} aria-label="Run info">
            <div className="sse-timeline__info-header">
              <h4>Info</h4>
              <div className="sse-timeline__info-meta">
                <span>
                  approvals={pendingApprovals.length} · sandbox={sandboxSummary.active ? 'active' : 'inactive'} · tools=
                  {sandboxSummary.total_tools}
                </span>
                <Button
                  variant="ghost"
                  size="small"
                  onClick={() => {
                    setInfoOpen((v) => {
                      const next = !v;
                      if (!next) infoDismissedRef.current = true;
                      return next;
                    });
                  }}
                  title={infoOpen ? 'Hide info' : 'Show info'}
                >
                  {infoOpen ? 'Hide' : 'Show'}
                </Button>
              </div>
            </div>

            {infoOpen && (
              <div className="sse-timeline__info-body">
                <Tabs defaultTab={infoTab} activeTab={infoTab} onChange={(id) => setInfoTab(id as typeof infoTab)}>
                  <Tabs.List>
                    <Tabs.Tab id="status" badge={statusItems.length ? statusItems.length : undefined}>
                      Status
                    </Tabs.Tab>
                    <Tabs.Tab id="history" badge={history.length ? history.length : undefined}>
                      History
                    </Tabs.Tab>
                    <Tabs.Tab id="logs" badge={events.length ? events.length : undefined}>
                      Logs
                    </Tabs.Tab>
                    <Tabs.Tab id="approvals" badge={pendingApprovals.length ? pendingApprovals.length : undefined}>
                      Approvals
                    </Tabs.Tab>
                    <Tabs.Tab id="sandbox">Sandbox</Tabs.Tab>
                    <Tabs.Tab id="config">Config</Tabs.Tab>
                  </Tabs.List>

                  <Tabs.Content>
                    <Tabs.Panel id="status">
                      {statusItems.length === 0 ? (
                        <div className="sse-timeline__panel-empty">No status yet.</div>
                      ) : (
                        <div className="sse-timeline__status-list">
                          {statusItems.slice(-60).map((it) => (
                            <div key={it.id} className="sse-timeline__status-item">
                              <span className="sse-timeline__status-time">{formatTime(new Date(it.timestamp))}</span>
                              <span className="sse-timeline__status-msg">{it.message}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </Tabs.Panel>

                    <Tabs.Panel id="history">
                      {history.length === 0 ? (
                        <div className="sse-timeline__panel-empty">No history yet.</div>
                      ) : (
                        <div className="sse-timeline__history-list">
                          {history.map((h) => (
                            <div key={h.id} className="sse-timeline__history-item">
                              <div className="sse-timeline__history-head">
                                <span className={`sse-timeline__history-badge sse-timeline__history-badge--${h.status}`}>
                                  {h.status}
                                </span>
                                <span className="sse-timeline__history-time">{formatTime(new Date(h.created_at))}</span>
                                <span className="sse-timeline__history-runid">{h.run_id}</span>
                              </div>
                              <div className="sse-timeline__history-prompt">{h.prompt}</div>
                              <pre className="sse-timeline__history-final">
                                {h.final_text.length > 320 ? `${h.final_text.slice(0, 320)}…` : h.final_text}
                              </pre>
                            </div>
                          ))}
                        </div>
                      )}
                    </Tabs.Panel>

                    <Tabs.Panel id="logs">
                      <div className="sse-timeline__logs-body" ref={logsScrollRef}>
                        <div className="sse-timeline__events">
                          {events.map((event, index) => (
                            <div
                              key={event.id}
                              className={`sse-timeline__event ${index === events.length - 1 ? 'sse-timeline__event--new' : ''}`}
                            >
                              <div className="sse-timeline__event-content">
                                <div className="sse-timeline__event-type">
                                  <span>{event.type}</span>
                                  <span className="sse-timeline__event-time">{formatTime(event.timestamp)}</span>
                                </div>
                                {event.summary && (
                                  <pre className="sse-timeline__event-message">
                                    {event.summary.length > 240 ? `${event.summary.slice(0, 240)}…` : event.summary}
                                  </pre>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </Tabs.Panel>

                    <Tabs.Panel id="approvals">
                      {pendingApprovals.length === 0 ? (
                        <div className="sse-timeline__panel-empty">No pending approvals.</div>
                      ) : (
                        <div className="sse-timeline__approvals-list">
                          {pendingApprovals.map((a) => (
                            <div key={a.approval_key} className="sse-timeline__approvals-item">
                              <div className="sse-timeline__approvals-key">{a.approval_key}</div>
                              <div className="sse-timeline__approvals-summary">{a.summary ?? '—'}</div>
                            </div>
                          ))}
                        </div>
                      )}

                      {approvalEventItems.length > 0 && (
                        <div className="sse-timeline__approvals-events">
                          <div className="sse-timeline__section-title">Timeline</div>
                          <div className="sse-timeline__approvals-timeline">
                            {approvalEventItems.slice(-50).map((it) => (
                              <div key={it.id} className="sse-timeline__approvals-timeline-item">
                                <span className="sse-timeline__mono">{formatTime(new Date(it.timestamp))}</span>
                                <span className="sse-timeline__mono">{it.source_event}</span>
                                <span className="sse-timeline__mono">{it.approval_key}</span>
                                <span>{it.summary ?? it.decision ?? '—'}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </Tabs.Panel>

                    <Tabs.Panel id="sandbox">
                      <div className="sse-timeline__sandbox-head">
                        <div className="sse-timeline__sandbox-summary">
                          <div>active: {sandboxSummary.active ? 'true' : 'false'}</div>
                          <div>effective: {sandboxSummary.effective ?? '—'}</div>
                          <div>adapter: {sandboxSummary.adapter ?? '—'}</div>
                          <div>
                            tools: {sandboxSummary.total_tools} (failed: {sandboxSummary.failed_tools})
                          </div>
                        </div>
                        <div className="sse-timeline__sandbox-view">
                          <Button variant={sandboxView === 'summary' ? 'primary' : 'ghost'} size="small" onClick={() => setSandboxView('summary')}>
                            Summary
                          </Button>
                          <Button variant={sandboxView === 'tools' ? 'primary' : 'ghost'} size="small" onClick={() => setSandboxView('tools')}>
                            Per-Tool
                          </Button>
                          <Button variant={sandboxView === 'last' ? 'primary' : 'ghost'} size="small" onClick={() => setSandboxView('last')}>
                            Last Fail
                          </Button>
                        </div>
                      </div>

                      {sandboxView === 'summary' && (
                        <div className="sse-timeline__panel-empty">
                          提示：`active=true` 只代表“走过 OS sandbox adapter”，不代表 profile 足够严格。要验证限制是否生效，建议跑
                          `bash scripts/integration/os_sandbox_restriction_demo.sh`。
                        </div>
                      )}

                      {sandboxView === 'tools' && (
                        <div className="sse-timeline__sandbox-tools">
                          {toolSandboxEntries.length === 0 ? (
                            <div className="sse-timeline__panel-empty">No tool sandbox data yet.</div>
                          ) : (
                            toolSandboxEntries.slice(-80).map((e) => (
                              <div key={e.id} className="sse-timeline__sandbox-tool">
                                <span className="sse-timeline__mono">{formatTime(new Date(e.timestamp))}</span>
                                <span className="sse-timeline__mono">{e.tool}</span>
                                <span className="sse-timeline__mono">ok={String(e.ok)}</span>
                                <span className="sse-timeline__mono">active={String(e.sandbox?.active)}</span>
                                <span className="sse-timeline__mono">{e.sandbox?.adapter ?? '—'}</span>
                                <span className="sse-timeline__mono">{e.sandbox?.effective ?? '—'}</span>
                              </div>
                            ))
                          )}
                        </div>
                      )}

                      {sandboxView === 'last' && (
                        <div className="sse-timeline__sandbox-last">
                          {!lastSandboxFailure ? (
                            <div className="sse-timeline__panel-empty">No sandbox-related failures yet.</div>
                          ) : (
                            <pre className="sse-timeline__mono-block">{JSON.stringify(lastSandboxFailure, null, 2)}</pre>
                          )}
                        </div>
                      )}
                    </Tabs.Panel>

                    <Tabs.Panel id="config">
                      {!configSummary ? (
                        <div className="sse-timeline__panel-empty">No config_summary yet (waiting for run_started).</div>
                      ) : (
                        <div className="sse-timeline__config">
                          <div className="sse-timeline__section-title">Models</div>
                          <pre className="sse-timeline__mono-block">{JSON.stringify(configSummary.models ?? {}, null, 2)}</pre>
                          <div className="sse-timeline__section-title">LLM</div>
                          <pre className="sse-timeline__mono-block">{JSON.stringify(configSummary.llm ?? {}, null, 2)}</pre>
                          <div className="sse-timeline__section-title">Overlays</div>
                          <pre className="sse-timeline__mono-block">{JSON.stringify(configSummary.config_overlays ?? [], null, 2)}</pre>
                          <div className="sse-timeline__section-title">Sources</div>
                          <pre className="sse-timeline__mono-block">{JSON.stringify(configSummary.sources ?? {}, null, 2)}</pre>
                        </div>
                      )}
                    </Tabs.Panel>
                  </Tabs.Content>
                </Tabs>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Approval Modal */}
      {showApprovalModal && pendingApprovals.length > 0 && (
        <ApprovalModal
          runId={currentRunIdRef.current || ''}
          approvals={pendingApprovals}
          onDecisionSubmitted={() => {
            // Refresh pending approvals after decision
            if (currentRunIdRef.current) {
              listPendingApprovals(currentRunIdRef.current)
                .then((response) => {
                  const approvals = response.approvals ?? response.pending ?? [];
                  if (approvals.length > 0) {
                    setPendingApprovals(approvals);
                  } else {
                    setPendingApprovals([]);
                    setShowApprovalModal(false);
                  }
                })
                .catch(() => {
                  // On error, close modal
                  setPendingApprovals([]);
                  setShowApprovalModal(false);
                });
            }
          }}
          onClose={() => {
            // Don't actually close - user must make a decision
            // But provide escape hatch if run is no longer active
            if (!isLoading) {
              setShowApprovalModal(false);
            }
          }}
        />
      )}
    </div>
  );
};

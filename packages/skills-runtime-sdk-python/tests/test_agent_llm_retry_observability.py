from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.state.jsonl_wal import JsonlWal


class _RetryObservableBackend:
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[Any]:
        on_retry = (request.extra or {}).get("on_retry")
        if callable(on_retry):
            on_retry(
                {
                    "attempt": 1,
                    "max_retries": 3,
                    "delay_ms": 250,
                    "reason": "request_error",
                }
            )
        yield ChatStreamEvent(type="text_delta", text="ok")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_emits_llm_retry_scheduled_to_stream_and_wal(tmp_path: Path) -> None:
    agent = Agent(backend=_RetryObservableBackend(), workspace_root=tmp_path, model="fake")

    events = list(agent.run_stream("hi"))
    retry_events = [e for e in events if e.type == "llm_retry_scheduled"]
    assert retry_events, "expected llm_retry_scheduled in run_stream events"
    payload: Dict[str, Any] = dict(retry_events[-1].payload or {})
    assert payload.get("attempt") == 1
    assert payload.get("max_retries") == 3
    assert payload.get("delay_ms") == 250
    assert payload.get("reason") == "request_error"

    completed = [e for e in events if e.type == "run_completed"]
    assert completed, "expected run_completed terminal event"
    wal_locator = str((completed[-1].payload or {}).get("wal_locator") or "")
    assert wal_locator, "expected wal_locator on run_completed"

    wal_events = list(JsonlWal(Path(wal_locator)).iter_events())
    wal_retry_events = [e for e in wal_events if e.type == "llm_retry_scheduled"]
    assert wal_retry_events, "expected llm_retry_scheduled in WAL"
    wal_payload = dict(wal_retry_events[-1].payload or {})
    assert wal_payload.get("attempt") == 1
    assert wal_payload.get("reason") == "request_error"

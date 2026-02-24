from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.tools.protocol import ToolSpec


class _RaiseBackend:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def stream_chat(  # type: ignore[override]
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[Any]:
        _ = model, messages, tools, temperature
        raise self._exc
        yield ChatStreamEvent(type="completed")  # pragma: no cover


def _load_failed_payload(events_path: str) -> Dict[str, Any]:
    events = list(JsonlWal(Path(events_path)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    return dict(failed[-1].payload or {})


def test_run_failed_maps_http_429_to_rate_limited_with_retry_after(tmp_path: Path) -> None:
    req = httpx.Request("POST", "http://example.test/v1/chat/completions")
    resp = httpx.Response(
        status_code=429,
        headers={"Retry-After": "2"},
        json={"error": {"message": "rate limit"}},
        request=req,
    )
    exc = httpx.HTTPStatusError("HTTP error", request=req, response=resp)

    agent = Agent(backend=_RaiseBackend(exc), workspace_root=tmp_path, model="fake")
    result = agent.run("hi")
    assert result.status == "failed"

    payload = _load_failed_payload(result.events_path)
    assert payload.get("error_kind") == "rate_limited"
    assert payload.get("retryable") is True
    assert payload.get("retry_after_ms") == 2000


def test_run_failed_maps_value_error_to_config_error(tmp_path: Path) -> None:
    agent = Agent(backend=_RaiseBackend(ValueError("bad config")), workspace_root=tmp_path, model="fake")
    result = agent.run("hi")
    payload = _load_failed_payload(result.events_path)
    assert payload.get("error_kind") == "config_error"
    assert payload.get("retryable") is False


def test_run_failed_maps_unknown_exception_to_unknown(tmp_path: Path) -> None:
    agent = Agent(backend=_RaiseBackend(RuntimeError("boom")), workspace_root=tmp_path, model="fake")
    result = agent.run("hi")
    payload = _load_failed_payload(result.events_path)
    assert payload.get("error_kind") == "unknown"

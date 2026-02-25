from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Optional

from skills_runtime.core.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest


class _Backend:
    def __init__(self) -> None:
        self.called = False
        self.last_request: Optional[ChatRequest] = None

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self.called = True
        self.last_request = request
        yield ChatStreamEvent(type="text_delta", text=f"ok({request.model})")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_calls_stream_chat_with_chatrequest(tmp_path: Path) -> None:
    backend = _Backend()
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path)

    events = list(agent.run_stream("hi"))
    assert backend.called is True
    assert backend.last_request is not None
    assert backend.last_request.model == "fake-model"
    assert backend.last_request.messages
    assert any(e.type == "run_completed" for e in events)

    # extra 必须可预测地传递给 backend（即便 backend 忽略）
    assert "on_retry" in (backend.last_request.extra or {})


class _LegacyBackend:
    async def stream_chat(self, *, model: str, messages: list[dict[str, Any]]) -> AsyncIterator[ChatStreamEvent]:
        _ = model, messages
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_legacy_backend_signature_is_rejected_fail_fast(tmp_path: Path) -> None:
    agent = Agent(model="fake-model", backend=_LegacyBackend(), workspace_root=tmp_path)

    events = list(agent.run_stream("hi"))
    failed = [e for e in events if e.type == "run_failed"]
    assert failed, "expected run_failed for legacy backend signature"
    payload = failed[-1].payload
    assert payload.get("error_kind") == "config_error"
    assert "stream_chat" in str(payload.get("message") or "")


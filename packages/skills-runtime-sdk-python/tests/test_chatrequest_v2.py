from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from agent_sdk.core.agent import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.protocol import ChatRequest
from agent_sdk.tools.protocol import ToolSpec


class _V2Backend:
    def __init__(self) -> None:
        self.v1_called = False
        self.v2_called = False
        self.last_request: Optional[ChatRequest] = None

    async def stream_chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        self.v1_called = True
        raise AssertionError("Agent loop should not call v1 stream_chat when v2 is available")

    async def stream_chat_v2(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self.v2_called = True
        self.last_request = request
        yield ChatStreamEvent(type="text_delta", text=f"ok({request.model})")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_uses_stream_chat_v2_when_available(tmp_path: Path) -> None:
    backend = _V2Backend()
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path)

    events = list(agent.run_stream("hi"))
    assert backend.v2_called is True
    assert backend.v1_called is False
    assert backend.last_request is not None
    assert backend.last_request.model == "fake-model"
    assert backend.last_request.messages
    assert any(e.type == "run_completed" for e in events)


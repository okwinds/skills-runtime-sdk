from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from skills_runtime.core.agent import Agent
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolSpec


class _StubBackend:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        # 若取消逻辑正确，测试不应走到这里（但即使到这里也不影响断言）
        yield ChatStreamEvent(type="text_delta", text=f"echo({request.model})")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_cancelled_emits_run_cancelled(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    agent = Agent(
        backend=_StubBackend(),
        workspace_root=tmp_path,
        cancel_checker=lambda: True,
    )

    events: List[AgentEvent] = list(agent.run_stream("hi"))
    types = [e.type for e in events]
    assert "run_started" in types
    assert "run_cancelled" in types
    assert "llm_request_started" not in types


class _BlockingBackend:
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        _ = request
        # 模拟真实网络 SSE：长时间无输出（等待会被 task.cancel() 打断）
        import asyncio

        await asyncio.sleep(999)
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_cancel_interrupts_blocking_stream(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import time

    monkeypatch.chdir(tmp_path)

    started = time.monotonic()

    def cancel_checker() -> bool:
        return (time.monotonic() - started) > 0.05

    agent = Agent(
        backend=_BlockingBackend(),
        workspace_root=tmp_path,
        cancel_checker=cancel_checker,
    )

    events: List[AgentEvent] = list(agent.run_stream("hi"))
    types = [e.type for e in events]
    assert "run_cancelled" in types

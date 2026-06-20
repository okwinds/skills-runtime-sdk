"""BL-038 测试：run_stream_async_iter 应传播后台异常（fail-loud），不得静默空流。

对齐 sync 路径 run_stream_sync 的 err_q fail-loud 语义。
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.stream_adapters import run_stream_async_iter


class _BoomLoop:
    """伪 loop：_run_stream_async 抛异常，模拟启动期 WalCorruptError 等失败。"""

    async def _run_stream_async(
        self, task: str, *, run_id: Optional[str], initial_history: Optional[List[Any]], emit: Any
    ) -> None:
        # 先发一个事件，再抛异常（模拟 run 中途失败）
        emit(AgentEvent(type="run_started", timestamp="t", run_id=run_id or "r", payload={}))
        raise RuntimeError("boom-from-runner")


class _EmptyBoomLoop:
    """伪 loop：未发任何事件即抛异常（模拟启动期 prepare_resume 失败）。"""

    async def _run_stream_async(
        self, task: str, *, run_id: Optional[str], initial_history: Optional[List[Any]], emit: Any
    ) -> None:
        raise RuntimeError("boom-before-any-event")


class _NormalLoop:
    """伪 loop：正常发两个事件后结束。"""

    async def _run_stream_async(
        self, task: str, *, run_id: Optional[str], initial_history: Optional[List[Any]], emit: Any
    ) -> None:
        emit(AgentEvent(type="run_started", timestamp="t", run_id=run_id or "r", payload={}))
        emit(AgentEvent(type="run_completed", timestamp="t", run_id=run_id or "r", payload={}))


@pytest.mark.asyncio
async def test_async_iter_propagates_runner_exception() -> None:
    """_runner 抛异常时，消费者应收到已发事件后 re-raise（不得静默空流）。"""

    events = []
    with pytest.raises(RuntimeError, match="boom-from-runner"):
        async for ev in run_stream_async_iter(_BoomLoop(), "task", run_id="r", initial_history=None):
            events.append(ev)
    # 已发事件仍应被消费（不丢）
    assert any(e.type == "run_started" for e in events)


@pytest.mark.asyncio
async def test_async_iter_propagates_exception_before_any_event() -> None:
    """启动期即抛异常（无任何事件）时，消费者应 re-raise 而非静默空流结束。"""

    with pytest.raises(RuntimeError, match="boom-before-any-event"):
        async for _ in run_stream_async_iter(_EmptyBoomLoop(), "task", run_id="r", initial_history=None):
            pass


@pytest.mark.asyncio
async def test_async_iter_normal_stream_unchanged() -> None:
    """正常流不回归：消费全部事件，不抛。"""

    events = []
    async for ev in run_stream_async_iter(_NormalLoop(), "task", run_id="r", initial_history=None):
        events.append(ev)
    assert [e.type for e in events] == ["run_started", "run_completed"]

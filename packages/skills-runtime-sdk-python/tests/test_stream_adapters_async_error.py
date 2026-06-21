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


class _LongLoop:
    """伪 loop：持续发事件不结束，模拟消费者中途 break 的场景。"""

    async def _run_stream_async(
        self, task: str, *, run_id: Optional[str], initial_history: Optional[List[Any]], emit: Any
    ) -> None:
        # 模拟长 run：循环发事件，被 cancel 时退出（await 抛 CancelledError）。
        try:
            while True:
                emit(AgentEvent(type="tool_call_requested", timestamp="t", run_id=run_id or "r", payload={}))
                # 让出控制权，允许消费者消费 + cancel 传播。
                import asyncio

                await asyncio.sleep(0)
        except BaseException:
            # runner 被 cancel 时收 CancelledError；不向外抛（由 stream_adapters 统一处理）。
            raise


@pytest.mark.asyncio
async def test_async_iter_early_break_does_not_raise_cancelled() -> None:
    """消费者提前 break（干净的中途退出）不应向消费者抛 CancelledError。

    回归护栏：BL-038 早先版本会在 break→t.cancel()→runner 收 CancelledError→finally re-raise，
    把干净退出变成异常抛出。
    """

    events = []
    # 消费首个事件后立即 break
    async for ev in run_stream_async_iter(_LongLoop(), "task", run_id="r", initial_history=None):
        events.append(ev)
        break
    assert len(events) == 1
    # break 后 async for 正常结束（GeneratorExit 收尾），不应抛 CancelledError
    # （若抛出，此测试会以 CancelledError 失败）

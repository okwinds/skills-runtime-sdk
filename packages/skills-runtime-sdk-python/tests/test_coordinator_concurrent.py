"""Task 7.3：验证 Coordinator.run_children_concurrent() async 并发语义。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from skills_runtime.core.agent import Agent
from skills_runtime.core.coordinator import Coordinator
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest


class _DelayedBackend:
    """模拟有延迟的 LLM 响应（用于验证并发执行）。"""

    def __init__(self, *, delay: float, text: str) -> None:
        self._delay = delay
        self._text = text

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        await asyncio.sleep(self._delay)
        yield ChatStreamEvent(type="text_delta", text=self._text)
        yield ChatStreamEvent(type="completed", finish_reason="stop")


class _ImmediateBackend:
    """立即完成的 stub backend。"""

    def __init__(self, *, text: str = "ok") -> None:
        self._text = text

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        yield ChatStreamEvent(type="text_delta", text=self._text)
        yield ChatStreamEvent(type="completed", finish_reason="stop")


@pytest.mark.asyncio
async def test_run_children_concurrent_collects_all_results(tmp_path: Path) -> None:
    """run_children_concurrent 必须返回所有子 agent 的结果，按 child_tasks 顺序排列。"""
    primary = Agent(backend=_ImmediateBackend(text="primary"), workspace_root=tmp_path)
    child1 = Agent(backend=_ImmediateBackend(text="child1-result"), workspace_root=tmp_path)
    child2 = Agent(backend=_ImmediateBackend(text="child2-result"), workspace_root=tmp_path)

    coordinator = Coordinator(agents=[primary, child1, child2])
    results = await coordinator.run_children_concurrent(["task1", "task2"], start_index=1)

    assert len(results) == 2
    assert results[0].summary == "child1-result"
    assert results[1].summary == "child2-result"
    assert all(r.status == "completed" for r in results)


@pytest.mark.asyncio
async def test_run_children_concurrent_is_faster_than_serial(tmp_path: Path) -> None:
    """
    并发执行多个子 agent 的总耗时 MUST 显著小于串行执行的总耗时。

    场景：3 个子 agent 各需 50ms，串行耗时 ~150ms，并发耗时应 ~50ms。
    """
    import time

    primary = Agent(backend=_ImmediateBackend(), workspace_root=tmp_path)
    children = [
        Agent(backend=_DelayedBackend(delay=0.05, text=f"child{i}"), workspace_root=tmp_path)
        for i in range(3)
    ]
    coordinator = Coordinator(agents=[primary, *children])

    start = time.monotonic()
    results = await coordinator.run_children_concurrent(
        ["t1", "t2", "t3"], start_index=1
    )
    elapsed = time.monotonic() - start

    assert len(results) == 3
    # 并发耗时应 < 3 * 50ms / 2（即 < 75ms），而不是串行的 ~150ms
    assert elapsed < 0.120, (
        f"并发执行耗时 {elapsed*1000:.1f}ms，超过预期上限 120ms（3 个 50ms 任务应并发完成）"
    )


@pytest.mark.asyncio
async def test_run_children_concurrent_raises_on_empty_tasks(tmp_path: Path) -> None:
    """child_tasks 为空时 MUST 抛出 ValueError。"""
    primary = Agent(backend=_ImmediateBackend(), workspace_root=tmp_path)
    coordinator = Coordinator(agents=[primary])

    with pytest.raises(ValueError, match="child_tasks"):
        await coordinator.run_children_concurrent([])


@pytest.mark.asyncio
async def test_run_children_concurrent_raises_on_insufficient_agents(tmp_path: Path) -> None:
    """当 agents 数量不足以满足 child_tasks 时 MUST 抛出 ValueError。"""
    primary = Agent(backend=_ImmediateBackend(), workspace_root=tmp_path)
    only_one_child = Agent(backend=_ImmediateBackend(), workspace_root=tmp_path)
    coordinator = Coordinator(agents=[primary, only_one_child])

    with pytest.raises(ValueError, match="agent 数量不足"):
        await coordinator.run_children_concurrent(["t1", "t2"])  # 需要 agents[1] 和 agents[2]，但只有 agents[1]

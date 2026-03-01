"""
同步/异步 stream 适配器（从 core.agent_loop 拆出）。

目标：
- 保持对外 API 行为不变；
- 将 run/run_stream/run_stream_async 的“桥接逻辑”从核心 loop 中剥离。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Protocol

from skills_runtime.core.contracts import AgentEvent


class _HasRunStreamAsync(Protocol):
    """最小协议：支持 `_run_stream_async` 的 loop 对象。"""
    async def _run_stream_async(  # pragma: no cover - 仅作静态约束
        self,
        task: str,
        *,
        run_id: Optional[str],
        initial_history: Optional[List[Dict[str, Any]]],
        emit,
    ) -> None:
        """执行 async loop，并通过 emit 回调实时输出事件。"""
        ...


@dataclass(frozen=True)
class RunSyncSummary:
    """同步 run 的终态摘要（对外由 RunResult 再封装）。"""
    status: str
    final_output: str
    wal_locator: str


def run_sync(
    loop: _HasRunStreamAsync,
    task: str,
    *,
    run_id: Optional[str],
    initial_history: Optional[List[Dict[str, Any]]],
) -> RunSyncSummary:
    """同步运行任务并返回汇总信息（由调用方封装成 RunResult）。"""

    final_output = ""
    wal_locator = ""
    status = "completed"
    for ev in run_stream_sync(loop, task, run_id=run_id, initial_history=initial_history):
        if ev.type == "run_completed":
            final_output = str(ev.payload.get("final_output") or "")
            wal_locator = str(ev.payload.get("wal_locator") or "")
            status = "completed"
        if ev.type == "run_failed":
            final_output = str(ev.payload.get("message") or "")
            wal_locator = str(ev.payload.get("wal_locator") or wal_locator or "")
            status = "failed"
        if ev.type == "run_cancelled":
            final_output = str(ev.payload.get("message") or "")
            wal_locator = str(ev.payload.get("wal_locator") or wal_locator or "")
            status = "cancelled"
    return RunSyncSummary(status=status, final_output=final_output, wal_locator=wal_locator)


def run_stream_sync(
    loop: _HasRunStreamAsync,
    task: str,
    *,
    run_id: Optional[str],
    initial_history: Optional[List[Dict[str, Any]]],
) -> Iterator[AgentEvent]:
    """
    同步事件流接口（Iterator[AgentEvent]）。

    实现方式：
    - 在后台线程运行 async loop
    - 通过线程安全队列把事件传回当前线程
    """

    import queue

    q: "queue.Queue[Optional[AgentEvent]]" = queue.Queue()
    err_q: "queue.Queue[BaseException]" = queue.Queue()

    def _worker() -> None:
        """后台线程入口：运行 async loop 并把事件写入线程安全队列。"""

        try:
            asyncio.run(
                loop._run_stream_async(
                    task,
                    run_id=run_id,
                    initial_history=initial_history,
                    emit=lambda e: q.put(e),
                )
            )
        except BaseException as e:  # pragma: no cover（线程内异常兜底）
            err_q.put(e)
        finally:
            q.put(None)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev

    if not err_q.empty():
        raise err_q.get()


async def run_stream_async_iter(
    loop: _HasRunStreamAsync,
    task: str,
    *,
    run_id: Optional[str],
    initial_history: Optional[List[Dict[str, Any]]],
) -> AsyncIterator[AgentEvent]:
    """
    异步事件流接口（给 Web/SSE 适配层使用）。

    约束（生产化补齐）：
    - 必须是真正的 streaming：事件产生即 yield，不得“缓冲到结束再一次性输出”。
    """

    q: "asyncio.Queue[AgentEvent | None]" = asyncio.Queue()

    def _emit(e: AgentEvent) -> None:
        """把事件写入 asyncio queue（非阻塞）。"""

        try:
            q.put_nowait(e)
        except Exception:
            # 防御性兜底：asyncio.QueueFull 或其他队列异常不应杀死 run；可能导致上层丢事件。
            pass

    async def _runner() -> None:
        """后台任务：执行核心 loop 并把事件推入 queue。"""

        try:
            await loop._run_stream_async(task, run_id=run_id, initial_history=initial_history, emit=_emit)
        finally:
            with contextlib.suppress(Exception):
                q.put_nowait(None)

    t = asyncio.create_task(_runner())
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            yield item
    finally:
        if not t.done():
            t.cancel()
            with contextlib.suppress(BaseException):
                await asyncio.gather(t, return_exceptions=True)


__all__ = ["RunSyncSummary", "run_sync", "run_stream_sync", "run_stream_async_iter"]

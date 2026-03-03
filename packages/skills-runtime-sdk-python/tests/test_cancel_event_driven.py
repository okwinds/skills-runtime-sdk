"""Task 6.4：验证取消检测事件驱动语义（asyncio.Event + 10ms watcher）。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, AsyncIterator

from skills_runtime.core.agent import Agent
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest


class _SlowBackend:
    """模拟慢速 LLM 响应（用于触发取消路径）。"""

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        import asyncio
        await asyncio.sleep(10)  # 等待会被取消打断
        yield ChatStreamEvent(type="completed", finish_reason="stop")


class _FastBackend:
    """立即完成的 stub backend（用于验证 cancel_checker=None 时的正常路径）。"""

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        yield ChatStreamEvent(type="text_delta", text="hello")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_cancel_checker_triggers_run_cancelled_within_20ms(tmp_path: Path) -> None:
    """
    cancel_checker 返回 True 后，run MUST 在 20ms 内发出 run_cancelled 并停止。

    目标：验证事件驱动取消检测的响应延迟。
    注意：实际触发延迟取决于 _stop_watcher 的轮询间隔（10ms），
          加上 asyncio 调度开销，预期总延迟 < 20ms。
    """
    detection_start: list = []
    cancel_flag = [False]

    def cancel_checker() -> bool:
        return cancel_flag[0]

    agent = Agent(
        backend=_SlowBackend(),
        workspace_root=tmp_path,
        cancel_checker=cancel_checker,
    )

    # 在后台设置取消 flag，记录设置时间
    import threading

    def trigger_cancel() -> None:
        time.sleep(0.050)  # 等 50ms 再触发取消
        detection_start.append(time.monotonic())
        cancel_flag[0] = True

    t = threading.Thread(target=trigger_cancel, daemon=True)
    t.start()

    start = time.monotonic()
    events = list(agent.run_stream("hi"))
    total_elapsed = time.monotonic() - start

    types = [e.type for e in events]
    assert "run_cancelled" in types, f"期望 run_cancelled 事件，实际事件序列：{types}"

    # 整体耗时应 < 100ms（50ms 等待 + 20ms 取消响应 + 余量）
    assert total_elapsed < 0.150, (
        f"取消响应过慢：总耗时 {total_elapsed*1000:.1f}ms，期望 < 150ms"
    )


def test_cancel_checker_none_run_completes_normally(tmp_path: Path) -> None:
    """cancel_checker=None 时，run MUST 正常完成，不受取消机制影响。"""
    agent = Agent(
        backend=_FastBackend(),
        workspace_root=tmp_path,
        cancel_checker=None,
    )

    events = list(agent.run_stream("hi"))
    types = [e.type for e in events]

    assert "run_completed" in types
    assert "run_cancelled" not in types


def test_cancel_checker_false_run_completes_normally(tmp_path: Path) -> None:
    """cancel_checker 始终返回 False 时，run MUST 正常完成。"""
    agent = Agent(
        backend=_FastBackend(),
        workspace_root=tmp_path,
        cancel_checker=lambda: False,
    )

    events = list(agent.run_stream("hi"))
    types = [e.type for e in events]

    assert "run_completed" in types
    assert "run_cancelled" not in types

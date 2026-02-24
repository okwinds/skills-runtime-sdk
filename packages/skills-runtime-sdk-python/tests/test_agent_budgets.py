from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.llm.protocol import ChatRequest
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.tools.protocol import ToolCall


class _SleepingBackend:
    """
    人为制造 “wall time 预算超时” 的 backend。

    说明：
    - 通过 sleep 模拟网络/模型卡顿；
    - Agent 需要在等待 streaming 输出时也能检查 wall time budget。
    """

    def __init__(self, *, sleep_sec: float) -> None:
        self._sleep_sec = float(sleep_sec)

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        _ = request
        await asyncio.sleep(self._sleep_sec)
        yield ChatStreamEvent(type="text_delta", text="late")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_respects_max_steps_budget(tmp_path: Path) -> None:
    # 配置：只允许 1 个 step（本实现中 step=tool call 执行次数）
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("run:\n  max_steps: 1\n  max_wall_time_sec: 1800\n", encoding="utf-8")

    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")

    call1_args = {"path": "a.txt"}
    call2_args = {"path": "b.txt"}
    call1 = ToolCall(call_id="c1", name="file_read", args=call1_args, raw_arguments=json.dumps(call1_args))
    call2 = ToolCall(call_id="c2", name="file_read", args=call2_args, raw_arguments=json.dumps(call2_args))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call1], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call2], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="should-not-reach"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[cfg])
    result = agent.run("read files")

    assert result.status == "failed"

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed, "expected run_failed when max_steps budget exceeded"
    assert failed[-1].payload["error_kind"] == "budget_exceeded"


def test_agent_respects_max_wall_time_budget(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("run:\n  max_steps: 40\n  max_wall_time_sec: 1\n", encoding="utf-8")

    agent = Agent(model="fake-model", backend=_SleepingBackend(sleep_sec=1.2), workspace_root=tmp_path, config_paths=[cfg])
    result = agent.run("slow request")

    assert result.status == "failed"

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed, "expected run_failed when max_wall_time_sec budget exceeded"
    assert failed[-1].payload["error_kind"] == "budget_exceeded"

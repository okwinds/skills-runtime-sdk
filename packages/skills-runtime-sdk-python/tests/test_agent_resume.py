from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.llm.protocol import ChatRequest


class _AssertResumeSummaryBackend:
    """
    在 stream_chat(...) 被调用时，断言 messages 中存在 resume 摘要（assistant history）。

    说明：
    - Phase 2 resume 不要求逐事件重建；
    - 但必须能把 WAL 中的关键信息以摘要形式注入 initial_history（可测）。
    """

    def __init__(self, *, expected_substring: str, response_text: str) -> None:
        self._expected = expected_substring
        self._response_text = response_text

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        found = False
        for m in request.messages:
            if m.get("role") != "assistant":
                continue
            content = m.get("content")
            if isinstance(content, str) and self._expected in content:
                found = True
                break
        assert found, "expected resume summary to be injected into assistant history"

        yield ChatStreamEvent(type="text_delta", text=self._response_text)
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_resume_injects_summary_from_existing_wal(tmp_path: Path) -> None:
    run_id = "run_resume_1"

    # 第一次运行：产生可用于 resume 的 WAL
    backend1 = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="first-output"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            )
        ]
    )
    agent1 = Agent(model="fake-model", backend=backend1, workspace_root=tmp_path)
    r1 = agent1.run("task-1", run_id=run_id)
    assert r1.status == "completed"
    assert r1.final_output == "first-output"

    # 第二次运行：新进程/新实例，但使用同一 run_id，应从 WAL 注入摘要
    backend2 = _AssertResumeSummaryBackend(expected_substring="first-output", response_text="second-output")
    agent2 = Agent(model="fake-model", backend=backend2, workspace_root=tmp_path)
    r2 = agent2.run("task-2", run_id=run_id)

    assert r2.status == "completed"
    assert r2.final_output == "second-output"

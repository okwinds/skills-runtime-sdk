from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.llm.protocol import ChatRequest


class _AssertAssistantHistoryContainsBackend:
    """
    在 stream_chat(...) 被调用时，断言 messages 中存在包含目标子串的 assistant 历史消息。

    用途：
    - 回归 Coordinator 的 “child summary 回灌到主 agent initial_history” 语义
    """

    def __init__(self, *, expected_substrings: List[str], response_text: str) -> None:
        self._expected = list(expected_substrings)
        self._response_text = response_text

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        messages = request.messages
        found = False
        for m in messages:
            if m.get("role") != "assistant":
                continue
            content = m.get("content")
            if not isinstance(content, str):
                continue
            if all(s in content for s in self._expected):
                found = True
                break
        assert found, "expected child summary to be injected into assistant history"

        yield ChatStreamEvent(type="text_delta", text=self._response_text)
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_coordinator_run_with_child_injects_summary(tmp_path: Path) -> None:
    # child：输出一段可识别的 summary
    child_backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="child:plan-v1"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            )
        ]
    )
    child = Agent(model="fake-model", backend=child_backend, workspace_root=tmp_path)

    # primary：断言 messages 中包含 child summary，并返回最终输出
    primary_backend = _AssertAssistantHistoryContainsBackend(
        expected_substrings=["[ChildAgent Summary]", "wal_locator:", "summary: child:plan-v1"],
        response_text="primary:done",
    )
    primary = Agent(model="fake-model", backend=primary_backend, workspace_root=tmp_path)

    # NOTE：Coordinator 在实现前不存在；本测试用于 TDD（先红后绿）。
    from agent_sdk import Coordinator

    coord = Coordinator(agents=[primary, child])
    result = coord.run_with_child(task="do it", child_task="make a plan")

    assert result.status == "completed"
    assert result.final_output == "primary:done"

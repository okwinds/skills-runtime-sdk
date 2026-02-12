"""
Fake LLM backend（离线回归夹具）。

用途：
- 在不依赖真实模型/外网的情况下，回归 Agent Loop 的编排逻辑（tool_calls → 执行 → 回注 → 继续）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.tools.protocol import ToolSpec


@dataclass(frozen=True)
class FakeChatCall:
    """一次 chat 调用的预期输出（按顺序吐出 ChatStreamEvent）。"""

    events: List[ChatStreamEvent]


class FakeChatBackend:
    """
    用脚本化事件序列模拟 LLM streaming 输出。

    说明：
    - 每次 `stream_chat(...)` 消耗一个 `FakeChatCall`
    - 事件序列必须最终包含 `completed`（否则会被自动补齐）
    """

    def __init__(self, calls: Sequence[FakeChatCall]) -> None:
        """
        创建一个可预测的 fake backend。

        参数：
        - `calls`：预设的调用序列；每次 `stream_chat` 会消费一个条目。
        """

        self._calls = list(calls)
        self._idx = 0

    async def stream_chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        """
        按预设事件序列产出 streaming 事件。

        说明：
        - `model/messages/tools/temperature` 仅为接口兼容；Fake backend 不做真实推理。
        - 若预设序列未包含 `completed`，会在末尾自动补齐一个 `completed`。
        """

        if self._idx >= len(self._calls):
            raise ValueError("FakeChatBackend calls 已耗尽")
        call = self._calls[self._idx]
        self._idx += 1

        completed_seen = False
        for ev in call.events:
            if ev.type == "completed":
                completed_seen = True
            yield ev
        if not completed_seen:
            yield ChatStreamEvent(type="completed", finish_reason="fake_eof")

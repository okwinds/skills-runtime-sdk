"""
LLM backend（OpenAI-compatible chat.completions）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/llm-backend.md`

Phase 2 目标：
- 提供可离线测试的 SSE parser（tool_calls arguments 拼接）
- 提供 OpenAI-compatible 的请求封装（网络层在集成测试验证）
"""

from __future__ import annotations

from skills_runtime.llm.chat_sse import ChatCompletionsSseParser, ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.llm.openai_chat import OpenAIChatCompletionsBackend

__all__ = [
    "ChatCompletionsSseParser",
    "ChatStreamEvent",
    "FakeChatBackend",
    "FakeChatCall",
    "OpenAIChatCompletionsBackend",
]

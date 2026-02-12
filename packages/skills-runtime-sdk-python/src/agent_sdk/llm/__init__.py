"""
LLM backend（OpenAI-compatible chat.completions）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/llm-backend.md`

Phase 2 目标：
- 提供可离线测试的 SSE parser（tool_calls arguments 拼接）
- 提供 OpenAI-compatible 的请求封装（网络层在集成测试验证）
"""

from __future__ import annotations

from agent_sdk.llm.chat_sse import ChatCompletionsSseParser, ChatStreamEvent
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend

__all__ = ["ChatCompletionsSseParser", "ChatStreamEvent", "OpenAIChatCompletionsBackend"]


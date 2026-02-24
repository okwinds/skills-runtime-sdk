"""
LLM 协议：ChatRequest（v2 请求参数包）。

对齐 OpenSpec（本仓重构）：
- `openspec/changes/sdk-production-refactor-p0/specs/chatrequest-v2/spec.md`

设计目标：
- 用单一参数对象承载 LLM 请求信息，避免散落的关键字参数不断膨胀；
- 允许通过 `extra` 承载 provider 特有选项（保持协议签名稳定）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_sdk.tools.protocol import ToolSpec


@dataclass(frozen=True)
class ChatRequest:
    """
    ChatRequest：v2 LLM 请求参数包（最小可用 + 可扩展）。

    字段：
    - model：模型名
    - messages：OpenAI-compatible message list（role/content/tool_call_id 等形态由上层组装保证）
    - tools：可选 tools 列表
    - temperature/max_tokens/top_p/response_format：常见推理参数（可选）
    - run_id/turn_id：可选，用于下游链路追踪（日志/限流/观测）
    - extra：provider 特有扩展字段（必须可预测、可检查；即使 backend 忽略也应可传递）
    """

    model: str
    messages: List[Dict[str, Any]]
    tools: Optional[List[ToolSpec]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    response_format: Optional[Dict[str, Any]] = None

    run_id: Optional[str] = None
    turn_id: Optional[str] = None

    extra: Dict[str, Any] = field(default_factory=dict)


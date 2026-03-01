"""
LLM 协议：ChatRequest / ChatBackend。

对齐 OpenSpec（本仓重构）：
- `openspec/specs/chat-backend/spec.md`

设计目标：
- 用单一参数对象承载 LLM 请求信息，避免散落的关键字参数不断膨胀；
- 允许通过 `extra` 承载 provider 特有选项（保持协议签名稳定）。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol

from skills_runtime.tools.protocol import ToolSpec


@dataclass(frozen=True)
class ChatRequest:
    """
    ChatRequest：LLM 请求参数包（最小可用 + 可扩展）。

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


class ChatBackend(Protocol):
    """
    LLM backend 抽象（Phase 2：chat.completions streaming）。
    """

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[Any]:
        """
        唯一入口：以单一 ChatRequest 参数包承载请求信息。

        约束：
        - 返回的 item 需满足 `skills_runtime.llm.chat_sse` 的事件约定（例如 `type=text_delta/tool_calls/completed`）。
        """

        ...


def _validate_chat_backend_protocol(backend: Any) -> None:
    """
    校验 ChatBackend 协议（fail-fast）。

    约束：
    - backend 必须实现 `stream_chat(request: ChatRequest)`；
    - 不允许 legacy `stream_chat(model, messages, ...)` 签名被当作“可用协议”。

    参数：
    - backend：待校验的 backend 实例

    异常：
    - ValueError：协议不匹配（将被映射为 run_failed 的 `config_error`）
    """

    fn = getattr(backend, "stream_chat", None)
    if not callable(fn):
        raise ValueError("ChatBackend protocol mismatch: missing stream_chat(request: ChatRequest)")

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        # fail-open：无法可靠 introspect 时，至少确保可调用；实际调用失败会被 run_failed 捕获并分类
        return

    params = list(sig.parameters.values())
    if params and params[0].name in ("self", "cls"):
        params = params[1:]

    if not params:
        raise ValueError("ChatBackend.stream_chat must accept a `request` parameter")

    # 允许 request 为 positional/keyword-only，但必须存在名为 request 的参数。
    request_param = params[0]
    if request_param.name != "request":
        raise ValueError("ChatBackend.stream_chat must be stream_chat(request=...) (legacy signatures are not supported)")

    # 除 request 外，不允许出现“无默认值的额外参数”（避免误把 legacy 签名当成可用）。
    for p in params[1:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            raise ValueError("ChatBackend.stream_chat must accept only `request` (additional required params are not supported)")

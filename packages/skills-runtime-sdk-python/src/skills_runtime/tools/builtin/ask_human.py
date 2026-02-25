"""
内置工具：ask_human（Phase 2 MVP）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools.md` §8（HumanIOProvider）

说明：
- 本实现支持注入 `HumanIOProvider`：
  - 有 provider：同步获取 answer，并落盘 human_request/human_response 事件（若配置 wal）
  - 无 provider：返回 `error_kind="human_required"`（供上层决定如何进入等待态）
"""

from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


def _now_rfc3339() -> str:
    """返回当前 UTC 时间的 RFC3339 字符串（以 `Z` 结尾）。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _AskHumanArgs(BaseModel):
    """ask_human 输入参数（Phase 2 最小字段 + 可选 choices/context）。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    choices: Optional[list[str]] = None
    context: Optional[Dict[str, Any]] = None


ASK_HUMAN_SPEC = ToolSpec(
    name="ask_human",
    description="向用户提问并等待回答（需要 HumanIOProvider 适配）。",
    parameters={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "要向用户提出的问题"},
            "choices": {"type": "array", "items": {"type": "string"}, "description": "可选项（可选）"},
            "context": {"type": "object", "description": "UI 展示用上下文（可选）"},
        },
        "required": ["question"],
        "additionalProperties": False,
    },
)


def ask_human(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 ask_human。

    参数：
    - call：工具调用（args.question / args.choices / args.context）
    - ctx：执行上下文（human_io + 可选 wal）

    返回：
    - 若 ctx.human_io 存在：ok=true，details.stdout 为用户回答（同时 data.answer 提供结构化字段）
    - 若 ctx.human_io 缺失：ok=false，error_kind="human_required"
    """

    start = time.monotonic()
    try:
        args = _AskHumanArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    ctx.emit_event(
        AgentEvent(
            type="human_request",
            timestamp=_now_rfc3339(),
            run_id=ctx.run_id,
            payload={
                "call_id": call.call_id,
                "question": args.question,
                "choices": args.choices,
                "context": args.context,
            },
        )
    )

    if ctx.human_io is None:
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult.error_payload(
            error_kind="human_required",
            stderr="ask_human 需要 HumanIOProvider，但当前上下文未配置 human_io",
            data={"question": args.question},
            duration_ms=duration_ms,
        )

    answer = ctx.human_io.request_human_input(
        call_id=call.call_id,
        question=args.question,
        choices=args.choices,
        context=args.context,
        timeout_ms=None,
    )

    ctx.emit_event(
        AgentEvent(
            type="human_response",
            timestamp=_now_rfc3339(),
            run_id=ctx.run_id,
            payload={
                "call_id": call.call_id,
                "answer": answer,
            },
        )
    )

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout=answer,
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"answer": answer},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

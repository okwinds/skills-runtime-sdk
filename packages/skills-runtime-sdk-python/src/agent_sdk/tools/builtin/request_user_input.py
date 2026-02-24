"""
内置工具：request_user_input（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-plan-and-input.md`

说明：
- 本工具是“结构化 human I/O 原语”；无 human_io provider 时必须 fail-closed：human_required。
- 事件落盘语义复用 `human_request/human_response`（每题一对），以便可审计与 metrics 统计。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.core.contracts import AgentEvent
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


def _now_rfc3339() -> str:
    """返回当前 UTC 时间的 RFC3339 字符串（以 `Z` 结尾）。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _Option(BaseModel):
    """request_user_input：options 条目。"""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    description: Optional[str] = None


class _Question(BaseModel):
    """request_user_input：单个问题。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    header: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: Optional[List[_Option]] = None


class _RequestUserInputArgs(BaseModel):
    """request_user_input 输入参数（questions[]）。"""

    model_config = ConfigDict(extra="forbid")

    questions: List[_Question] = Field(min_length=1)


REQUEST_USER_INPUT_SPEC = ToolSpec(
    name="request_user_input",
    description="向用户请求结构化输入（多题/多选），并返回 answers（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "header": {"type": "string"},
                        "question": {"type": "string"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {"label": {"type": "string"}, "description": {"type": "string"}},
                                "required": ["label"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["id", "header", "question"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def request_user_input(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 request_user_input。

    参数：
    - call：工具调用（args.questions）
    - ctx：执行上下文（human_io + 可选 wal）
    """

    start = time.monotonic()
    try:
        args = _RequestUserInputArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    # 统一先发出 human_request（即使 human_io 缺失也可审计/回放）
    ctx.emit_event(
        AgentEvent(
            type="human_request",
            timestamp=_now_rfc3339(),
            run_id=ctx.run_id,
            payload={
                "call_id": call.call_id,
                "tool": "request_user_input",
                "questions": [q.model_dump() for q in args.questions],
            },
        )
    )

    if ctx.human_io is None:
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult.error_payload(
            error_kind="human_required",
            stderr="request_user_input requires HumanIOProvider but ctx.human_io is not configured",
            data={"questions_total": len(args.questions)},
            duration_ms=duration_ms,
        )

    answers: List[Dict[str, str]] = []
    for q in args.questions:
        choices: Optional[list[str]] = None
        context: Optional[Dict[str, Any]] = None
        if q.options is not None:
            if len(q.options) == 0:
                return ToolResult.error_payload(
                    error_kind="validation",
                    stderr="question.options must not be empty when provided",
                    data={"question_id": q.id},
                )
            labels = [str(o.label) for o in q.options]
            if len(set(labels)) != len(labels):
                return ToolResult.error_payload(
                    error_kind="validation",
                    stderr="question.options labels must be unique",
                    data={"question_id": q.id},
                )
            choices = labels
            context = {"options": [o.model_dump() for o in q.options]}

        answer = ctx.human_io.request_human_input(
            call_id=f"{call.call_id}:{q.id}",
            question=q.question,
            choices=choices,
            context={"header": q.header, "question_id": q.id, **(context or {})},
            timeout_ms=None,
        )
        answers.append({"id": q.id, "answer": str(answer)})

        ctx.emit_event(
            AgentEvent(
                type="human_response",
                timestamp=_now_rfc3339(),
                run_id=ctx.run_id,
                payload={"call_id": call.call_id, "question_id": q.id, "answer": str(answer)},
            )
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout="",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"answers": answers},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

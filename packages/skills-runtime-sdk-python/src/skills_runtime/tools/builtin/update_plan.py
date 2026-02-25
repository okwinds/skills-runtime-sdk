"""
内置工具：update_plan（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-plan-and-input.md`
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


def _now_rfc3339() -> str:
    """返回当前 UTC 时间的 RFC3339 字符串（以 `Z` 结尾）。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _PlanStep(BaseModel):
    """单个计划条目。"""

    model_config = ConfigDict(extra="forbid")

    step: str = Field(min_length=1, description="计划步骤（短句）")
    status: Literal["pending", "in_progress", "completed"]


class _UpdatePlanArgs(BaseModel):
    """update_plan 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    plan: List[_PlanStep] = Field(min_length=1)
    explanation: Optional[str] = None


UPDATE_PLAN_SPEC = ToolSpec(
    name="update_plan",
    description="更新任务计划（plan step/status），并发出 plan_updated 事件（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["step", "status"],
                    "additionalProperties": False,
                },
                "minItems": 1,
            },
            "explanation": {"type": "string"},
        },
        "required": ["plan"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def update_plan(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 update_plan。

    参数：
    - call：工具调用（args.plan/explanation）
    - ctx：执行上下文（可选 wal；用于 plan_updated 事件审计）
    """

    start = time.monotonic()
    try:
        args = _UpdatePlanArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    in_progress_total = sum(1 for it in args.plan if it.status == "in_progress")
    if in_progress_total > 1:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="plan must contain at most one in_progress step",
            data={"in_progress_total": in_progress_total},
        )

    plan_jsonable = [it.model_dump() for it in args.plan]
    ctx.emit_event(
        AgentEvent(
            type="plan_updated",
            timestamp=_now_rfc3339(),
            run_id=ctx.run_id,
            payload={"call_id": call.call_id, "plan": plan_jsonable, "explanation": args.explanation},
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
        data={"plan": plan_jsonable, "explanation": args.explanation},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

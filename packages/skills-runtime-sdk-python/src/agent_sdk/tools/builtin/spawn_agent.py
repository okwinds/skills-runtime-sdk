"""
内置工具：spawn_agent（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-collab.md`
"""

from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


class _SpawnAgentArgs(BaseModel):
    """spawn_agent 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, description="子 agent 初始任务文本")
    agent_type: Optional[str] = Field(default=None, description="子 agent 类型（可选）")


SPAWN_AGENT_SPEC = ToolSpec(
    name="spawn_agent",
    description="生成子 agent 并开始执行（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "子 agent 初始任务文本"},
            "agent_type": {"type": "string", "description": "子 agent 类型（可选）"},
        },
        "required": ["message"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unknown",
)


def spawn_agent(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 spawn_agent。

    约定：
    - ctx.collab_manager 需要提供 `spawn(message, agent_type) -> handle(id,status,...)`
    """

    start = time.monotonic()
    try:
        args = _SpawnAgentArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    mgr = ctx.collab_manager
    if mgr is None:
        return ToolResult.error_payload(error_kind="validation", stderr="spawn_agent requires collab_manager")

    try:
        h = mgr.spawn(message=str(args.message), agent_type=str(args.agent_type or "default"))  # type: ignore[attr-defined]
    except Exception as e:
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e))

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout="",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"id": str(h.id), "status": str(getattr(h, "status", "running"))},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

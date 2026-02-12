"""
内置工具：close_agent（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-collab.md`
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


class _CloseAgentArgs(BaseModel):
    """close_agent 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="子 agent id")


CLOSE_AGENT_SPEC = ToolSpec(
    name="close_agent",
    description="关闭/取消子 agent（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unknown",
)


def close_agent(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 close_agent。

    约定：
    - ctx.collab_manager 需要提供 `close(agent_id)`。
    """

    start = time.monotonic()
    try:
        args = _CloseAgentArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    mgr = ctx.collab_manager
    if mgr is None:
        return ToolResult.error_payload(error_kind="validation", stderr="close_agent requires collab_manager")

    try:
        mgr.close(agent_id=str(args.id))  # type: ignore[attr-defined]
    except KeyError:
        return ToolResult.error_payload(error_kind="not_found", stderr="agent not found", data={"id": str(args.id)})
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
        data={"id": str(args.id)},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

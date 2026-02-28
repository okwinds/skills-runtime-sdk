"""
内置工具：send_input（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-collab.md`
"""

from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class _SendInputArgs(BaseModel):
    """send_input 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, description="子 agent id")
    message: str = Field(min_length=1, description="投递给子 agent 的消息")
    interrupt: Optional[bool] = Field(default=None, description="是否中断（最小实现可忽略）")


SEND_INPUT_SPEC = ToolSpec(
    name="send_input",
    description="向子 agent 发送输入（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "message": {"type": "string"},
            "interrupt": {"type": "boolean"},
        },
        "required": ["id", "message"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unknown",
)


def send_input(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 send_input。

    约定：
    - ctx.collab_manager 需要提供 `send_input(agent_id, message)`。
    """

    start = time.monotonic()
    try:
        args = _SendInputArgs.model_validate(call.args)
    except Exception as e:
        # 防御性兜底：pydantic 验证失败（ValidationError 或其他）。
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    mgr = ctx.collab_manager
    if mgr is None:
        return ToolResult.error_payload(error_kind="validation", stderr="send_input requires collab_manager")

    try:
        mgr.send_input(agent_id=str(args.id), message=str(args.message))  # type: ignore[attr-defined]
    except KeyError:
        return ToolResult.error_payload(error_kind="not_found", stderr="agent not found", data={"id": str(args.id)})
    except Exception as e:
        # 防御性兜底：collab_manager 由外部注入，可能抛出任意异常。
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

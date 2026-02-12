"""
内置工具：wait（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-collab.md`

注意：
- tool 名为 `wait`，容易与 Python 关键字/stdlib 名冲突，因此模块内使用函数名 `wait_tool`。
"""

from __future__ import annotations

import time
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


class _WaitArgs(BaseModel):
    """wait 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    ids: List[str] = Field(min_length=1, description="要等待的子 agent ids")
    timeout_ms: Optional[int] = Field(default=None, ge=1, description="总超时（毫秒）")


WAIT_SPEC = ToolSpec(
    name="wait",
    description="等待子 agent 完成（可选超时）（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "timeout_ms": {"type": "integer", "minimum": 1},
        },
        "required": ["ids"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def wait_tool(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 wait。

    约定：
    - ctx.collab_manager 需要提供 `wait(ids, timeout_ms) -> handles[]`
    """

    start = time.monotonic()
    try:
        args = _WaitArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    mgr = ctx.collab_manager
    if mgr is None:
        return ToolResult.error_payload(error_kind="validation", stderr="wait requires collab_manager")

    ids = [str(i) for i in args.ids]
    try:
        handles = mgr.wait(ids=ids, timeout_ms=args.timeout_ms)  # type: ignore[attr-defined]
    except KeyError as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e), data={"ids": ids})
    except Exception as e:
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e))

    results = []
    for h in handles:
        status = str(getattr(h, "status", "unknown"))
        item = {"id": str(getattr(h, "id", "")), "status": status}
        out = getattr(h, "final_output", None)
        if out is not None and status == "completed":
            item["final_output"] = str(out)
        results.append(item)

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout="",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"results": results},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

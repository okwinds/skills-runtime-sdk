"""
内置工具：shell（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md`

说明：
- `shell` 是对 Codex 工具名的兼容层；内部复用 `shell_exec`。
- 安全门禁（approvals/sandbox）不在本 handler 内实现，而由 Agent loop 的 gate 编排。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.tools.builtin.shell_exec import shell_exec
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


class _ShellArgs(BaseModel):
    """shell 输入参数（对齐 Codex：command[] + workdir 等）。"""

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1, description="命令与参数（argv 形式）")
    workdir: Optional[str] = Field(default=None, description="工作目录（相对 workspace_root）")
    env: Optional[dict[str, str]] = Field(default=None, description="追加/覆盖的环境变量（可选）")
    timeout_ms: Optional[int] = Field(default=None, ge=1, description="超时毫秒数（可选）")
    sandbox: Optional[str] = Field(default=None, description="OS sandbox 执行策略（inherit|none|restricted）")
    sandbox_permissions: Optional[str] = Field(
        default=None, description="框架层权限语义（restricted|require_escalated）"
    )
    justification: Optional[str] = Field(default=None, description="需要审批时展示给用户的理由（可选）")


SHELL_SPEC = ToolSpec(
    name="shell",
    description="执行一条本地命令（argv 形式），并返回 stdout/stderr/exit_code（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "argv 形式命令"},
            "workdir": {"type": "string", "description": "工作目录（可选；默认 workspace_root）"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}, "description": "追加/覆盖环境变量"},
            "timeout_ms": {"type": "integer", "minimum": 1, "description": "超时毫秒数（可选）"},
            "sandbox": {"type": "string", "description": "OS sandbox 执行策略（可选）"},
            "sandbox_permissions": {"type": "string", "description": "sandbox 权限语义（可选）"},
            "justification": {"type": "string", "description": "需要审批时展示给用户的理由（可选）"},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unknown",
)


def _redact_payload(payload: Dict[str, Any], ctx: ToolExecutionContext) -> Dict[str, Any]:
    """对 stdout/stderr 做最小脱敏（best-effort）。"""

    out = dict(payload)
    if "stdout" in out and isinstance(out.get("stdout"), str):
        out["stdout"] = ctx.redact_text(out["stdout"])
    if "stderr" in out and isinstance(out.get("stderr"), str):
        out["stderr"] = ctx.redact_text(out["stderr"])
    return out


def shell(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 shell（内部复用 shell_exec）。

    参数：
    - call：工具调用（args.command/workdir/env/timeout_ms/...）
    - ctx：执行上下文（需要 Executor；否则 validation）

    返回：
    - ToolResultPayload（stdout/stderr 会做最小脱敏）
    """

    try:
        args = _ShellArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    inner_args: Dict[str, Any] = {"argv": list(args.command)}
    if args.workdir is not None:
        inner_args["cwd"] = str(args.workdir)
    if args.env is not None:
        inner_args["env"] = dict(args.env)
    if args.timeout_ms is not None:
        inner_args["timeout_ms"] = int(args.timeout_ms)
    if args.sandbox is not None:
        inner_args["sandbox"] = str(args.sandbox)
    if args.sandbox_permissions is not None:
        inner_args["sandbox_permissions"] = str(args.sandbox_permissions)
    if args.justification is not None:
        inner_args["justification"] = str(args.justification)

    inner_call = ToolCall(call_id=call.call_id, name="shell_exec", args=inner_args)
    inner_result = shell_exec(inner_call, ctx)

    # 统一脱敏：对 content/details 做 best-effort redaction
    if isinstance(inner_result.details, dict):
        redacted = _redact_payload(inner_result.details, ctx)
        payload = ToolResultPayload.model_validate(redacted)
        return ToolResult.from_payload(payload, message=inner_result.message)

    try:
        obj = json.loads(inner_result.content or "{}")
        if isinstance(obj, dict):
            redacted2 = _redact_payload(obj, ctx)
            payload2 = ToolResultPayload.model_validate(redacted2)
            return ToolResult.from_payload(payload2, message=inner_result.message)
    except Exception:
        pass

    return inner_result


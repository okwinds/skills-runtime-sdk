"""
内置工具：shell_exec（Phase 2 MVP）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools.md` §5
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class _ShellExecArgs(BaseModel):
    """shell_exec 输入参数（Phase 2 最小字段）。"""

    model_config = ConfigDict(extra="forbid")

    argv: list[str] = Field(min_length=1, description="命令与参数（argv 形式）")
    cwd: Optional[str] = Field(default=None, description="工作目录（相对 workspace_root）")
    env: Optional[dict[str, str]] = Field(default=None, description="追加/覆盖的环境变量（可选）")
    timeout_ms: Optional[int] = Field(default=None, ge=1, description="超时毫秒数（可选）")
    sandbox: Optional[str] = Field(
        default=None,
        description="OS sandbox 执行策略（可选）：inherit|none|restricted；inherit 将使用 SDK 默认策略。",
    )
    sandbox_permissions: Optional[str] = Field(
        default=None,
        description="sandbox 权限语义（可选）：restricted|require_escalated；require_escalated 必须进入审批。",
    )
    tty: bool = Field(default=False, description="是否分配 TTY（Phase 2：仅接收，不实际分配）")
    justification: Optional[str] = Field(default=None, description="需要审批时展示给用户的理由（可选）")


SHELL_EXEC_SPEC = ToolSpec(
    name="shell_exec",
    description="执行一条本地命令（argv 形式），并返回 stdout/stderr/exit_code。",
    parameters={
        "type": "object",
        "properties": {
            "argv": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "命令与参数（argv 形式），例如 [\"pytest\",\"-q\"]",
            },
            "cwd": {"type": "string", "description": "工作目录（可选；默认 workspace_root）"},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "追加/覆盖的环境变量（可选）",
            },
            "timeout_ms": {"type": "integer", "minimum": 1, "description": "超时毫秒数（可选）"},
            "sandbox": {
                "type": "string",
                "description": "OS sandbox 执行策略（可选）：inherit|none|restricted；inherit 将使用 SDK 默认策略。",
            },
            "sandbox_permissions": {
                "type": "string",
                "description": "sandbox 权限语义（可选）：restricted|require_escalated；require_escalated 必须进入审批。",
            },
            "tty": {"type": "boolean", "description": "是否分配 TTY（可选；默认 false）"},
            "justification": {"type": "string", "description": "需要审批时展示给用户的理由（可选）"},
        },
        "required": ["argv"],
        "additionalProperties": False,
    },
)


def shell_exec(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 shell_exec。

    参数：
    - call：工具调用（args 中至少包含 argv；可选 cwd/env/timeout_ms/tty/justification）
    - ctx：执行上下文（workspace_root/executor/default_timeout_ms 等）

    返回：
    - ToolResult.content：JSON 字符串（包含 stdout/stderr/exit_code/duration_ms/truncated/error_kind）
    - ToolResult.details：结构化 object（与 content 对齐，便于 WAL 检索）
    """

    if ctx.executor is None:
        return ToolResult.error_payload(error_kind="validation", stderr="shell_exec 需要 Executor，但当前上下文未配置 executor")

    try:
        args = _ShellExecArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    cwd = ctx.workspace_root
    if args.cwd is not None:
        try:
            cwd = ctx.resolve_path(args.cwd)
        except Exception as e:
            return ToolResult.error_payload(error_kind="permission", stderr=str(e))
        if not cwd.exists() or not cwd.is_dir():
            return ToolResult.error_payload(error_kind="validation", stderr=f"cwd 不存在或不是目录：{cwd}")

    timeout_ms = args.timeout_ms if args.timeout_ms is not None else ctx.default_timeout_ms
    merged_env = ctx.merged_env(args.env)
    sandbox = str(args.sandbox or "inherit").strip().lower()
    if sandbox not in ("inherit", "none", "restricted"):
        return ToolResult.error_payload(error_kind="validation", stderr=f"invalid sandbox policy: {sandbox}")

    effective_sandbox = ctx.sandbox_policy_default if sandbox == "inherit" else sandbox
    adapter_name = type(ctx.sandbox_adapter).__name__ if ctx.sandbox_adapter is not None else None
    sandbox_meta = {
        "requested": sandbox,
        "effective": effective_sandbox,
        "adapter": adapter_name,
        "active": bool(effective_sandbox == "restricted" and ctx.sandbox_adapter is not None),
    }

    argv = list(args.argv)
    exec_cwd = cwd
    if effective_sandbox == "restricted":
        if ctx.sandbox_adapter is None:
            return ToolResult.error_payload(
                error_kind="sandbox_denied",
                stderr="Sandbox is required but no sandbox adapter is configured.",
                data={"sandbox": sandbox_meta},
            )
        try:
            prepared = ctx.sandbox_adapter.prepare_shell_exec(  # type: ignore[union-attr]
                argv=argv,
                cwd=exec_cwd,
                env=merged_env,
                workspace_root=ctx.workspace_root,
            )
            argv = list(prepared.argv)
            exec_cwd = prepared.cwd
        except Exception as e:
            return ToolResult.error_payload(
                error_kind="sandbox_denied",
                stderr=str(e),
                data={"sandbox": sandbox_meta},
            )

    result = ctx.executor.run_command(
        argv,
        cwd=exec_cwd,
        env=merged_env,
        timeout_ms=timeout_ms,
        cancel_checker=ctx.cancel_checker,
    )

    payload = ToolResultPayload(
        ok=result.ok,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        duration_ms=result.duration_ms,
        truncated=result.truncated,
        data={"sandbox": sandbox_meta},
        error_kind=result.error_kind,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload, message=None if result.ok else "shell_exec 执行失败")

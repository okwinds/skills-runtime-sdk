"""
内置工具：exec_command（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md`

说明：
- 本工具启动一个 PTY-backed session，并返回 session_id（若仍在运行）。
- stdout/stderr 采用最小脱敏（best-effort），降低 secrets 意外回显风险。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class _ExecCommandArgs(BaseModel):
    """exec_command 输入参数（对齐 Codex 最小集合）。"""

    model_config = ConfigDict(extra="forbid")

    cmd: str = Field(min_length=1, description="要执行的命令（string）")
    workdir: Optional[str] = Field(default=None, description="工作目录（相对 workspace_root）")
    yield_time_ms: int = Field(default=50, ge=0, description="等待输出的时间（毫秒）")
    max_output_tokens: Optional[int] = Field(default=None, ge=1, description="最大输出 tokens（近似；可选）")
    tty: bool = Field(default=True, description="是否分配 TTY（默认 true；本实现总是 PTY）")
    sandbox: Optional[str] = Field(default=None, description="OS sandbox 执行策略（inherit|restricted）")
    sandbox_permissions: Optional[str] = Field(
        default=None, description="框架层权限语义（restricted|require_escalated）"
    )
    justification: Optional[str] = Field(default=None, description="需要审批时展示给用户的理由（可选）")


EXEC_COMMAND_SPEC = ToolSpec(
    name="exec_command",
    description="启动可交互的 exec session（PTY-backed），并返回 session_id（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "cmd": {"type": "string", "description": "要执行的命令（string）"},
            "workdir": {"type": "string", "description": "工作目录（可选；默认 workspace_root）"},
            "yield_time_ms": {"type": "integer", "minimum": 0, "description": "等待输出的时间（毫秒）"},
            "max_output_tokens": {"type": "integer", "minimum": 1, "description": "最大输出 tokens（近似；可选）"},
            "tty": {"type": "boolean", "description": "是否分配 TTY（默认 true）"},
            "sandbox": {
                "type": "string",
                "enum": ["inherit", "restricted"],
                "description": "OS sandbox 执行策略（可选）",
            },
            "sandbox_permissions": {"type": "string", "description": "sandbox 权限语义（可选）"},
            "justification": {"type": "string", "description": "需要审批时展示给用户的理由（可选）"},
        },
        "required": ["cmd"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unknown",
)


def _max_output_bytes_from_tokens(max_output_tokens: Optional[int]) -> int:
    """
    将 max_output_tokens 映射为近似 max_output_bytes（保守估算）。

    约定：
    - 1 token ≈ 4 bytes（粗略）
    - 最小 1024 bytes（避免过小导致“永远看不到输出”）
    - 最大 256KB（避免一次读取过大）
    """

    if max_output_tokens is None:
        return 64 * 1024
    b = int(max_output_tokens) * 4
    b = max(1024, b)
    return min(256 * 1024, b)


def exec_command(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 exec_command：启动 session 并返回 session_id。

    参数：
    - call：工具调用（args.cmd/workdir/yield_time_ms/...）
    - ctx：执行上下文（需要 exec_sessions；否则 validation）
    """

    start = time.monotonic()
    try:
        args = _ExecCommandArgs.model_validate(call.args)
    except Exception as e:
        # 防御性兜底：pydantic 验证失败（ValidationError 或其他）。
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    if ctx.exec_sessions is None:
        return ToolResult.error_payload(error_kind="validation", stderr="exec_command requires exec_sessions manager")

    workdir = ctx.workspace_root
    if args.workdir is not None:
        try:
            workdir = ctx.resolve_path(str(args.workdir))
        except Exception as e:  # 防御性兜底：resolve_path 可能抛出 UserError（越界）或 OSError 等。
            return ToolResult.error_payload(error_kind="permission", stderr=str(e))
        if not workdir.exists() or not workdir.is_dir():
            return ToolResult.error_payload(error_kind="validation", stderr=f"workdir is not a directory: {workdir}")

    sandbox = str(args.sandbox or "inherit").strip().lower()
    if sandbox not in ("inherit", "restricted"):
        return ToolResult.error_payload(error_kind="validation", stderr=f"invalid sandbox policy: {sandbox}")
    effective_sandbox = ctx.sandbox_policy_default if sandbox == "inherit" else sandbox
    adapter_name = type(ctx.sandbox_adapter).__name__ if ctx.sandbox_adapter is not None else None
    sandbox_meta = {
        "requested": sandbox,
        "effective": effective_sandbox,
        "adapter": adapter_name,
        "active": bool(effective_sandbox == "restricted" and ctx.sandbox_adapter is not None),
    }

    argv = ["/bin/sh", "-lc", str(args.cmd)]
    env = ctx.merged_env(None)
    exec_cwd = workdir

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
                env=env,
                workspace_root=ctx.workspace_root,
            )
            argv = list(prepared.argv)
            exec_cwd = prepared.cwd
        except Exception as e:
            # 防御性兜底：sandbox adapter 由外部注入，可能抛出任意异常。
            return ToolResult.error_payload(
                error_kind="sandbox_denied",
                stderr=str(e),
                data={"sandbox": sandbox_meta},
            )

    try:
        session = ctx.exec_sessions.spawn(argv=argv, cwd=exec_cwd, env=env, tty=bool(args.tty))  # type: ignore[union-attr]
    except (OSError, RuntimeError) as e:
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e))

    max_output_bytes = _max_output_bytes_from_tokens(args.max_output_tokens)
    try:
        wr = ctx.exec_sessions.write(  # type: ignore[union-attr]
            session_id=session.session_id,
            chars="",
            yield_time_ms=int(args.yield_time_ms),
            max_output_bytes=max_output_bytes,
        )
    except (OSError, RuntimeError) as e:
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e))

    stdout = ctx.redact_text(wr.stdout)
    duration_ms = int((time.monotonic() - start) * 1000)
    if wr.running:
        payload = ToolResultPayload(
            ok=True,
            stdout=stdout,
            stderr="",
            exit_code=None,
            duration_ms=duration_ms,
            truncated=bool(wr.truncated),
            data={"session_id": int(session.session_id), "running": True, "sandbox": sandbox_meta},
            error_kind=None,
            retryable=False,
            retry_after_ms=None,
        )
        return ToolResult.from_payload(payload)

    ok = wr.exit_code == 0
    payload2 = ToolResultPayload(
        ok=bool(ok),
        stdout=stdout,
        stderr="",
        exit_code=wr.exit_code,
        duration_ms=duration_ms,
        truncated=bool(wr.truncated),
        data={"session_id": None, "running": False, "sandbox": sandbox_meta},
        error_kind=None if ok else "exit_code",
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload2)

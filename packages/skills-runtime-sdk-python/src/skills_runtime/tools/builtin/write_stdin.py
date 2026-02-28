"""
内置工具：write_stdin（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md`
"""

from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext
from skills_runtime.tools.builtin.exec_command import _max_output_bytes_from_tokens


class _WriteStdinArgs(BaseModel):
    """write_stdin 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    session_id: int = Field(ge=1, description="exec session id")
    chars: Optional[str] = Field(default=None, description="要写入的字符（可选；为空表示轮询输出）")
    yield_time_ms: int = Field(default=50, ge=0, description="等待输出的时间（毫秒）")
    max_output_tokens: Optional[int] = Field(default=None, ge=1, description="最大输出 tokens（近似；可选）")


WRITE_STDIN_SPEC = ToolSpec(
    name="write_stdin",
    description="向 exec session 写入 stdin 并读取输出（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {
            "session_id": {"type": "integer", "minimum": 1, "description": "exec session id"},
            "chars": {"type": "string", "description": "要写入的字符（可选；为空表示轮询输出）"},
            "yield_time_ms": {"type": "integer", "minimum": 0, "description": "等待输出的时间（毫秒）"},
            "max_output_tokens": {"type": "integer", "minimum": 1, "description": "最大输出 tokens（近似；可选）"},
        },
        "required": ["session_id"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unknown",
)


def write_stdin(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 write_stdin。

    参数：
    - call：工具调用（args.session_id/chars/yield_time_ms/...）
    - ctx：执行上下文（需要 exec_sessions；否则 validation）
    """

    start = time.monotonic()
    try:
        args = _WriteStdinArgs.model_validate(call.args)
    except Exception as e:
        # 防御性兜底：pydantic 验证失败（ValidationError 或其他）。
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    if ctx.exec_sessions is None:
        return ToolResult.error_payload(error_kind="validation", stderr="write_stdin requires exec_sessions manager")

    max_output_bytes = _max_output_bytes_from_tokens(args.max_output_tokens)
    try:
        wr = ctx.exec_sessions.write(  # type: ignore[union-attr]
            session_id=int(args.session_id),
            chars=str(args.chars or ""),
            yield_time_ms=int(args.yield_time_ms),
            max_output_bytes=max_output_bytes,
        )
    except KeyError:
        return ToolResult.error_payload(
            error_kind="not_found",
            stderr="session not found",
            data={"session_id": int(args.session_id)},
        )
    except ValueError as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))
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
            data={"session_id": int(args.session_id), "running": True},
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
        data={"session_id": None, "running": False},
        error_kind=None if ok else "exit_code",
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload2)


"""
内置工具：file_read（Phase 2 MVP）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools.md` §6
"""

from __future__ import annotations

import os
import time
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class _FileReadArgs(BaseModel):
    """file_read 输入参数（Phase 2 最小字段）。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    max_bytes: Optional[int] = Field(default=None, ge=1, description="最大读取字节数（可选）")


FILE_READ_SPEC = ToolSpec(
    name="file_read",
    description="读取文本文件内容，并返回内容（可能被截断）。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要读取的文件路径（相对或绝对）"},
            "max_bytes": {"type": "integer", "minimum": 1, "description": "最大读取字节数（可选）"},
        },
        "required": ["path"],
        "additionalProperties": False,
    },
)


def _read_text_with_limit(path, *, max_bytes: int, marker: bytes) -> tuple[str, bool]:
    """
    读取文件内容并在超出 max_bytes 时进行 head+tail 截断。

    返回：
    - text：UTF-8 解码文本（非法字节替换）
    - truncated：是否发生截断
    """

    st = path.stat()
    if st.st_size <= max_bytes:
        data = path.read_bytes()
        return data.decode("utf-8", errors="replace"), False

    # head+tail：保留前后片段，满足“尾部截断 or 头部+尾部保留”的规格要求
    head_len = max_bytes // 2
    tail_len = max_bytes - head_len
    with path.open("rb") as f:
        head = f.read(head_len)
        tail = b""
        if tail_len > 0:
            try:
                f.seek(-tail_len, os.SEEK_END)
                tail = f.read(tail_len)
            except OSError:
                # 极端情况下（例如非常小的文件/不支持 seek），退化为只保留 head
                tail = b""
    data = head + marker + tail
    return data.decode("utf-8", errors="replace"), True


def file_read(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 file_read。

    参数：
    - call：工具调用（args.path / args.max_bytes）
    - ctx：执行上下文（workspace_root/max_file_bytes）

    返回：
    - ok=true：details.stdout 为读取到的文本内容（可能截断）
    - ok=false：error_kind 指示 not_found/permission/validation 等
    """

    start = time.monotonic()
    try:
        args = _FileReadArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    try:
        p = ctx.resolve_path(args.path)
    except Exception as e:
        return ToolResult.error_payload(error_kind="permission", stderr=str(e))

    if not p.exists():
        return ToolResult.error_payload(error_kind="not_found", stderr=f"文件不存在：{args.path}", data={"path": args.path})

    max_bytes = args.max_bytes if args.max_bytes is not None else ctx.max_file_bytes
    marker = b"\n...<truncated>\n"

    try:
        text, truncated = _read_text_with_limit(p, max_bytes=max_bytes, marker=marker)
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e), duration_ms=duration_ms)

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout=text,
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=truncated,
        data={"path": str(args.path)},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

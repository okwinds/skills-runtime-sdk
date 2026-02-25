"""
内置工具：file_write（Phase 2 MVP）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools.md` §7
"""

from __future__ import annotations

import time

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class _FileWriteArgs(BaseModel):
    """file_write 输入参数（Phase 2 最小字段）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    path: str
    content: str
    sandbox_permissions: str | None = Field(
        default=None,
        description="sandbox 权限语义（可选）：restricted|require_escalated；require_escalated 必须进入审批。",
    )
    justification: str | None = Field(default=None, description="需要审批时展示给用户的理由（可选）")
    create_dirs: bool = Field(
        default=True,
        validation_alias=AliasChoices("create_dirs", "mkdirs"),
        description="是否自动创建父目录（默认 true；兼容 mkdirs 别名）",
    )


FILE_WRITE_SPEC = ToolSpec(
    name="file_write",
    description="写入/创建文本文件（整体覆盖写）。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件路径（相对或绝对）"},
            "content": {"type": "string", "description": "完整文件内容（Phase 2：整体覆盖写）"},
            "sandbox_permissions": {
                "type": "string",
                "description": "sandbox 权限语义（可选）：restricted|require_escalated；require_escalated 必须进入审批。",
            },
            "justification": {"type": "string", "description": "需要审批时展示给用户的理由（可选）"},
            "create_dirs": {"type": "boolean", "description": "是否自动创建父目录（默认 true）"},
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    },
)


def file_write(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 file_write。

    参数：
    - call：工具调用（args.path / args.content / args.create_dirs）
    - ctx：执行上下文（workspace_root）

    返回：
    - ok=true：写入成功摘要写入 details.stdout
    - ok=false：error_kind 指示 permission/validation/unknown 等
    """

    start = time.monotonic()
    try:
        args = _FileWriteArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    try:
        p = ctx.resolve_path(args.path)
    except Exception as e:
        return ToolResult.error_payload(error_kind="permission", stderr=str(e))

    try:
        if args.create_dirs:
            p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.content, encoding="utf-8")
    except PermissionError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult.error_payload(error_kind="permission", stderr=str(e), duration_ms=duration_ms)
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e), duration_ms=duration_ms)

    duration_ms = int((time.monotonic() - start) * 1000)
    wrote_bytes = len(args.content.encode("utf-8"))
    payload = ToolResultPayload(
        ok=True,
        stdout=f"wrote {wrote_bytes} bytes",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"path": str(args.path), "bytes": wrote_bytes},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

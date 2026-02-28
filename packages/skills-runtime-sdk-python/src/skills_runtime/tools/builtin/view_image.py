"""
内置工具：view_image（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-web-and-image.md`
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext

_MAX_IMAGE_BYTES = 5 * 1024 * 1024


class _ViewImageArgs(BaseModel):
    """view_image 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, description="图片路径（必须在 workspace_root 内）")


VIEW_IMAGE_SPEC = ToolSpec(
    name="view_image",
    description="读取本地图片并返回 base64（Codex parity）。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "图片路径（必须在 workspace_root 内）"}},
        "required": ["path"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def _guess_mime(path: Path) -> str:
    """
    根据文件扩展名推断 mime 类型（最小集合）。

    参数：
    - path：文件路径

    返回：
    - str：mime 类型字符串；未知扩展名返回 `application/octet-stream`
    """

    suf = path.suffix.lower()
    if suf in {".png"}:
        return "image/png"
    if suf in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suf in {".gif"}:
        return "image/gif"
    if suf in {".webp"}:
        return "image/webp"
    return "application/octet-stream"


def view_image(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 view_image。

    参数：
    - call：工具调用（args.path）
    - ctx：执行上下文（workspace_root 用于路径边界）
    """

    start = time.monotonic()
    try:
        args = _ViewImageArgs.model_validate(call.args)
    except Exception as e:
        # 防御性兜底：pydantic 验证失败（ValidationError 或其他）。
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    try:
        path = ctx.resolve_path(args.path)
    except Exception as e:
        # 防御性兜底：resolve_path 可能抛出 UserError（越界）或 OSError 等。
        return ToolResult.error_payload(error_kind="permission", stderr=str(e))

    if not path.exists():
        return ToolResult.error_payload(
            error_kind="not_found",
            stderr="image not found",
            data={"path": str(args.path)},
        )
    if not path.is_file():
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="path must be a file",
            data={"path": str(path)},
        )

    try:
        raw = path.read_bytes()
    except OSError as e:
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e), data={"path": str(path)})

    if len(raw) > _MAX_IMAGE_BYTES:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="image exceeds max bytes",
            data={"path": str(path), "bytes": len(raw), "max_bytes": _MAX_IMAGE_BYTES},
        )

    b64 = base64.b64encode(raw).decode("ascii")
    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout="",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"path": str(path), "mime": _guess_mime(path), "base64": b64, "bytes": len(raw)},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

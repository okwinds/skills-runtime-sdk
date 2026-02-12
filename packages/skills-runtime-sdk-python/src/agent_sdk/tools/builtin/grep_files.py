"""
内置工具：grep_files（Phase 4）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-standard-library.md`（工具标准库）
- 参考 Codex：`../codex/docs/workdocjcl/spec/05_Integrations/TOOLS_DETAILED/grep_files.md`

语义：
- 返回“包含匹配的文件路径列表”，不返回逐行匹配内容。
  - 逐行内容由 `file_read` 再读取（更可控，避免输出过大）。
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


class _GrepFilesArgs(BaseModel):
    """grep_files 输入参数（Phase 4 最小字段）。"""

    model_config = ConfigDict(extra="forbid")

    pattern: str
    path: Optional[str] = None
    include: Optional[str] = None
    limit: int = Field(default=100, ge=1, description="最多返回的匹配文件数")


GREP_FILES_SPEC = ToolSpec(
    name="grep_files",
    description="在指定路径下搜索 pattern，返回“包含匹配的文件路径列表”（不返回逐行内容）。",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则/搜索模式（trim 后不能为空）"},
            "path": {"type": "string", "description": "搜索根路径（可选；默认 workspace_root）"},
            "include": {"type": "string", "description": "glob 过滤（可选，例如 \"*.md\"）"},
            "limit": {"type": "integer", "minimum": 1, "description": "最多返回的匹配文件数（可选；默认 100）"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def _is_hidden_rel(rel: Path) -> bool:
    """判断相对路径任一组件是否以 '.' 开头。"""

    for part in rel.parts:
        if part.startswith("."):
            return True
    return False


def _looks_binary_prefix(data: bytes) -> bool:
    """用极轻量启发式判断二进制文件（含 NUL 字节）。"""

    return b"\x00" in data


def grep_files(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 grep_files。

    参数：
    - call：工具调用（args.pattern / args.path / args.include / args.limit）
    - ctx：执行上下文（workspace_root；用于路径边界校验）

    返回：
    - ok=true：data.files 为绝对路径列表；stdout 为多行文本（可读）
    - ok=false：error_kind 指示 permission/validation/unknown
    """

    start = time.monotonic()
    try:
        args = _GrepFilesArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    pattern = str(args.pattern or "").strip()
    if not pattern:
        return ToolResult.error_payload(error_kind="validation", stderr="pattern must be non-empty")

    include = args.include
    if isinstance(include, str) and not include.strip():
        include = None

    try:
        regex = re.compile(pattern)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=f"invalid pattern regex: {e}")

    root = ctx.workspace_root
    if args.path is not None:
        try:
            root = ctx.resolve_path(args.path)
        except Exception as e:
            return ToolResult.error_payload(error_kind="permission", stderr=str(e))

    if root.exists() and root.is_file():
        # 单文件模式：只检查该文件
        roots = [(root.parent, [root.name])]
    else:
        roots = []
        if not root.exists():
            # 根不存在：视为无匹配（不当作错误，便于产品层决定提示口径）
            duration_ms = int((time.monotonic() - start) * 1000)
            payload = ToolResultPayload(
                ok=True,
                stdout="No matches found.\n",
                stderr="",
                exit_code=0,
                duration_ms=duration_ms,
                truncated=False,
                data={"files": []},
                error_kind=None,
                retryable=False,
                retry_after_ms=None,
            )
            return ToolResult.from_payload(payload)
        if not root.is_dir():
            return ToolResult.error_payload(error_kind="validation", stderr="path must be a directory or a file")
        roots = [(root, None)]

    matches: list[str] = []
    truncated = False
    limit = int(args.limit)

    for walk_root, files_override in roots:
        if files_override is not None:
            # 固定文件列表（单文件模式）
            to_iter = [(walk_root, [], files_override)]
        else:
            to_iter = os.walk(walk_root, topdown=True, followlinks=False)

        for dirpath, dirnames, filenames in to_iter:
            # 过滤隐藏目录（避免深入）
            try:
                rel_dir = Path(dirpath).relative_to(root)
            except Exception:
                rel_dir = Path(".")
            if _is_hidden_rel(rel_dir) and str(rel_dir) != ".":
                if isinstance(dirnames, list):
                    dirnames[:] = []
                continue

            for fname in filenames:
                fpath = Path(dirpath) / fname
                try:
                    rel = fpath.relative_to(root)
                except Exception:
                    continue
                if _is_hidden_rel(rel):
                    continue

                rel_posix = rel.as_posix()
                if include is not None and not fnmatch.fnmatch(rel_posix, include):
                    continue

                try:
                    with fpath.open("rb") as f:
                        head = f.read(1024)
                        if _looks_binary_prefix(head):
                            continue
                        data = head + f.read()
                    text = data.decode("utf-8", errors="replace")
                except Exception:
                    continue

                if regex.search(text) is None:
                    continue
                matches.append(str(fpath.resolve()))
                if len(matches) >= limit:
                    truncated = True
                    break

            if truncated:
                break
        if truncated:
            break

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout = "No matches found.\n" if not matches else "\n".join(matches) + "\n"
    payload = ToolResultPayload(
        ok=True,
        stdout=stdout,
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=truncated,
        data={"files": matches, "pattern": pattern, "root": str(root), "include": include, "limit": limit},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

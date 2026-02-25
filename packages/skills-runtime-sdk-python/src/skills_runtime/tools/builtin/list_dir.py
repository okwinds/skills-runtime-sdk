"""
内置工具：list_dir（Phase 4）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-standard-library.md`（工具标准库）
- 参考 Codex：`../codex/docs/workdocjcl/spec/05_Integrations/TOOLS_DETAILED/list_dir.md`

说明：
- 本实现以“可回归 + 安全边界”为优先：只允许列出 workspace_root 内的目录。
- 默认不跟随 symlink 递归（避免隐式越界）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class _ListDirArgs(BaseModel):
    """list_dir 输入参数（Phase 4 最小字段）。"""

    model_config = ConfigDict(extra="forbid")

    dir_path: str
    depth: int = Field(default=2, ge=1, description="递归深度（>=1）")
    offset: int = Field(default=1, ge=1, description="1-indexed 起始条目序号（>=1）")
    limit: int = Field(default=25, ge=1, description="返回条目数上限（>=1）")


LIST_DIR_SPEC = ToolSpec(
    name="list_dir",
    description="列出目录条目（可递归到指定深度），并返回多行文本与结构化 entries。",
    parameters={
        "type": "object",
        "properties": {
            "dir_path": {"type": "string", "description": "要列出的目录路径（必须在 workspace_root 内）"},
            "depth": {"type": "integer", "minimum": 1, "description": "递归深度（可选；默认 2）"},
            "offset": {"type": "integer", "minimum": 1, "description": "1-indexed 起始条目序号（可选；默认 1）"},
            "limit": {"type": "integer", "minimum": 1, "description": "返回条目数上限（可选；默认 25）"},
        },
        "required": ["dir_path"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


@dataclass(frozen=True)
class _Entry:
    """目录条目（结构化输出）。"""

    rel_path: str
    abs_path: str
    type: str  # file|dir|symlink|other

    def to_dict(self) -> dict[str, str]:
        """转成 JSONable dict。"""

        return {"rel_path": self.rel_path, "abs_path": self.abs_path, "type": self.type}


def _is_dot_entry(path: Path) -> bool:
    """判断路径任一组件是否以 '.' 开头（用于忽略隐藏条目）。"""

    for part in path.parts:
        if part.startswith("."):
            return True
    return False


def _collect_entries(*, root: Path, depth: int) -> list[_Entry]:
    """
    BFS 收集目录条目（不跟随 symlink 递归）。

    参数：
    - root：起始目录（绝对路径，且在 workspace_root 内）
    - depth：递归深度（>=1）

    返回：
    - _Entry 列表（未切片；稳定排序由调用方负责）
    """

    out: list[_Entry] = []
    queue: list[tuple[Path, int]] = [(root, 1)]
    while queue:
        current, current_depth = queue.pop(0)
        try:
            children = list(current.iterdir())
        except Exception:
            continue

        for child in children:
            try:
                rel = child.relative_to(root)
            except Exception:
                continue
            if _is_dot_entry(rel):
                continue

            rel_str = rel.as_posix()
            typ = "other"
            try:
                if child.is_symlink():
                    typ = "symlink"
                elif child.is_dir():
                    typ = "dir"
                elif child.is_file():
                    typ = "file"
            except Exception:
                typ = "other"

            out.append(_Entry(rel_path=rel_str, abs_path=str(child.resolve()), type=typ))

            # 递归：仅对真实 dir，且不跟随 symlink dir
            if typ == "dir" and current_depth < depth:
                queue.append((child, current_depth + 1))
    out.sort(key=lambda e: e.rel_path.replace("\\", "/"))
    return out


def list_dir(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 list_dir。

    参数：
    - call：工具调用（args.dir_path / args.depth / args.offset / args.limit）
    - ctx：执行上下文（workspace_root；用于路径边界校验）

    返回：
    - ok=true：data.entries 为结构化条目；stdout 为多行文本
    - ok=false：error_kind 指示 permission/not_found/validation/unknown
    """

    start = time.monotonic()
    try:
        args = _ListDirArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    try:
        root = ctx.resolve_path(args.dir_path)
    except Exception as e:
        return ToolResult.error_payload(error_kind="permission", stderr=str(e))

    if not root.exists():
        return ToolResult.error_payload(error_kind="not_found", stderr=f"目录不存在：{args.dir_path}", data={"dir_path": args.dir_path})
    if not root.is_dir():
        return ToolResult.error_payload(error_kind="validation", stderr=f"dir_path 不是目录：{args.dir_path}", data={"dir_path": args.dir_path})

    entries = _collect_entries(root=root, depth=int(args.depth))
    total = len(entries)

    offset0 = int(args.offset) - 1
    limit = int(args.limit)
    if offset0 >= total and total > 0:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="offset exceeds directory entry count",
            data={"dir_path": str(root), "offset": int(args.offset), "total": total},
        )
    if total == 0 and int(args.offset) != 1:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="offset exceeds directory entry count",
            data={"dir_path": str(root), "offset": int(args.offset), "total": total},
        )

    sliced = entries[offset0 : offset0 + limit]
    truncated = (offset0 + limit) < total

    lines: list[str] = [f"Absolute path: {root}"]
    for e in sliced:
        suffix = ""
        if e.type == "dir":
            suffix = "/"
        elif e.type == "symlink":
            suffix = "@"
        elif e.type == "other":
            suffix = "?"
        lines.append(f"{e.rel_path}{suffix}")
    if truncated:
        lines.append(f"More than {limit} entries found")

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout="\n".join(lines) + "\n",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=truncated,
        data={
            "dir_path": str(root),
            "depth": int(args.depth),
            "offset": int(args.offset),
            "limit": int(args.limit),
            "total": total,
            "entries": [e.to_dict() for e in sliced],
        },
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)


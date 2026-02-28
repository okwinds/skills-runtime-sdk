"""
内置工具：apply_patch（Phase 4）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-standard-library.md`（工具标准库）
- 参考 Codex：
  - `../codex/docs/workdocjcl/spec/05_Integrations/TOOLS_DETAILED/apply_patch.function.md`

Patch 格式：
- 使用 Codex 风格的“文件级补丁文本”：
  - `*** Begin Patch` / `*** End Patch`
  - `*** Add File:` / `*** Update File:` / `*** Delete File:`
  - 可选：`*** Move to:`
  - Update hunks 采用 `@@` 分段，行前缀：` `（context）/`-`（delete）/`+`（add）

安全边界：
- 所有路径必须位于 workspace_root 下（通过 `ctx.resolve_path` 强制）。
- Add File / Move to 目标禁止覆盖已有文件。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class _ApplyPatchArgs(BaseModel):
    """apply_patch 输入参数（function 形态；Phase 4 最小字段）。"""

    model_config = ConfigDict(extra="forbid")

    input: str = Field(description="完整 patch 文本（包含 *** Begin Patch / *** End Patch）")


APPLY_PATCH_SPEC = ToolSpec(
    name="apply_patch",
    description="对工作区文件应用补丁（patch），并返回变更摘要。",
    parameters={
        "type": "object",
        "properties": {"input": {"type": "string", "description": "完整 patch 文本"}},
        "required": ["input"],
        "additionalProperties": False,
    },
    requires_approval=True,
    idempotency="unsafe",
)


@dataclass(frozen=True)
class _Change:
    """结构化变更（用于审计输出）。"""

    kind: str  # add|update|delete|move
    path: str
    moved_to: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """转成 JSONable dict。"""

        obj: Dict[str, Any] = {"kind": self.kind, "path": self.path}
        if self.moved_to is not None:
            obj["moved_to"] = self.moved_to
        return obj


def _split_lines(text: str) -> List[str]:
    """把文本拆成行（不保留行尾换行符）。"""

    return (text or "").splitlines()


def _find_subsequence(haystack: List[str], needle: List[str], *, start: int = 0) -> Optional[int]:
    """
    在 haystack 中寻找 needle 子序列首次出现的位置。

    参数：
    - haystack：目标行列表
    - needle：要匹配的行列表（必须非空）
    - start：起始搜索位置

    返回：
    - index（int）或 None（未找到）
    """

    if not needle:
        return None
    n = len(needle)
    for i in range(start, len(haystack) - n + 1):
        if haystack[i : i + n] == needle:
            return i
    return None


def _apply_hunk(*, file_lines: List[str], hunk_lines: List[str], search_from: int) -> Tuple[List[str], int]:
    """
    将一个 hunk 应用到 file_lines 上。

    参数：
    - file_lines：原始文件行（无行尾换行符）
    - hunk_lines：前缀为 ' ' / '-' / '+' 的行
    - search_from：从该位置开始寻找匹配片段（用于多 hunk 顺序应用）

    返回：
    - (new_lines, next_search_from)

    异常：
    - ValueError：hunk 无法匹配
    """

    before: List[str] = []
    after: List[str] = []
    for raw in hunk_lines:
        if not raw:
            raise ValueError("invalid hunk line: empty")
        tag = raw[0]
        text = raw[1:]
        if tag in (" ", "-"):
            before.append(text)
        if tag in (" ", "+"):
            after.append(text)

    if not before:
        raise ValueError("unsupported hunk: empty match block")

    idx = _find_subsequence(file_lines, before, start=search_from)
    if idx is None:
        raise ValueError("hunk does not apply (context not found)")

    new_lines = list(file_lines[:idx]) + after + list(file_lines[idx + len(before) :])
    next_from = idx + len(after)
    return new_lines, next_from


def _parse_patch_sections(lines: List[str]) -> List[Tuple[str, str, List[str]]]:
    """
    解析 patch 为 sections：[(op, path, body_lines)]。

    op：
    - "add" / "update" / "delete"

    body_lines：
    - add：必须全部以 '+' 开头（内容行）
    - update：包含可选 move 行、hunk header（@@）与 hunk 行
    - delete：应为空
    """

    if not lines or lines[0].strip() != "*** Begin Patch":
        raise ValueError("missing *** Begin Patch")
    if "*** End Patch" not in lines:
        raise ValueError("missing *** End Patch")

    # 取 Begin/End 之间
    try:
        end_idx = lines.index("*** End Patch")
    except ValueError as e:  # pragma: no cover
        raise ValueError("missing *** End Patch") from e
    body = lines[1:end_idx]

    def _is_next_section_header(s: str) -> bool:
        """判断是否是下一个 file section 的 header 行（不包含 Move to）。"""

        return s.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: "))

    sections: List[Tuple[str, str, List[str]]] = []
    i = 0
    while i < len(body):
        line = body[i].rstrip("\n")
        if not line:
            i += 1
            continue
        if line.startswith("*** Add File: "):
            path = line[len("*** Add File: ") :].strip()
            i += 1
            content_lines: List[str] = []
            while i < len(body) and not _is_next_section_header(body[i]):
                content_lines.append(body[i])
                i += 1
            sections.append(("add", path, content_lines))
            continue
        if line.startswith("*** Update File: "):
            path = line[len("*** Update File: ") :].strip()
            i += 1
            content_lines = []
            while i < len(body) and not _is_next_section_header(body[i]):
                content_lines.append(body[i])
                i += 1
            sections.append(("update", path, content_lines))
            continue
        if line.startswith("*** Delete File: "):
            path = line[len("*** Delete File: ") :].strip()
            i += 1
            content_lines = []
            while i < len(body) and not _is_next_section_header(body[i]):
                if body[i].strip():
                    content_lines.append(body[i])
                i += 1
            sections.append(("delete", path, content_lines))
            continue
        raise ValueError(f"unknown file section header: {line}")

    return sections


def _write_new_file(path: Path, *, content_lines: List[str]) -> None:
    """按 Add File 语义写入新文件（不允许覆盖）。"""

    if path.exists():
        raise FileExistsError(f"file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)

    for raw in content_lines:
        if not raw.startswith("+"):
            raise ValueError("Add File content lines must start with '+'")
    text = "\n".join(line[1:] for line in content_lines) + "\n"
    path.write_text(text, encoding="utf-8")


def _delete_file(path: Path) -> None:
    """按 Delete File 语义删除文件。"""

    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"path is a directory: {path}")
    path.unlink()


def _apply_update(path: Path, *, update_lines: List[str], ctx: ToolExecutionContext) -> Tuple[Optional[Path], List[str]]:
    """
    应用 Update File 语义（包含 hunks + 可选 Move to）。

    返回：
    - (move_to_path_or_none, applied_hunk_summaries)
    """

    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"path is a directory: {path}")

    move_to: Optional[Path] = None
    hunks: List[List[str]] = []
    current_hunk: List[str] = []
    summaries: List[str] = []

    for raw in update_lines:
        if raw.startswith("*** Move to: "):
            target = raw[len("*** Move to: ") :].strip()
            move_to = ctx.resolve_path(target)
            continue
        if raw.startswith("@@"):
            if current_hunk:
                hunks.append(current_hunk)
                current_hunk = []
            continue
        if raw.startswith((" ", "+", "-")):
            current_hunk.append(raw)
            continue
        if not raw.strip():
            continue
        raise ValueError(f"invalid update line: {raw}")

    if current_hunk:
        hunks.append(current_hunk)

    original_text = path.read_text(encoding="utf-8")
    file_lines = original_text.splitlines()
    search_from = 0
    for h in hunks:
        file_lines, search_from = _apply_hunk(file_lines=file_lines, hunk_lines=h, search_from=search_from)
        summaries.append(f"hunk_applied:{len(h)}")

    new_text = "\n".join(file_lines) + ("\n" if original_text.endswith("\n") or file_lines else "")
    path.write_text(new_text, encoding="utf-8")
    return move_to, summaries


def apply_patch(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 apply_patch。

    参数：
    - call：工具调用（args.input）
    - ctx：执行上下文（workspace_root；用于路径边界校验）

    返回：
    - ok=true：stdout 为摘要；data.changes 为结构化变更
    - ok=false：error_kind 指示 permission/not_found/validation/unknown
    """

    start = time.monotonic()
    try:
        args = _ApplyPatchArgs.model_validate(call.args)
    except Exception as e:
        # 防御性兜底：pydantic 验证失败（ValidationError 或其他）。
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    try:
        sections = _parse_patch_sections(_split_lines(args.input))
    except (ValueError, IndexError) as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    changes: List[_Change] = []
    try:
        for op, raw_path, body_lines in sections:
            target = ctx.resolve_path(raw_path)
            if op == "add":
                _write_new_file(target, content_lines=body_lines)
                changes.append(_Change(kind="add", path=raw_path))
                continue
            if op == "delete":
                if body_lines:
                    raise ValueError("Delete File section must be empty")
                _delete_file(target)
                changes.append(_Change(kind="delete", path=raw_path))
                continue
            if op == "update":
                move_to, _summaries = _apply_update(target, update_lines=body_lines, ctx=ctx)
                changes.append(_Change(kind="update", path=raw_path, moved_to=str(move_to) if move_to else None))
                if move_to is not None:
                    if move_to.exists():
                        raise FileExistsError(f"move target already exists: {move_to}")
                    move_to.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(move_to)
                    changes.append(_Change(kind="move", path=raw_path, moved_to=str(move_to)))
                continue
            raise ValueError(f"unsupported op: {op}")
    except FileNotFoundError as e:
        return ToolResult.error_payload(error_kind="not_found", stderr=str(e))
    except (PermissionError,) as e:
        return ToolResult.error_payload(error_kind="permission", stderr=str(e))
    except Exception as e:
        # 防御性兜底：resolve_path 的越界属于 permission（UserError），其他为 validation。
        msg = str(e)
        if "禁止访问 workspace_root 之外的路径" in msg:
            return ToolResult.error_payload(error_kind="permission", stderr=msg)
        return ToolResult.error_payload(error_kind="validation", stderr=msg)

    duration_ms = int((time.monotonic() - start) * 1000)
    added = sum(1 for c in changes if c.kind == "add")
    updated = sum(1 for c in changes if c.kind == "update")
    deleted = sum(1 for c in changes if c.kind == "delete")
    moved = sum(1 for c in changes if c.kind == "move")
    summary = f"apply_patch ok: add={added} update={updated} delete={deleted} move={moved}"

    payload = ToolResultPayload(
        ok=True,
        stdout=summary + "\n",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"changes": [c.to_dict() for c in changes]},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

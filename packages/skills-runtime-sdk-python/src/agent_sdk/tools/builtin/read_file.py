"""
内置工具：read_file（Phase 4.2/Phase 5：Codex 兼容形态，slice + indentation）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-standard-library.md`（read_file slice + indentation 契约）
- Codex 参考：`../codex/docs/workdocjcl/spec/05_Integrations/TOOLS_DETAILED/read_file.md`
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext

_MAX_LINE_LENGTH = 500


class _ReadFileArgs(BaseModel):
    """read_file 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    file_path: str
    offset: int = Field(default=1, ge=1, description="1-indexed 起始行号（>=1）")
    limit: int = Field(default=2000, ge=1, description="返回行数上限（>=1）")
    mode: str = Field(default="slice", description="读取模式：slice/indentation")
    indentation: Optional[_ReadFileIndentationArgs] = None


class _ReadFileIndentationArgs(BaseModel):
    """read_file indentation 模式参数。"""

    model_config = ConfigDict(extra="forbid")

    anchor_line: Optional[int] = Field(default=None, ge=1, description="锚点行号（默认等于 offset）")
    max_levels: int = Field(default=4, ge=0, description="向上扩展的最大层级（0 表示不限制）")
    include_siblings: bool = Field(default=False, description="是否包含同级块")
    include_header: bool = Field(default=False, description="是否包含块 header 行")
    max_lines: Optional[int] = Field(default=None, ge=1, description="最大输出行数（默认等于 limit）")


READ_FILE_SPEC = ToolSpec(
    name="read_file",
    description="读取文件并返回带行号的多行文本（slice/indentation）。",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "文件路径（必须在 workspace_root 内）"},
            "offset": {"type": "integer", "minimum": 1, "description": "1-indexed 起始行号（默认 1）"},
            "limit": {"type": "integer", "minimum": 1, "description": "返回行数上限（默认 2000）"},
            "mode": {"type": "string", "description": "读取模式（默认 slice；支持 slice/indentation）"},
            "indentation": {
                "type": "object",
                "description": "仅 mode=indentation 时使用的块扩展参数",
                "properties": {
                    "anchor_line": {"type": "integer", "minimum": 1, "description": "锚点行号（默认等于 offset）"},
                    "max_levels": {"type": "integer", "minimum": 0, "description": "向上扩展的最大层级（默认 4）"},
                    "include_siblings": {"type": "boolean", "description": "是否包含同级块（默认 false）"},
                    "include_header": {"type": "boolean", "description": "是否包含 header 行（默认 false）"},
                    "max_lines": {"type": "integer", "minimum": 1, "description": "最大输出行数（默认等于 limit）"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def _clip_line(text: str) -> str:
    """
    将单行裁剪到可控长度（避免超长输出）。

    参数：
    - text：原始单行文本

    返回：
    - str：裁剪后的文本（超过上限时附加 "..."）
    """

    if len(text) <= _MAX_LINE_LENGTH:
        return text
    return text[:_MAX_LINE_LENGTH] + "..."


def _indent_width(raw_line: str) -> int:
    """
    计算一行的缩进宽度（用于 indentation 模式的块识别）。

    约束：
    - TAB 视为 4 个空格（display 语义）
    - 空行缩进视为 0

    参数：
    - raw_line：不含换行的单行文本

    返回：
    - int：缩进宽度（>=0）
    """

    if not raw_line.strip():
        return 0
    width = 0
    for ch in raw_line:
        if ch == " ":
            width += 1
            continue
        if ch == "\t":
            width += 4
            continue
        break
    return width


def _is_blank(raw_line: str) -> bool:
    """判断一行是否为空白行（仅含空白字符）。"""

    return not raw_line.strip()


def _next_nonblank_index(lines: list[str], start_idx: int) -> Optional[int]:
    """
    返回从 start_idx 开始向下的第一条非空行 index（含 start_idx）。

    参数：
    - lines：全文件 lines（不含换行）
    - start_idx：起始 index（0-indexed）

    返回：
    - Optional[int]：找到则返回 index；超界则返回 None
    """

    i = start_idx
    while i < len(lines):
        if not _is_blank(lines[i]):
            return i
        i += 1
    return None


def _find_header_above(lines: list[str], from_idx: int, current_indent: int) -> Optional[int]:
    """
    向上寻找“候选 header 行”（indentation 模式）。

    header 识别规则（对齐 `tools-standard-library.md`）：
    - header 行缩进严格小于 current_indent
    - header 行后紧邻的下一条非空行缩进严格大于 header 缩进

    参数：
    - lines：全文件 lines（不含换行）
    - from_idx：向上搜索的起点（包含该行，0-indexed）
    - current_indent：当前块缩进宽度（用于比较）

    返回：
    - Optional[int]：header 行 index；找不到返回 None
    """

    i = from_idx
    while i >= 0:
        if _is_blank(lines[i]):
            i -= 1
            continue
        h_indent = _indent_width(lines[i])
        if h_indent < current_indent:
            nxt = _next_nonblank_index(lines, i + 1)
            if nxt is not None and _indent_width(lines[nxt]) > h_indent:
                return i
        i -= 1
    return None


def _block_range_for_header(lines: list[str], header_idx: int) -> tuple[int, int]:
    """
    计算某个 header 的 body block 范围（不含 header 行）。

    规则：
    - body_start 固定为 header_idx+1（包含空行）
    - body_end：遇到下一条“非空且缩进 <= header 缩进”的行时停止（该行不包含在 body 中）

    返回：
    - (start_idx, end_idx)：均为 0-indexed 且 end_idx >= start_idx-1。
      当文件在 header 之后无任何行时，返回 (len(lines), len(lines)-1)（空范围）。
    """

    if header_idx + 1 >= len(lines):
        return len(lines), len(lines) - 1

    header_indent = _indent_width(lines[header_idx])
    start_idx = header_idx + 1
    end_idx = len(lines) - 1

    i = start_idx
    while i < len(lines):
        if _is_blank(lines[i]):
            i += 1
            continue
        if _indent_width(lines[i]) <= header_indent:
            end_idx = i - 1
            break
        i += 1
    return start_idx, end_idx


def _indentation_select_range(
    *,
    lines: list[str],
    anchor_idx: int,
    max_levels: int,
    include_siblings: bool,
    include_header: bool,
) -> Optional[tuple[int, int]]:
    """
    按 indentation 规则选择输出范围（0-indexed，含 header 可选）。

    参数：
    - lines：全文件 lines（不含换行）
    - anchor_idx：锚点 index（0-indexed）
    - max_levels：向上扩展最大层级；0 表示不限制
    - include_siblings：是否包含同级块
    - include_header：是否包含 header 行

    返回：
    - Optional[(start_idx, end_idx)]：找不到可用 header 时返回 None（调用方可回退到 slice）
    """

    anchor_indent = _indent_width(lines[anchor_idx])
    if anchor_indent <= 0:
        return None

    target_levels = max_levels if max_levels > 0 else 10_000
    # 为 include_siblings 预留 1 层（用于定位“selected header 的 parent header”）。
    collect_levels = target_levels + 1 if include_siblings and max_levels > 0 else target_levels

    headers: list[int] = []
    current_from = anchor_idx - 1
    current_indent = anchor_indent
    while current_from >= 0 and len(headers) < collect_levels:
        h = _find_header_above(lines, current_from, current_indent)
        if h is None:
            break
        headers.append(h)
        current_from = h - 1
        current_indent = _indent_width(lines[h])

    if not headers:
        return None

    selected_levels = min(len(headers), target_levels)
    selected_header = headers[selected_levels - 1]

    if include_siblings and max_levels > 0 and len(headers) > selected_levels:
        # 将“同级扩展”收敛为：选择 selected header 的 parent header 的 body（并按 include_header 决定是否包含 parent header 行）。
        selected_header = headers[selected_levels]

    body_start, body_end = _block_range_for_header(lines, selected_header)
    if body_end < body_start:
        # 空 body：允许只返回 header（当 include_header=true）
        if include_header:
            return selected_header, selected_header
        return None

    if include_header:
        return selected_header, body_end
    return body_start, body_end


def read_file(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 read_file（slice/indentation）。

    参数：
    - call：工具调用（args.file_path / args.offset / args.limit / args.mode / args.indentation）
    - ctx：执行上下文（workspace_root；用于路径边界校验）

    返回：
    - ok=true：stdout 为带行号文本；data 含 total_lines/offset/limit/file_path
    - ok=false：error_kind 为 validation/not_found/permission/unknown
    """

    start = time.monotonic()
    try:
        args = _ReadFileArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    mode = (args.mode or "slice").strip() or "slice"
    if mode not in {"slice", "indentation"}:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="mode is not supported",
            data={"mode": mode, "supported": ["slice", "indentation"]},
        )

    try:
        path = ctx.resolve_path(args.file_path)
    except Exception as e:
        return ToolResult.error_payload(error_kind="permission", stderr=str(e))

    if not path.exists():
        return ToolResult.error_payload(
            error_kind="not_found",
            stderr=f"file not found: {args.file_path}",
            data={"file_path": str(args.file_path)},
        )
    if path.is_dir():
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="file_path must be a file",
            data={"file_path": str(args.file_path)},
        )

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="failed to decode file as utf-8 text",
            data={"file_path": str(path)},
        )
    except Exception as e:
        return ToolResult.error_payload(error_kind="unknown", stderr=str(e), data={"file_path": str(path)})

    lines = text.splitlines()
    total_lines = len(lines)
    offset = int(args.offset)
    limit = int(args.limit)

    # 基础 offset 越界校验（对齐 slice 语义；indentation 也复用 offset 默认值）
    if total_lines == 0 and offset != 1:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="offset exceeds file length",
            data={"file_path": str(path), "offset": offset, "total_lines": total_lines},
        )
    if offset > total_lines and total_lines > 0:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="offset exceeds file length",
            data={"file_path": str(path), "offset": offset, "total_lines": total_lines},
        )

    selected_start_idx: int
    selected_end_idx: int
    max_output_lines: int = limit

    if mode == "slice":
        selected_start_idx = offset - 1
        selected_end_idx = min(total_lines, selected_start_idx + limit) - 1
    else:
        ind = args.indentation or _ReadFileIndentationArgs()
        anchor_line = int(ind.anchor_line or offset)
        if total_lines == 0:
            return ToolResult.error_payload(
                error_kind="validation",
                stderr="anchor_line exceeds file length",
                data={"file_path": str(path), "anchor_line": anchor_line, "total_lines": total_lines},
            )
        if anchor_line < 1 or anchor_line > total_lines:
            return ToolResult.error_payload(
                error_kind="validation",
                stderr="anchor_line exceeds file length",
                data={"file_path": str(path), "anchor_line": anchor_line, "total_lines": total_lines},
            )
        max_output_lines = int(ind.max_lines or limit)
        if max_output_lines <= 0:
            return ToolResult.error_payload(
                error_kind="validation",
                stderr="max_lines must be greater than zero",
                data={"file_path": str(path), "max_lines": max_output_lines},
            )

        selected = _indentation_select_range(
            lines=lines,
            anchor_idx=anchor_line - 1,
            max_levels=int(ind.max_levels),
            include_siblings=bool(ind.include_siblings),
            include_header=bool(ind.include_header),
        )
        if selected is None:
            # 无法识别可用 header（例如 anchor 在顶层、或文件形态不适合缩进块）：
            # 按 spec 允许回退为 slice（从 anchor_line 开始，最多 max_output_lines 行）。
            selected_start_idx = anchor_line - 1
            selected_end_idx = min(total_lines, selected_start_idx + max_output_lines) - 1
        else:
            selected_start_idx, selected_end_idx = selected

    if total_lines == 0:
        selected_start_idx = 0
        selected_end_idx = -1

    # 输出范围保护（max_output_lines）
    out_start = max(0, selected_start_idx)
    out_end = min(total_lines - 1, selected_end_idx)
    range_truncated = False
    if out_end < out_start:
        out_lines = []
    else:
        full_len = out_end - out_start + 1
        slice_len = min(full_len, max_output_lines)
        range_truncated = slice_len < full_len
        final_end = out_start + slice_len - 1
        out_lines = []
        for i in range(out_start, final_end + 1):
            n = i + 1
            out_lines.append(f"L{n}: {_clip_line(lines[i])}")

    if mode == "slice":
        truncated = (offset - 1 + limit) < total_lines
    else:
        truncated = range_truncated

    stdout = "\n".join(out_lines) + ("\n" if out_lines else "")
    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout=stdout,
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=truncated,
        data={
            "file_path": str(path),
            "offset": offset,
            "limit": limit,
            "total_lines": total_lines,
            "mode": mode,
        },
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

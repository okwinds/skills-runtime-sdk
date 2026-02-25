from __future__ import annotations

from pathlib import Path

import pytest

from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


def _mk_ctx(*, workspace_root: Path) -> ToolExecutionContext:
    """构造 ToolExecutionContext（grep_files 不需要 executor）。"""

    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_test",
        wal=None,
        executor=None,
        human_io=None,
        env={},
        cancel_checker=None,
        redaction_values=[],
        default_timeout_ms=123,
        max_file_bytes=10,
        sandbox_policy_default="none",
        sandbox_adapter=None,
        emit_tool_events=False,
        event_sink=None,
        skills_manager=None,
    )


def _call_grep(*, pattern: str, path: str | None = None, include: str | None = None, limit: int | None = None) -> ToolCall:
    """构造 grep_files ToolCall。"""

    args: dict = {"pattern": pattern}
    if path is not None:
        args["path"] = path
    if include is not None:
        args["include"] = include
    if limit is not None:
        args["limit"] = limit
    return ToolCall(call_id="c1", name="grep_files", args=args)


def _assert_files(result) -> list[str]:  # type: ignore[no-untyped-def]
    assert result.ok is True
    assert isinstance(result.details, dict)
    data = (result.details or {}).get("data") or {}
    assert isinstance(data, dict)
    files = data.get("files")
    assert isinstance(files, list)
    assert all(isinstance(x, str) for x in files)
    return files  # type: ignore[return-value]


def test_grep_files_validation_missing_pattern(tmp_path: Path) -> None:
    """pattern 缺失必须失败（validation）。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    ctx = _mk_ctx(workspace_root=tmp_path)
    call = ToolCall(call_id="c1", name="grep_files", args={})
    result = grep_files(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


@pytest.mark.parametrize("pattern", ["", "   "])
def test_grep_files_validation_blank_pattern(tmp_path: Path, pattern: str) -> None:
    """pattern.trim() 不能为空，否则 validation。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    ctx = _mk_ctx(workspace_root=tmp_path)
    result = grep_files(_call_grep(pattern=pattern), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


def test_grep_files_permission_escape_workspace(tmp_path: Path) -> None:
    """path 越界必须拒绝（permission）。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    ctx = _mk_ctx(workspace_root=tmp_path)
    result = grep_files(_call_grep(pattern="x", path="/"), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"


def test_grep_files_ok_no_matches_returns_empty(tmp_path: Path) -> None:
    """无匹配必须返回 ok=true 且 files=[]。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = grep_files(_call_grep(pattern="world"), ctx)
    files = _assert_files(ok)
    assert files == []


def test_grep_files_finds_matching_files(tmp_path: Path) -> None:
    """有匹配时返回包含匹配的文件路径列表。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("world\n", encoding="utf-8")
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = grep_files(_call_grep(pattern="world"), ctx)
    files = _assert_files(ok)
    assert files == [str((tmp_path / "b.txt").resolve())]


def test_grep_files_include_glob_filters(tmp_path: Path) -> None:
    """include glob 必须生效。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    (tmp_path / "a.md").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("x=1\n", encoding="utf-8")
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = grep_files(_call_grep(pattern="x=1", include="*.md"), ctx)
    files = _assert_files(ok)
    assert files == [str((tmp_path / "a.md").resolve())]


def test_grep_files_limit_stops_early(tmp_path: Path) -> None:
    """limit 必须限制返回文件数。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    for i in range(10):
        (tmp_path / f"f{i}.txt").write_text("hit\n", encoding="utf-8")
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = grep_files(_call_grep(pattern="hit", limit=3), ctx)
    files = _assert_files(ok)
    assert len(files) == 3


def test_grep_files_ignores_dot_entries_by_default(tmp_path: Path) -> None:
    """默认忽略 . 开头文件/目录（与常见 rg 行为一致）。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    (tmp_path / ".hidden.txt").write_text("hit\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("hit\n", encoding="utf-8")
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = grep_files(_call_grep(pattern="hit"), ctx)
    files = _assert_files(ok)
    assert str((tmp_path / "visible.txt").resolve()) in files
    assert str((tmp_path / ".hidden.txt").resolve()) not in files


def test_grep_files_handles_binary_like_bytes(tmp_path: Path) -> None:
    """遇到不可 UTF-8 解码内容不应崩溃（可视为不匹配）。"""

    from skills_runtime.tools.builtin.grep_files import grep_files

    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x00")
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = grep_files(_call_grep(pattern="x"), ctx)
    files = _assert_files(ok)
    assert str((tmp_path / "bin.dat").resolve()) not in files


from __future__ import annotations

from pathlib import Path

import pytest

from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


def _mk_ctx(*, workspace_root: Path) -> ToolExecutionContext:
    """构造 ToolExecutionContext（list_dir 不需要 executor）。"""

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


def _call_list_dir(*, dir_path: str, depth: int | None = None, offset: int | None = None, limit: int | None = None) -> ToolCall:
    """构造 list_dir ToolCall。"""

    args: dict = {"dir_path": dir_path}
    if depth is not None:
        args["depth"] = depth
    if offset is not None:
        args["offset"] = offset
    if limit is not None:
        args["limit"] = limit
    return ToolCall(call_id="c1", name="list_dir", args=args)


def _assert_has_entries(result) -> list[dict]:  # type: ignore[no-untyped-def]
    assert result.ok is True
    assert isinstance(result.details, dict)
    data = (result.details or {}).get("data") or {}
    assert isinstance(data, dict)
    entries = data.get("entries")
    assert isinstance(entries, list)
    assert all(isinstance(x, dict) for x in entries)
    return entries  # type: ignore[return-value]


def test_list_dir_validation_missing_dir_path(tmp_path: Path) -> None:
    """dir_path 缺失必须失败（validation）。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    ctx = _mk_ctx(workspace_root=tmp_path)
    call = ToolCall(call_id="c1", name="list_dir", args={})
    result = list_dir(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


def test_list_dir_permission_escape_workspace(tmp_path: Path) -> None:
    """dir_path 越界必须拒绝（permission）。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    ctx = _mk_ctx(workspace_root=tmp_path)
    result = list_dir(_call_list_dir(dir_path="/"), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"


def test_list_dir_not_found(tmp_path: Path) -> None:
    """目录不存在必须失败（not_found）。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    ctx = _mk_ctx(workspace_root=tmp_path)
    result = list_dir(_call_list_dir(dir_path=str((tmp_path / "missing").resolve())), ctx)
    assert result.ok is False
    assert result.error_kind == "not_found"


def test_list_dir_validation_dir_path_is_file(tmp_path: Path) -> None:
    """dir_path 指向文件必须失败（validation）。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = list_dir(_call_list_dir(dir_path=str(p.resolve())), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


@pytest.mark.parametrize("depth", [0, -1])
def test_list_dir_validation_depth(depth: int, tmp_path: Path) -> None:
    """depth 必须 >= 1，否则 validation。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    (tmp_path / "d").mkdir()
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = list_dir(_call_list_dir(dir_path=str((tmp_path / "d").resolve()), depth=depth), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


@pytest.mark.parametrize("offset", [0, -1])
def test_list_dir_validation_offset(offset: int, tmp_path: Path) -> None:
    """offset 必须 >= 1，否则 validation。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    (tmp_path / "d").mkdir()
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = list_dir(_call_list_dir(dir_path=str((tmp_path / "d").resolve()), offset=offset), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


@pytest.mark.parametrize("limit", [0, -1])
def test_list_dir_validation_limit(limit: int, tmp_path: Path) -> None:
    """limit 必须 >= 1，否则 validation。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    (tmp_path / "d").mkdir()
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = list_dir(_call_list_dir(dir_path=str((tmp_path / "d").resolve()), limit=limit), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


def test_list_dir_offset_exceeds_count_is_validation(tmp_path: Path) -> None:
    """offset 超过条目数必须失败（validation）。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = list_dir(_call_list_dir(dir_path=str(d.resolve()), offset=999), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


def test_list_dir_depth_1_lists_only_top_level(tmp_path: Path) -> None:
    """depth=1 只列顶层，不展开子目录。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("x", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "b.txt").write_text("y", encoding="utf-8")

    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = list_dir(_call_list_dir(dir_path=str(d.resolve()), depth=1), ctx)
    entries = _assert_has_entries(ok)
    rels = sorted(e.get("rel_path") for e in entries)
    assert rels == ["a.txt", "sub"]


def test_list_dir_sorted_and_marks_types(tmp_path: Path) -> None:
    """输出条目必须稳定排序，并标记 dir/symlink/file 类型。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    d = tmp_path / "d"
    d.mkdir()
    (d / "b.txt").write_text("x", encoding="utf-8")
    (d / "a").mkdir()
    (d / "c.link").symlink_to(d / "b.txt")

    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = list_dir(_call_list_dir(dir_path=str(d.resolve()), depth=2), ctx)
    entries = _assert_has_entries(ok)

    rels = [e.get("rel_path") for e in entries]
    assert rels == sorted(rels)

    by_rel = {e["rel_path"]: e for e in entries}  # type: ignore[index]
    assert by_rel["a"]["type"] == "dir"
    assert by_rel["b.txt"]["type"] == "file"
    assert by_rel["c.link"]["type"] == "symlink"


def test_list_dir_limit_truncates_and_sets_truncated(tmp_path: Path) -> None:
    """limit 生效时必须截断，并设置 truncated=true。"""

    from skills_runtime.tools.builtin.list_dir import list_dir

    d = tmp_path / "d"
    d.mkdir()
    for i in range(10):
        (d / f"f{i}.txt").write_text("x", encoding="utf-8")

    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = list_dir(_call_list_dir(dir_path=str(d.resolve()), limit=3), ctx)
    assert ok.ok is True
    assert ok.details is not None
    assert ok.details.get("truncated") is True
    data = ok.details.get("data") or {}
    assert isinstance(data, dict)
    files = data.get("entries") or []
    assert isinstance(files, list)
    assert len(files) == 3


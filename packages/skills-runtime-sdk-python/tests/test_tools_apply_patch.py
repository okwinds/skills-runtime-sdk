from __future__ import annotations

from pathlib import Path

import pytest

from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _mk_ctx(*, workspace_root: Path) -> ToolExecutionContext:
    """构造 ToolExecutionContext（apply_patch 不需要 executor）。"""

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


def _call_apply_patch(input_text: str) -> ToolCall:
    """构造 apply_patch ToolCall。"""

    return ToolCall(call_id="c1", name="apply_patch", args={"input": input_text})


def test_apply_patch_validation_missing_input(tmp_path: Path) -> None:
    """input 缺失必须失败（validation）。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    ctx = _mk_ctx(workspace_root=tmp_path)
    call = ToolCall(call_id="c1", name="apply_patch", args={})
    result = apply_patch(call, ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


def test_apply_patch_validation_missing_begin_end(tmp_path: Path) -> None:
    """缺少 Begin/End 必须失败（validation）。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    ctx = _mk_ctx(workspace_root=tmp_path)
    result = apply_patch(_call_apply_patch("hello"), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


def test_apply_patch_add_file_success(tmp_path: Path) -> None:
    """Add File：成功创建文件。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: a.txt",
            "+hello",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = apply_patch(_call_apply_patch(patch), ctx)
    assert ok.ok is True
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_apply_patch_add_file_no_overwrite(tmp_path: Path) -> None:
    """Add File：不允许覆盖已有文件（validation）。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: a.txt",
            "+hello",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = apply_patch(_call_apply_patch(patch), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "x\n"


def test_apply_patch_delete_file_success(tmp_path: Path) -> None:
    """Delete File：成功删除文件。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    patch = "\n".join(["*** Begin Patch", "*** Delete File: a.txt", "*** End Patch", ""])
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = apply_patch(_call_apply_patch(patch), ctx)
    assert ok.ok is True
    assert not (tmp_path / "a.txt").exists()


def test_apply_patch_delete_file_not_found(tmp_path: Path) -> None:
    """Delete File：文件不存在必须失败（not_found）。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    patch = "\n".join(["*** Begin Patch", "*** Delete File: a.txt", "*** End Patch", ""])
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = apply_patch(_call_apply_patch(patch), ctx)
    assert result.ok is False
    assert result.error_kind == "not_found"


def test_apply_patch_update_file_hunk_success(tmp_path: Path) -> None:
    """Update File：按 hunk 替换内容成功。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    (tmp_path / "a.txt").write_text("a\nb\nc\n", encoding="utf-8")
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: a.txt",
            "@@",
            " a",
            "-b",
            "+B",
            " c",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = apply_patch(_call_apply_patch(patch), ctx)
    assert ok.ok is True
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a\nB\nc\n"


def test_apply_patch_update_file_hunk_mismatch_is_validation(tmp_path: Path) -> None:
    """Update File：hunk 匹配失败必须 validation。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    (tmp_path / "a.txt").write_text("a\nb\nc\n", encoding="utf-8")
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: a.txt",
            "@@",
            " a",
            "-X",
            "+B",
            " c",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = apply_patch(_call_apply_patch(patch), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


def test_apply_patch_move_to_success(tmp_path: Path) -> None:
    """Move to：重命名文件成功（不允许覆盖）。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: a.txt",
            "*** Move to: b.txt",
            "@@",
            " x",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    ok = apply_patch(_call_apply_patch(patch), ctx)
    assert ok.ok is True
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "x\n"


def test_apply_patch_move_to_no_overwrite(tmp_path: Path) -> None:
    """Move to：目标已存在必须失败（validation）。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    (tmp_path / "a.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y\n", encoding="utf-8")
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: a.txt",
            "*** Move to: b.txt",
            "@@",
            " x",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = apply_patch(_call_apply_patch(patch), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "y\n"


def test_apply_patch_permission_path_escape(tmp_path: Path) -> None:
    """路径越界必须 permission。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: ../evil.txt",
            "+x",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = apply_patch(_call_apply_patch(patch), ctx)
    assert result.ok is False
    assert result.error_kind == "permission"


def test_apply_patch_rejects_unknown_file_section(tmp_path: Path) -> None:
    """未知的 file section 必须 validation。"""

    from agent_sdk.tools.builtin.apply_patch import apply_patch

    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Foo File: a.txt",
            "+x",
            "*** End Patch",
            "",
        ]
    )
    ctx = _mk_ctx(workspace_root=tmp_path)
    result = apply_patch(_call_apply_patch(patch), ctx)
    assert result.ok is False
    assert result.error_kind == "validation"


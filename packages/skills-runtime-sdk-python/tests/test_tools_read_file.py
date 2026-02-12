from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_sdk.tools.builtin.read_file import read_file
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _mk_ctx(tmp_path: Path) -> ToolExecutionContext:
    """构造 ToolExecutionContext（workspace_root=tmp_path；不写 WAL）。"""

    return ToolExecutionContext(workspace_root=tmp_path, run_id="t_read_file", emit_tool_events=False)


def _result_payload(result) -> dict:  # type: ignore[no-untyped-def]
    """从 ToolResult.content 解析 ToolResultPayload dict（测试断言用）。"""

    return json.loads(result.content)


def test_read_file_ok_basic(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("a\nb\nc\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt"}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert payload["data"]["total_lines"] == 3
    assert "L1: a" in payload["stdout"]
    assert "L3: c" in payload["stdout"]


def test_read_file_ok_offset_limit(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("a\nb\nc\nd\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt", "offset": 2, "limit": 2}), ctx
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert "L1:" not in payload["stdout"]
    assert "L2: b" in payload["stdout"]
    assert "L3: c" in payload["stdout"]
    assert "L4:" not in payload["stdout"]
    assert payload["data"]["offset"] == 2
    assert payload["data"]["limit"] == 2


def test_read_file_truncated_flag(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("a\nb\nc\nd\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt", "offset": 1, "limit": 2}), ctx
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert payload["truncated"] is True


def test_read_file_offset_exceeds_length_is_validation(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("a\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt", "offset": 2}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is False
    assert payload["error_kind"] == "validation"


def test_read_file_not_found(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "missing.txt"}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is False
    assert payload["error_kind"] == "not_found"


def test_read_file_escape_is_permission(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "../evil.txt"}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is False
    assert payload["error_kind"] == "permission"


def test_read_file_offset_must_be_ge_1(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("a\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt", "offset": 0}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is False
    assert payload["error_kind"] == "validation"


def test_read_file_limit_must_be_ge_1(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("a\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt", "limit": 0}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is False
    assert payload["error_kind"] == "validation"


def test_read_file_mode_unknown_is_validation(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("a\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt", "mode": "wat"}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is False
    assert payload["error_kind"] == "validation"


def test_read_file_long_line_is_clipped(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("x" * 2000 + "\n", encoding="utf-8")
    ctx = _mk_ctx(tmp_path)
    r = read_file(ToolCall(call_id="c1", name="read_file", args={"file_path": "a.txt"}), ctx)
    payload = _result_payload(r)
    assert payload["ok"] is True
    # 行前缀占用少量字符；这里断言总体不会把 2000 字符整行原样输出
    assert len(payload["stdout"]) < 1200


def _write_sample_python_file(tmp_path: Path) -> Path:
    p = tmp_path / "a.py"
    p.write_text(
        "\n".join(
            [
                "def foo():",
                "    x = 1",
                "    if x:",
                "        y = 2",
                "    z = 3",
                "",
                "def bar():",
                "    pass",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return p


def test_read_file_indentation_max_levels_1_includes_if_block(tmp_path: Path) -> None:
    _write_sample_python_file(tmp_path)
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(
            call_id="c1",
            name="read_file",
            args={
                "file_path": "a.py",
                "mode": "indentation",
                "offset": 4,
                "indentation": {"anchor_line": 4, "max_levels": 1, "include_header": True},
            },
        ),
        ctx,
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert "L3:     if x:" in payload["stdout"]
    assert "L4:         y = 2" in payload["stdout"]
    assert "L1:" not in payload["stdout"]


def test_read_file_indentation_max_levels_2_includes_def_block(tmp_path: Path) -> None:
    _write_sample_python_file(tmp_path)
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(
            call_id="c1",
            name="read_file",
            args={
                "file_path": "a.py",
                "mode": "indentation",
                "offset": 4,
                "indentation": {"anchor_line": 4, "max_levels": 2, "include_header": True},
            },
        ),
        ctx,
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert "L1: def foo():" in payload["stdout"]
    assert "L5:     z = 3" in payload["stdout"]
    assert "L7:" not in payload["stdout"]


def test_read_file_indentation_include_siblings_expands_to_parent_body(tmp_path: Path) -> None:
    _write_sample_python_file(tmp_path)
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(
            call_id="c1",
            name="read_file",
            args={
                "file_path": "a.py",
                "mode": "indentation",
                "offset": 4,
                "indentation": {"anchor_line": 4, "max_levels": 1, "include_siblings": True, "include_header": True},
            },
        ),
        ctx,
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert "L1: def foo():" in payload["stdout"]
    assert "L5:     z = 3" in payload["stdout"]
    assert "L7:" not in payload["stdout"]


def test_read_file_indentation_anchor_out_of_range_is_validation(tmp_path: Path) -> None:
    _write_sample_python_file(tmp_path)
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(
            call_id="c1",
            name="read_file",
            args={"file_path": "a.py", "mode": "indentation", "offset": 1, "indentation": {"anchor_line": 999}},
        ),
        ctx,
    )
    payload = _result_payload(r)
    assert payload["ok"] is False
    assert payload["error_kind"] == "validation"


def test_read_file_indentation_max_lines_truncates(tmp_path: Path) -> None:
    _write_sample_python_file(tmp_path)
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(
            call_id="c1",
            name="read_file",
            args={
                "file_path": "a.py",
                "mode": "indentation",
                "offset": 4,
                "indentation": {"anchor_line": 4, "max_levels": 2, "include_header": True, "max_lines": 2},
            },
        ),
        ctx,
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert payload["truncated"] is True
    assert payload["stdout"].count("\n") == 2


def test_read_file_indentation_top_level_falls_back_to_slice(tmp_path: Path) -> None:
    _write_sample_python_file(tmp_path)
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(
            call_id="c1",
            name="read_file",
            args={
                "file_path": "a.py",
                "mode": "indentation",
                "offset": 1,
                "indentation": {"anchor_line": 1, "max_lines": 1},
            },
        ),
        ctx,
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    assert "L1: def foo():" in payload["stdout"]
    assert "L2:" not in payload["stdout"]


def test_read_file_indentation_defaults_climb_and_omit_header(tmp_path: Path) -> None:
    _write_sample_python_file(tmp_path)
    ctx = _mk_ctx(tmp_path)
    r = read_file(
        ToolCall(call_id="c1", name="read_file", args={"file_path": "a.py", "mode": "indentation", "offset": 4}), ctx
    )
    payload = _result_payload(r)
    assert payload["ok"] is True
    # 默认 include_header=false，因此不包含 def header 行
    assert "L1:" not in payload["stdout"]
    assert "L2:     x = 1" in payload["stdout"]

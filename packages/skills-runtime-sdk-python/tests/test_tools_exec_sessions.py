from __future__ import annotations

import json
from pathlib import Path

from agent_sdk.core.exec_sessions import ExecSessionManager
from agent_sdk.tools.builtin.exec_command import exec_command
from agent_sdk.tools.builtin.write_stdin import write_stdin
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


def _mk_ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="t_exec_sessions",
        exec_sessions=ExecSessionManager(),
        emit_tool_events=False,
    )


def test_exec_command_requires_exec_sessions(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="t_exec_sessions", emit_tool_events=False)
    r = exec_command(ToolCall(call_id="c1", name="exec_command", args={"cmd": "echo 1"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_exec_command_workdir_escape_is_permission(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = exec_command(ToolCall(call_id="c1", name="exec_command", args={"cmd": "echo 1", "workdir": "../"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "permission"


def test_exec_command_sandbox_restricted_without_adapter_is_denied(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    ctx.sandbox_policy_default = "restricted"
    r = exec_command(ToolCall(call_id="c1", name="exec_command", args={"cmd": "echo 1", "sandbox": "inherit"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "sandbox_denied"
    assert p["data"]["sandbox"]["effective"] == "restricted"


def test_exec_command_sandbox_policy_invalid(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = exec_command(ToolCall(call_id="c1", name="exec_command", args={"cmd": "echo 1", "sandbox": "bad"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_exec_command_short_command_finishes_without_session_id(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = exec_command(ToolCall(call_id="c1", name="exec_command", args={"cmd": "echo hi", "yield_time_ms": 100}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    assert "hi" in p["stdout"]
    assert p["data"]["running"] is False
    assert p["data"]["session_id"] is None
    assert p["data"]["sandbox"]["effective"] == "none"


def test_exec_command_nonzero_exit_is_ok_false(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = exec_command(
        ToolCall(call_id="c1", name="exec_command", args={"cmd": "python -c \"import sys; sys.exit(5)\"", "yield_time_ms": 200}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "exit_code"
    assert p["exit_code"] == 5
    assert p["data"]["running"] is False
    assert p["data"]["session_id"] is None


def test_exec_command_interactive_returns_session_id(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = exec_command(
        ToolCall(call_id="c1", name="exec_command", args={"cmd": "python -u -c \"import time; print('ready'); time.sleep(2)\""}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["running"] is True
    assert isinstance(p["data"]["session_id"], int)
    assert p["data"]["session_id"] >= 1
    assert p["data"]["sandbox"]["effective"] == "none"


def test_write_stdin_validation_session_id_ge_1(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = write_stdin(ToolCall(call_id="c1", name="write_stdin", args={"session_id": 0}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_write_stdin_not_found(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = write_stdin(ToolCall(call_id="c1", name="write_stdin", args={"session_id": 999, "chars": "x"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "not_found"


def test_exec_command_and_write_stdin_roundtrip(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r1 = exec_command(
        ToolCall(
            call_id="c1",
            name="exec_command",
            args={
                "cmd": "python -u -c \"import sys; print('ready'); sys.stdout.flush(); line=sys.stdin.readline(); print('got:'+line.strip())\"",
                "yield_time_ms": 200,
            },
        ),
        ctx,
    )
    p1 = _payload(r1)
    assert p1["ok"] is True
    sid = p1["data"]["session_id"]
    assert isinstance(sid, int)
    assert "ready" in p1["stdout"]

    # PTY 往往处于 canonical mode；使用 CR 更接近“按下回车”
    r2 = write_stdin(ToolCall(call_id="c2", name="write_stdin", args={"session_id": sid, "chars": "hello\n", "yield_time_ms": 300}), ctx)
    p2 = _payload(r2)
    assert p2["ok"] is True
    combined = p2["stdout"]

    # PTY 通常会回显输入；真实输出可能在下一次 poll 才出现
    if p2["data"]["running"] is True:
        r3 = write_stdin(ToolCall(call_id="c3", name="write_stdin", args={"session_id": sid, "yield_time_ms": 300}), ctx)
        p3 = _payload(r3)
        assert p3["ok"] is True
        combined += p3["stdout"]
        assert p3["data"]["running"] is False
        assert p3["data"]["session_id"] is None
    else:
        assert p2["data"]["running"] is False
        assert p2["data"]["session_id"] is None

    assert "got:hello" in combined


def test_exec_command_max_output_tokens_truncates(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = exec_command(
        ToolCall(
            call_id="c1",
            name="exec_command",
            args={"cmd": "python -c \"print('x'*20000)\"", "yield_time_ms": 200, "max_output_tokens": 1},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["truncated"] is True

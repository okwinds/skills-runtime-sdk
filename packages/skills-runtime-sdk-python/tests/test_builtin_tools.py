from __future__ import annotations

import sys
from pathlib import Path

from agent_sdk.core.executor import Executor
from agent_sdk.tools.builtin import register_builtin_tools
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry


def _echo_argv() -> list[str]:
    echo_path = Path("/bin/echo")
    if echo_path.exists():
        return [str(echo_path), "hi"]
    return [sys.executable, "-c", 'print("hi")']


def test_builtin_file_write_read_roundtrip(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="r1", executor=Executor())
    reg = ToolRegistry(ctx=ctx)
    register_builtin_tools(reg)

    w = reg.dispatch(
        ToolCall(call_id="c1", name="file_write", args={"path": "a.txt", "content": "hello", "mkdirs": True})
    )
    assert w.ok is True

    r = reg.dispatch(ToolCall(call_id="c2", name="file_read", args={"path": "a.txt"}))
    assert r.ok is True
    assert r.details is not None
    assert "hello" in (r.details.get("stdout") or "")


def test_builtin_shell_exec_echo(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="r1", executor=Executor())
    reg = ToolRegistry(ctx=ctx)
    register_builtin_tools(reg)

    res = reg.dispatch(ToolCall(call_id="c1", name="shell_exec", args={"argv": _echo_argv()}))
    assert res.ok is True
    assert res.details is not None
    assert "hi" in (res.details.get("stdout") or "")


def test_builtin_ask_human_without_provider_returns_human_required(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="r1", executor=Executor(), human_io=None)
    reg = ToolRegistry(ctx=ctx)
    register_builtin_tools(reg)

    res = reg.dispatch(ToolCall(call_id="c1", name="ask_human", args={"question": "what?"}))
    assert res.ok is False
    assert res.error_kind == "human_required"

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skills_runtime.core.executor import Executor
from skills_runtime.tools.builtin.shell import shell
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


def _mk_ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="t_shell",
        executor=Executor(),
        emit_tool_events=False,
    )


def test_shell_ok_echo(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell(ToolCall(call_id="c1", name="shell", args={"command": ["python", "-c", "print('hi')"]}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    assert "hi" in p["stdout"]


def test_shell_requires_command_array(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell(ToolCall(call_id="c1", name="shell", args={"command": "echo hi"}), ctx)  # type: ignore[arg-type]
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_shell_workdir_escape_is_permission(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell(
        ToolCall(call_id="c1", name="shell", args={"command": ["python", "-c", "print('x')"], "workdir": "../"}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "permission"


def test_shell_timeout(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell(
        ToolCall(
            call_id="c1",
            name="shell",
            args={"command": ["python", "-c", "import time; time.sleep(2)"], "timeout_ms": 50},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "timeout"


def test_shell_env_is_passed(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell(
        ToolCall(
            call_id="c1",
            name="shell",
            args={
                "command": ["python", "-c", "import os; print(os.environ.get('X'))"],
                "env": {"X": "1"},
            },
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert "1" in p["stdout"]


def test_shell_sandbox_policy_invalid(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell(
        ToolCall(call_id="c1", name="shell", args={"command": ["python", "-c", "print(1)"], "sandbox": "bad"}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_shell_inherit_sandbox_default_none(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    # inherit + default none -> should still run without sandbox
    r = shell(
        ToolCall(call_id="c1", name="shell", args={"command": ["python", "-c", "print(2)"], "sandbox": "inherit"}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert "2" in p["stdout"]


def test_shell_sandbox_restricted_without_adapter_is_denied(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="t_shell", executor=Executor(), emit_tool_events=False)
    ctx.sandbox_policy_default = "restricted"
    r = shell(ToolCall(call_id="c1", name="shell", args={"command": ["python", "-c", "print(3)"]}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "sandbox_denied"


def test_shell_nonzero_exit_is_ok_false(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell(ToolCall(call_id="c1", name="shell", args={"command": ["python", "-c", "import sys; sys.exit(5)"]}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["exit_code"] == 5


def test_shell_stdout_is_redacted_by_ctx(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    secret = "S3CR3T"
    ctx.redaction_values = [secret]
    r = shell(
        ToolCall(call_id="c1", name="shell", args={"command": ["python", "-c", f"print('{secret}')"]}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert secret not in p["stdout"]
    assert "<redacted>" in p["stdout"]

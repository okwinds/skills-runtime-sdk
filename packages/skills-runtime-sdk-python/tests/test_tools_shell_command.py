from __future__ import annotations

import json
from pathlib import Path

from skills_runtime.core.executor import Executor
from skills_runtime.tools.builtin.shell_command import shell_command
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


def _mk_ctx(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="t_shell_command",
        executor=Executor(),
        emit_tool_events=False,
    )


def test_shell_command_ok_echo(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(
        ToolCall(call_id="c1", name="shell_command", args={"command": 'python -c "print(\\\"hi\\\")"'}), ctx
    )
    p = _payload(r)
    assert p["ok"] is True
    assert "hi" in p["stdout"]


def test_shell_command_requires_string(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(ToolCall(call_id="c1", name="shell_command", args={"command": ["echo", "hi"]}), ctx)  # type: ignore[arg-type]
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_shell_command_workdir_escape_is_permission(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(
        ToolCall(call_id="c1", name="shell_command", args={"command": "python -c \"print(1)\"", "workdir": "../"}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "permission"


def test_shell_command_timeout(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(
        ToolCall(
            call_id="c1",
            name="shell_command",
            args={"command": 'python -c "import time; time.sleep(2)"', "timeout_ms": 50},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "timeout"


def test_shell_command_env_is_passed(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(
        ToolCall(
            call_id="c1",
            name="shell_command",
            args={"command": 'python -c "import os; print(os.environ.get(\\\"X\\\"))"', "env": {"X": "1"}},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert "1" in p["stdout"]


def test_shell_command_sandbox_policy_invalid(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(ToolCall(call_id="c1", name="shell_command", args={"command": "echo 1", "sandbox": "bad"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_shell_command_inherit_sandbox_default_none(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(
        ToolCall(call_id="c1", name="shell_command", args={"command": "echo 2", "sandbox": "inherit"}), ctx
    )
    p = _payload(r)
    assert p["ok"] is True
    assert "2" in p["stdout"]


def test_shell_command_sandbox_restricted_without_adapter_is_denied(tmp_path: Path) -> None:
    ctx = ToolExecutionContext(workspace_root=tmp_path, run_id="t_shell_command", executor=Executor(), emit_tool_events=False)
    ctx.sandbox_policy_default = "restricted"
    r = shell_command(ToolCall(call_id="c1", name="shell_command", args={"command": "echo 3"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "sandbox_denied"


def test_shell_command_nonzero_exit_is_ok_false(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    r = shell_command(
        ToolCall(call_id="c1", name="shell_command", args={"command": 'python -c "import sys; sys.exit(5)"'}), ctx
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["exit_code"] == 5


def test_shell_command_stdout_is_redacted_by_ctx(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path)
    secret = "S3CR3T"
    ctx.redaction_values = [secret]
    r = shell_command(
        ToolCall(call_id="c1", name="shell_command", args={"command": f'python -c "print(\\\"{secret}\\\")"'}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert secret not in p["stdout"]
    assert "<redacted>" in p["stdout"]


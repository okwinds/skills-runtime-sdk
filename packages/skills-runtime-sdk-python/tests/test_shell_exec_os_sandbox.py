from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

import pytest

from skills_runtime.tools.builtin.shell_exec import shell_exec
from skills_runtime.tools.protocol import ToolCall
from skills_runtime.tools.registry import ToolExecutionContext


class _FakeCommandResult:
    def __init__(self, *, ok: bool, stdout: str = "", stderr: str = "", exit_code: int | None = 0, error_kind: str | None = None):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.duration_ms = 1
        self.truncated = False
        self.error_kind = error_kind


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run_command(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        timeout_ms: int = 60_000,
        cancel_checker=None,
    ):
        self.calls.append({"argv": list(argv), "cwd": Path(cwd), "env": dict(env or {}), "timeout_ms": timeout_ms})
        return _FakeCommandResult(ok=True, stdout="ok")


class _PrefixSandboxAdapter:
    def __init__(self, *, prefix: list[str]) -> None:
        self._prefix = list(prefix)

    def prepare_shell_exec(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Optional[Mapping[str, str]],
        workspace_root: Path,
    ):
        from skills_runtime.sandbox import PreparedCommand

        return PreparedCommand(argv=self._prefix + list(argv), cwd=cwd)


def _mk_ctx(
    *,
    workspace_root: Path,
    executor,
    sandbox_policy_default: str = "none",
    sandbox_adapter=None,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=workspace_root,
        run_id="run_test",
        wal=None,
        executor=executor,
        human_io=None,
        env={},
        cancel_checker=None,
        redaction_values=[],
        default_timeout_ms=123,
        max_file_bytes=10,
        sandbox_policy_default=sandbox_policy_default,
        sandbox_adapter=sandbox_adapter,
        emit_tool_events=False,
        event_sink=None,
    )


def test_shell_exec_default_inherit_uses_ctx_default_none(tmp_path: Path) -> None:
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=None)
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"]})
    result = shell_exec(call, ctx)

    assert result.ok is True
    assert ex.calls and ex.calls[0]["argv"] == ["echo", "hi"]
    assert (result.details or {}).get("data", {}).get("sandbox", {}).get("effective") == "none"
    assert (result.details or {}).get("data", {}).get("sandbox", {}).get("active") is False


def test_shell_exec_inherit_restricted_without_adapter_is_sandbox_denied(tmp_path: Path) -> None:
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="restricted", sandbox_adapter=None)
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"]})
    result = shell_exec(call, ctx)

    assert result.ok is False
    assert result.error_kind == "sandbox_denied"
    assert "Sandbox is required" in (result.details or {}).get("stderr", "")
    assert (result.details or {}).get("data", {}).get("sandbox", {}).get("effective") == "restricted"
    assert ex.calls == []


def test_shell_exec_explicit_none_ignores_restricted_default(tmp_path: Path) -> None:
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="restricted", sandbox_adapter=None)
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "none"})
    result = shell_exec(call, ctx)

    assert result.ok is True
    assert ex.calls and ex.calls[0]["argv"] == ["echo", "hi"]


def test_shell_exec_explicit_restricted_requires_adapter(tmp_path: Path) -> None:
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=None)
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "restricted"})
    result = shell_exec(call, ctx)

    assert result.ok is False
    assert result.error_kind == "sandbox_denied"
    assert ex.calls == []


def test_shell_exec_explicit_restricted_uses_adapter_wraps_argv(tmp_path: Path) -> None:
    ex = _RecordingExecutor()
    adapter = _PrefixSandboxAdapter(prefix=["sandbox-wrapper"])
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=adapter)
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "restricted"})
    result = shell_exec(call, ctx)

    assert result.ok is True
    assert ex.calls and ex.calls[0]["argv"] == ["sandbox-wrapper", "echo", "hi"]
    assert (result.details or {}).get("data", {}).get("sandbox", {}).get("effective") == "restricted"
    assert (result.details or {}).get("data", {}).get("sandbox", {}).get("active") is True


def test_shell_exec_invalid_sandbox_policy_is_validation_error(tmp_path: Path) -> None:
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=None)
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "???bad"})
    result = shell_exec(call, ctx)

    assert result.ok is False
    assert result.error_kind == "validation"
    assert ex.calls == []


def test_shell_exec_sandbox_inherit_is_case_insensitive(tmp_path: Path) -> None:
    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=None)
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "InHeRiT"})
    result = shell_exec(call, ctx)

    assert result.ok is True
    assert ex.calls and ex.calls[0]["argv"] == ["echo", "hi"]


def test_shell_exec_sandbox_restricted_adapter_exception_maps_to_sandbox_denied(tmp_path: Path) -> None:
    class _BadAdapter:
        def prepare_shell_exec(self, *, argv, cwd, env, workspace_root):
            raise RuntimeError("boom")

    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=_BadAdapter())
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "restricted"})
    result = shell_exec(call, ctx)

    assert result.ok is False
    assert result.error_kind == "sandbox_denied"
    assert "boom" in (result.details or {}).get("stderr", "")
    assert ex.calls == []


def test_shell_exec_sandbox_restricted_passes_workspace_root_to_adapter(tmp_path: Path) -> None:
    captured: dict = {}

    class _CaptureAdapter:
        def prepare_shell_exec(self, *, argv, cwd, env, workspace_root):
            captured["workspace_root"] = workspace_root
            from skills_runtime.sandbox import PreparedCommand

            return PreparedCommand(argv=list(argv), cwd=cwd)

    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=_CaptureAdapter())
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "sandbox": "restricted"})
    result = shell_exec(call, ctx)

    assert result.ok is True
    assert captured["workspace_root"] == tmp_path


def test_shell_exec_sandbox_restricted_passes_cwd_to_adapter(tmp_path: Path) -> None:
    captured: dict = {}
    (tmp_path / "sub").mkdir()

    class _CaptureAdapter:
        def prepare_shell_exec(self, *, argv, cwd, env, workspace_root):
            captured["cwd"] = cwd
            from skills_runtime.sandbox import PreparedCommand

            return PreparedCommand(argv=list(argv), cwd=cwd)

    ex = _RecordingExecutor()
    ctx = _mk_ctx(workspace_root=tmp_path, executor=ex, sandbox_policy_default="none", sandbox_adapter=_CaptureAdapter())
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"], "cwd": "sub", "sandbox": "restricted"})
    result = shell_exec(call, ctx)

    assert result.ok is True
    assert captured["cwd"] == tmp_path / "sub"


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_seatbelt_sandbox_exec_available_on_mac_or_skip() -> None:
    from skills_runtime.sandbox import SeatbeltSandboxAdapter

    adapter = SeatbeltSandboxAdapter(profile="(version 1) (allow default)")
    # 只验证可用性检测本身不抛异常；不强制在非 mac 上可用
    _ = adapter.is_available()


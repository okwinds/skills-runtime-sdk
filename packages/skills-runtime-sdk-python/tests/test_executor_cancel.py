from __future__ import annotations

from typing import Any, AsyncIterator
import time
from pathlib import Path

import pytest

from skills_runtime.core.executor import Executor
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolCall, ToolSpec
from skills_runtime.tools.builtin.shell_exec import shell_exec
from skills_runtime.tools.registry import ToolExecutionContext
from skills_runtime.core.agent import Agent


def _sleep_argv() -> list[str]:
    # 跨平台：用 python 自身 sleep，避免依赖 /bin/sleep 在 Windows 不可用
    return ["python", "-c", "import time; time.sleep(5)"]


def test_executor_cancel_checker_immediate_cancels(tmp_path: Path) -> None:
    ex = Executor()
    r = ex.run_command(_sleep_argv(), cwd=tmp_path, timeout_ms=10_000, cancel_checker=lambda: True)
    assert r.ok is False
    assert r.error_kind == "cancelled"
    assert r.timeout is False
    assert r.exit_code is None


def test_executor_cancel_checker_eventually_cancels_fast(tmp_path: Path) -> None:
    ex = Executor()
    started = time.monotonic()

    def cancel_checker() -> bool:
        return (time.monotonic() - started) > 0.05

    t0 = time.monotonic()
    r = ex.run_command(_sleep_argv(), cwd=tmp_path, timeout_ms=10_000, cancel_checker=cancel_checker)
    dt = time.monotonic() - t0
    assert dt < 1.5
    assert r.error_kind == "cancelled"


def test_executor_completes_before_cancel_checker_triggers(tmp_path: Path) -> None:
    ex = Executor()
    started = time.monotonic()

    def cancel_checker() -> bool:
        return (time.monotonic() - started) > 0.5

    r = ex.run_command(
        ["python", "-c", "print('done')"],
        cwd=tmp_path,
        timeout_ms=10_000,
        cancel_checker=cancel_checker,
    )
    assert r.ok is True
    assert r.error_kind is None
    assert r.exit_code == 0
    assert "done" in r.stdout


def test_executor_timeout_is_distinct_from_cancel(tmp_path: Path) -> None:
    ex = Executor()
    r = ex.run_command(_sleep_argv(), cwd=tmp_path, timeout_ms=10, cancel_checker=lambda: False)
    assert r.ok is False
    assert r.timeout is True
    assert r.error_kind == "timeout"


@pytest.mark.parametrize("timeout_ms", [1, 10, 50])
def test_executor_cancel_does_not_report_timeout_when_cancelled(tmp_path: Path, timeout_ms: int) -> None:
    ex = Executor()
    started = time.monotonic()

    def cancel_checker() -> bool:
        return (time.monotonic() - started) > 0.001

    r = ex.run_command(_sleep_argv(), cwd=tmp_path, timeout_ms=timeout_ms, cancel_checker=cancel_checker)
    assert r.error_kind == "cancelled"
    assert r.timeout is False


def test_executor_cancel_checker_exception_is_fail_open(tmp_path: Path) -> None:
    ex = Executor()

    def cancel_checker() -> bool:
        raise RuntimeError("boom")

    # cancel_checker 异常不应导致 executor 崩溃；这里会走 timeout
    r = ex.run_command(_sleep_argv(), cwd=tmp_path, timeout_ms=20, cancel_checker=cancel_checker)
    assert r.ok is False
    assert r.error_kind in ("timeout", "cancelled", "exit_code", "unknown")


def test_executor_cancelled_can_capture_partial_stdout(tmp_path: Path) -> None:
    ex = Executor()
    started = time.monotonic()

    def cancel_checker() -> bool:
        # 需要留足时间让子进程启动并把第一行输出写入管道；过短会导致不同机器上偶发“尚未输出即被取消”。
        return (time.monotonic() - started) > 0.2

    r = ex.run_command(
        ["python", "-u", "-c", "import time; print('hello', flush=True); time.sleep(5)"],
        cwd=tmp_path,
        timeout_ms=10_000,
        cancel_checker=cancel_checker,
    )
    assert r.ok is False
    assert r.error_kind == "cancelled"
    assert "hello" in r.stdout


def test_executor_env_is_applied_when_running_command(tmp_path: Path) -> None:
    ex = Executor()
    r = ex.run_command(
        ["python", "-c", "import os; print(os.getenv('X_ENV_TEST',''))"],
        cwd=tmp_path,
        timeout_ms=10_000,
        env={"X_ENV_TEST": "v1"},
    )
    assert r.ok is True
    assert r.stdout.strip() == "v1"


def test_shell_exec_propagates_cancel_to_executor(tmp_path: Path) -> None:
    ex = Executor()
    ctx = ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="run_test",
        wal=None,
        executor=ex,
        cancel_checker=lambda: True,
    )
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": _sleep_argv()}, raw_arguments=None)
    res = shell_exec(call, ctx)
    obj = __import__("json").loads(res.content)
    assert obj["ok"] is False
    assert obj["error_kind"] == "cancelled"


def test_tool_ctx_env_is_merged_into_shell_exec(tmp_path: Path) -> None:
    ex = Executor()
    ctx = ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="run_test",
        wal=None,
        executor=ex,
        env={"X_DEMO": "1"},
    )
    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["python", "-c", "import os;print(os.getenv('X_DEMO',''))"]})
    res = shell_exec(call, ctx)
    obj = __import__("json").loads(res.content)
    assert obj["ok"] is True
    assert obj["stdout"].strip() == "1"


def test_agent_cancelled_during_shell_exec_emits_run_cancelled(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    class _Backend:
        async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # type: ignore[override]
            _ = request
            yield ChatStreamEvent(
                type="tool_calls",
                tool_calls=[
                    ToolCall(
                        call_id="c1",
                        name="shell_exec",
                        args={"argv": _sleep_argv(), "timeout_ms": 10_000},
                        raw_arguments=None,
                    )
                ],
                finish_reason="tool_calls",
            )
            yield ChatStreamEvent(type="completed", finish_reason="tool_calls")

    # 显式开启 safety.mode=allow：
    # - 默认配置是 ask（见 assets/default.yaml 与 safety.md），在无 approval_provider 时会被保守拒绝；
    # - 本用例希望 shell_exec 真正执行长命令，并由 cancel_checker 在执行期间触发取消。
    overlay_path = tmp_path / "test-overlay.yaml"
    overlay_path.write_text('safety:\n  mode: "allow"\n', encoding="utf-8")

    started = time.monotonic()

    def cancel_checker() -> bool:
        return (time.monotonic() - started) > 0.2

    agent = Agent(
        backend=_Backend(),
        workspace_root=tmp_path,
        cancel_checker=cancel_checker,
        config_paths=[overlay_path],
    )
    events = list(agent.run_stream("run a long command"))
    types = [e.type for e in events]
    assert "approval_requested" not in types
    tool_finished = [e for e in events if e.type == "tool_call_finished" and e.payload.get("tool") == "shell_exec"]
    assert tool_finished
    assert (tool_finished[-1].payload.get("result") or {}).get("error_kind") == "cancelled"
    assert "run_cancelled" in types

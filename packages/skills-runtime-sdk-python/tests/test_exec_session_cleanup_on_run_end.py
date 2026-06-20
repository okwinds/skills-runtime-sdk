"""P0-3 测试：run 结束时清理 in-process exec session（终态触发、不误杀 waiting_human、run-scoped）。

对齐规格：docs/specs/2026-06-20-p0-safety-runtime-hardening.md 的 P0-3 节 + Test Plan P0-3。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.exec_sessions import ExecSessionManager
from skills_runtime.core.run_context import RunContext
from skills_runtime.state.wal_emitter import WalEmitter


def _make_ctx(tmp_path: Path, run_id: str = "run-x") -> RunContext:
    """构造最小 RunContext（不依赖真实 WAL 文件）。"""

    emitter = MagicMock(spec=WalEmitter)
    return RunContext(
        run_id=run_id,
        run_dir=tmp_path,
        wal=MagicMock(),
        wal_locator=str(tmp_path / "events.jsonl"),
        wal_emitter=emitter,
        history=[],
        artifacts_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# ExecSessionManager: close_all_for_run / spawn run_id
# ---------------------------------------------------------------------------


def test_spawn_records_run_id_and_close_all_for_run_filters(tmp_path: Path) -> None:
    mgr = ExecSessionManager()
    s1 = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    s2 = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-b")
    try:
        assert mgr.has(s1.session_id) and mgr.has(s2.session_id)

        closed = mgr.close_all_for_run("run-a")
        assert closed == 1
        assert not mgr.has(s1.session_id)
        assert mgr.has(s2.session_id)  # run-b 不被误杀
    finally:
        mgr.close_all()


def test_close_all_for_run_none_is_noop(tmp_path: Path) -> None:
    mgr = ExecSessionManager()
    s = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    try:
        closed = mgr.close_all_for_run(None)
        assert closed == 0
        assert mgr.has(s.session_id)  # None 不退化，不清理任何 session
    finally:
        mgr.close_all()


def test_close_all_for_run_unknown_run_id_closes_zero(tmp_path: Path) -> None:
    mgr = ExecSessionManager()
    mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    try:
        assert mgr.close_all_for_run("run-zzz") == 0
        assert len(mgr._sessions) == 1
    finally:
        mgr.close_all()


def test_close_all_for_run_fail_soft(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mgr = ExecSessionManager()
    mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    try:
        original_close = mgr.close

        call_count = {"n": 0}

        def _flaky_close(sid: int) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom")
            original_close(sid)

        monkeypatch.setattr(mgr, "close", _flaky_close)
        # 第一个 session 抛异常，第二个仍被清理
        closed = mgr.close_all_for_run("run-a")
        assert closed == 1
    finally:
        mgr.close_all()


def test_spawn_without_run_id_defaults_none(tmp_path: Path) -> None:
    """spawn 不传 run_id 时，session.run_id 为 None；close_all_for_run(具体 id) 不会命中它。"""

    mgr = ExecSessionManager()
    s = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path)
    try:
        assert s.run_id is None
        assert mgr.close_all_for_run("run-a") == 0
        assert mgr.has(s.session_id)
    finally:
        mgr.close_all()


# ---------------------------------------------------------------------------
# RunContext: last_terminal_state 收口
# ---------------------------------------------------------------------------


def test_run_context_terminal_state_tracking() -> None:
    """emit_event 按 event.type 统一设 _last_terminal_state。"""

    import skills_runtime.core.utils as utils_mod

    ctx = RunContext(
        run_id="r",
        run_dir=Path("/tmp"),
        wal=MagicMock(),
        wal_locator="x",
        wal_emitter=MagicMock(spec=WalEmitter),
        history=[],
        artifacts_dir=Path("/tmp"),
    )

    def _ev(t: str) -> AgentEvent:
        return AgentEvent(type=t, timestamp="2026-06-20T00:00:00Z", run_id="r", payload={})

    assert ctx.last_terminal_state is None
    ctx.emit_event(_ev("run_started"))
    assert ctx.last_terminal_state is None  # 非 终态 type
    ctx.emit_event(_ev("tool_call_requested"))
    assert ctx.last_terminal_state is None
    ctx.emit_event(_ev("run_failed"))
    assert ctx.last_terminal_state == "failed"
    ctx.emit_event(_ev("run_completed"))
    assert ctx.last_terminal_state == "completed"
    ctx.emit_event(_ev("run_cancelled"))
    assert ctx.last_terminal_state == "cancelled"
    ctx.emit_event(_ev("run_waiting_human"))
    assert ctx.last_terminal_state == "waiting_human"


# ---------------------------------------------------------------------------
# RunSession.cleanup_exec_sessions: 终态判定 + run-scoped
# ---------------------------------------------------------------------------


def _make_run_session(ctx: RunContext, exec_sessions: Optional[ExecSessionManager]) -> Any:
    """构造一个最小 RunSession，仅注入 ctx + exec_sessions（绕过完整装配）。"""

    from skills_runtime.core.run_lifecycle import RunSession

    return RunSession(
        run_id=ctx.run_id,
        wal_locator=ctx.wal_locator,
        ctx=ctx,
        loop=MagicMock(),
        backend=None,
        registry=MagicMock(),
        dispatcher=MagicMock(),
        safety_gate=MagicMock(),
        turn_orchestrator=MagicMock(),
        finalizer=MagicMock(),
        run_env_store={},
        builtin_tool_names=frozenset(),
        exec_sessions=exec_sessions,
    )


def test_cleanup_on_completed(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, run_id="run-a")
    mgr = ExecSessionManager()
    s = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    session = _make_run_session(ctx, mgr)
    ctx.emit_event(AgentEvent(type="run_completed", timestamp="t", run_id="run-a", payload={}))
    session.cleanup_exec_sessions()
    assert not mgr.has(s.session_id)


def test_cleanup_on_failed_includes_denial_abort(tmp_path: Path) -> None:
    """denial-abort 直接 emit run_failed（不经具名 setter），收口仍应覆盖。"""

    ctx = _make_ctx(tmp_path, run_id="run-a")
    mgr = ExecSessionManager()
    s = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    session = _make_run_session(ctx, mgr)
    # 模拟 tool_orchestration 的 policy_denied 直接 emit
    ctx.emit_event(
        AgentEvent(
            type="run_failed",
            timestamp="t",
            run_id="run-a",
            payload={"error_kind": "policy_denied"},
        )
    )
    assert ctx.last_terminal_state == "failed"
    session.cleanup_exec_sessions()
    assert not mgr.has(s.session_id)


def test_cleanup_not_on_waiting_human(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, run_id="run-a")
    mgr = ExecSessionManager()
    s = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    session = _make_run_session(ctx, mgr)
    ctx.emit_event(AgentEvent(type="run_waiting_human", timestamp="t", run_id="run-a", payload={}))
    session.cleanup_exec_sessions()
    assert mgr.has(s.session_id)  # waiting_human 不清理


def test_cleanup_not_on_none_terminal(tmp_path: Path) -> None:
    """未到终态（异常路径，last_terminal_state=None）不清理。"""

    ctx = _make_ctx(tmp_path, run_id="run-a")
    mgr = ExecSessionManager()
    s = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    session = _make_run_session(ctx, mgr)
    # 不 emit 任何终态
    session.cleanup_exec_sessions()
    assert mgr.has(s.session_id)


def test_cleanup_manager_none_is_noop(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, run_id="run-a")
    session = _make_run_session(ctx, None)
    ctx.emit_event(AgentEvent(type="run_completed", timestamp="t", run_id="run-a", payload={}))
    session.cleanup_exec_sessions()  # 不抛


def test_cleanup_run_scoped_no_cross_run_kill(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, run_id="run-a")
    mgr = ExecSessionManager()
    sa = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    sb = mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-b")
    session = _make_run_session(ctx, mgr)
    ctx.emit_event(AgentEvent(type="run_completed", timestamp="t", run_id="run-a", payload={}))
    try:
        session.cleanup_exec_sessions()
        assert not mgr.has(sa.session_id)
        assert mgr.has(sb.session_id)  # run-b 不被误杀
    finally:
        mgr.close_all()


def test_cleanup_fail_soft_does_not_raise(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ctx = _make_ctx(tmp_path, run_id="run-a")
    mgr = ExecSessionManager()
    mgr.spawn(argv=["python", "-c", "import time;time.sleep(30)"], cwd=tmp_path, run_id="run-a")
    session = _make_run_session(ctx, mgr)
    ctx.emit_event(AgentEvent(type="run_completed", timestamp="t", run_id="run-a", payload={}))

    def _boom(_run_id: Any) -> int:
        raise RuntimeError("cleanup boom")

    monkeypatch.setattr(mgr, "close_all_for_run", _boom)
    # 不抛，吞异常
    session.cleanup_exec_sessions()

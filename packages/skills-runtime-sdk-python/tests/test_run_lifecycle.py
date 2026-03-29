from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from skills_runtime.agent import Agent
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.run_errors import _classify_run_exception
from skills_runtime.core.run_lifecycle import RunBootstrap, RunFinalizer
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.state.wal_emitter import WalEmitter
from skills_runtime.state.wal_protocol import InMemoryWal


class _EchoBackend:
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        _ = request
        yield ChatStreamEvent(type="text_delta", text="ok")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_run_bootstrap_builds_session_and_emits_run_started(tmp_path: Path) -> None:
    wal = InMemoryWal(locator_str="wal://bootstrap")
    agent = Agent(backend=_EchoBackend(), workspace_root=tmp_path, wal_backend=wal)
    loop_obj = agent._loop
    stream_events: list[AgentEvent] = []
    bootstrap = RunBootstrap(
        workspace_root=loop_obj._workspace_root,
        config=loop_obj._config,
        config_overlay_paths=loop_obj._config_overlay_paths,
        profile_id=loop_obj._profile_id,
        child_profile_map=loop_obj._child_profile_map,
        planner_model=loop_obj._planner_model,
        executor_model=loop_obj._executor_model,
        backend=loop_obj._backend,
        executor=loop_obj._executor,
        human_io=loop_obj._human_io,
        cancel_checker=loop_obj._cancel_checker,
        safety=loop_obj._safety,
        approved_for_session_keys=loop_obj._approved_for_session_keys,
        exec_sessions=loop_obj._exec_sessions,
        collab_manager=loop_obj._collab_manager,
        wal_backend=loop_obj._wal_backend,
        event_hooks=loop_obj._event_hooks,
        env_store=loop_obj._env_store,
        skills_manager=loop_obj._skills_manager,
        prompt_manager=loop_obj._prompt_manager,
        extra_tools=loop_obj._extra_tools,
        builtin_tool_names_cache=loop_obj._builtin_tool_names_cache,
        ensure_skill_env_vars=loop_obj._ensure_skill_env_vars,
    )

    session = bootstrap.build(
        task="hi",
        run_id="run_bootstrap",
        initial_history=[{"role": "user", "content": "seed"}],
        emit=stream_events.append,
    )

    assert session.run_id == "run_bootstrap"
    assert session.ctx.wal_locator == "wal://bootstrap#run_id=run_bootstrap"
    assert session.ctx.history == [{"role": "user", "content": "seed"}]
    assert stream_events[0].type == "run_started"
    assert session.turn_orchestrator is not None
    assert session.finalizer is not None


def test_run_finalizer_emits_terminal_event_and_merges_only_new_env_vars(tmp_path: Path) -> None:
    wal = InMemoryWal(locator_str="wal://finalizer")
    stream_events: list[AgentEvent] = []
    emitter = WalEmitter(wal=wal, stream=stream_events.append, hooks=[])
    ctx = RunContext(
        run_id="run_finalizer",
        run_dir=tmp_path,
        wal=wal,
        wal_locator=wal.locator(),
        wal_emitter=emitter,
        history=[],
        artifacts_dir=tmp_path / "artifacts",
    )
    session_env = {"EXISTING": "keep"}
    run_env = {"EXISTING": "temp-change", "NEW_KEY": "new-value"}
    finalizer = RunFinalizer(
        ctx=ctx,
        session_env_store=session_env,
        run_env_store=run_env,
        initial_env_keys=set(session_env.keys()),
        classify_run_exception=_classify_run_exception,
    )

    finalizer.emit_completed(final_output="done")
    finalizer.merge_new_env_vars()

    terminal = stream_events[-1]
    assert terminal.type == "run_completed"
    assert terminal.payload["final_output"] == "done"
    assert session_env == {"EXISTING": "keep", "NEW_KEY": "new-value"}


def test_run_finalizer_emits_failed_event_with_wal_locator(tmp_path: Path) -> None:
    wal = InMemoryWal(locator_str="wal://finalizer-failed")
    stream_events: list[AgentEvent] = []
    emitter = WalEmitter(wal=wal, stream=stream_events.append, hooks=[])
    ctx = RunContext(
        run_id="run_failed",
        run_dir=tmp_path,
        wal=wal,
        wal_locator=wal.locator(),
        wal_emitter=emitter,
        history=[],
        artifacts_dir=tmp_path / "artifacts",
    )
    finalizer = RunFinalizer(
        ctx=ctx,
        session_env_store={},
        run_env_store={},
        initial_env_keys=set(),
        classify_run_exception=_classify_run_exception,
    )

    finalizer.emit_failed(ValueError("boom"))

    terminal = stream_events[-1]
    assert terminal.type == "run_failed"
    assert terminal.payload["error_kind"] == "config_error"
    assert terminal.payload["message"] == "boom"
    assert terminal.payload["wal_locator"] == "wal://finalizer-failed"


def test_agent_emits_run_failed_when_backend_is_missing(tmp_path: Path) -> None:
    wal = InMemoryWal(locator_str="wal://missing-backend")
    agent = Agent(backend=None, workspace_root=tmp_path, wal_backend=wal)

    events = list(agent.run_stream("hi", run_id="run_missing_backend"))

    failed = [event for event in events if event.type == "run_failed"]
    assert failed
    assert failed[-1].payload["error_kind"] == "config_error"
    assert failed[-1].payload["message"] == "未配置 LLM backend（backend=None）"
    assert failed[-1].payload["wal_locator"] == "wal://missing-backend#run_id=run_missing_backend"

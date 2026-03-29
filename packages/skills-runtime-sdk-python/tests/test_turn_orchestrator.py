from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.loop_controller import LoopController
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.turn_orchestrator import TurnOrchestrator, TurnResult
from skills_runtime.state.wal_emitter import WalEmitter
from skills_runtime.state.wal_protocol import InMemoryWal
from skills_runtime.tools.protocol import ToolCall, ToolSpec


class _SkillsManagerStub:
    def resolve_mentions(self, task: str):
        _ = task
        return []


class _PromptManagerStub:
    def build_messages(
        self,
        *,
        task: str,
        cwd: str,
        tools,
        skills_manager,
        injected_skills,
        history,
        user_input=None,
    ):
        _ = (cwd, tools, skills_manager, injected_skills, history, user_input)
        return ([{"role": "user", "content": task}], {"debug": "ok"})


class _RegistryStub:
    def list_specs(self):
        return [ToolSpec(name="echo", description="echo", parameters={"type": "object", "properties": {}})]


class _BridgeStub:
    def __init__(self, outcome):
        self._outcome = outcome

    async def run(self, *, backend, request):
        _ = (backend, request)
        return self._outcome


class _OutcomeStub:
    def __init__(self, *, assistant_text: str, pending_tool_calls, terminal_state: str, terminal_error=None):
        self.assistant_text = assistant_text
        self.pending_tool_calls = list(pending_tool_calls)
        self.terminal_state = terminal_state
        self.terminal_error = terminal_error
        self.usage_payload = None


def _make_ctx(tmp_path: Path) -> tuple[RunContext, list[AgentEvent]]:
    wal = InMemoryWal(locator_str="wal://turn-orchestrator")
    stream_events: list[AgentEvent] = []
    emitter = WalEmitter(wal=wal, stream=stream_events.append, hooks=[])
    ctx = RunContext(
        run_id="run_turn",
        run_dir=tmp_path,
        wal=wal,
        wal_locator=wal.locator(),
        wal_emitter=emitter,
        history=[],
        artifacts_dir=tmp_path / "artifacts",
    )
    return ctx, stream_events


@pytest.mark.asyncio
async def test_turn_orchestrator_returns_continue_with_tools_when_bridge_has_pending_calls(tmp_path: Path) -> None:
    ctx, stream_events = _make_ctx(tmp_path)
    call = ToolCall(call_id="c1", name="echo", args={"x": 1}, raw_arguments='{"x":1}')
    bridge = _BridgeStub(
        _OutcomeStub(
            assistant_text="draft",
            pending_tool_calls=[call],
            terminal_state="completed",
        )
    )
    orchestrator = TurnOrchestrator(
        workspace_root=tmp_path,
        run_id="run_turn",
        task="do it",
        executor_model="fake-model",
        human_io=None,
        human_timeout_ms=1000,
        skills_manager=_SkillsManagerStub(),
        prompt_manager=_PromptManagerStub(),
        registry=_RegistryStub(),
        ensure_skill_env_vars=lambda **kwargs: True,
        bridge_factory=lambda **kwargs: bridge,
        handle_context_length_exceeded_fn=None,
    )

    result = await orchestrator.run_turn(
        ctx=ctx,
        loop=LoopController(max_steps=10, max_wall_time_sec=None, started_monotonic=0.0),
        backend=object(),
        turn_id="turn_1",
        run_env_store={},
        safety_gate=object(),
    )

    assert result.kind == "continue_with_tools"
    assert result.assistant_text == "draft"
    assert [c.call_id for c in result.pending_tool_calls] == ["c1"]
    assert [ev.type for ev in stream_events] == ["llm_request_started"]


@pytest.mark.asyncio
async def test_turn_orchestrator_returns_completed_without_terminal_event(tmp_path: Path) -> None:
    ctx, stream_events = _make_ctx(tmp_path)
    bridge = _BridgeStub(
        _OutcomeStub(
            assistant_text="done",
            pending_tool_calls=[],
            terminal_state="completed",
        )
    )
    orchestrator = TurnOrchestrator(
        workspace_root=tmp_path,
        run_id="run_turn",
        task="do it",
        executor_model="fake-model",
        human_io=None,
        human_timeout_ms=1000,
        skills_manager=_SkillsManagerStub(),
        prompt_manager=_PromptManagerStub(),
        registry=_RegistryStub(),
        ensure_skill_env_vars=lambda **kwargs: True,
        bridge_factory=lambda **kwargs: bridge,
        handle_context_length_exceeded_fn=None,
    )

    result = await orchestrator.run_turn(
        ctx=ctx,
        loop=LoopController(max_steps=10, max_wall_time_sec=None, started_monotonic=0.0),
        backend=object(),
        turn_id="turn_1",
        run_env_store={},
        safety_gate=object(),
    )

    assert result.kind == "completed"
    assert result.assistant_text == "done"
    assert result.pending_tool_calls == []
    assert [ev.type for ev in stream_events] == ["llm_request_started"]

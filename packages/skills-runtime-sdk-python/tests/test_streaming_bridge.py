from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import AsyncIterator

import pytest

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.loop_controller import LoopController
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.streaming_bridge import StreamOutcome, StreamingBridge
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.safety.gate import SafetyGate
from skills_runtime.state.wal_emitter import WalEmitter
from skills_runtime.state.wal_protocol import InMemoryWal
from skills_runtime.tools.protocol import ToolCall


class _BackendWithDeltas:
    """产出 text/tool_calls/completed 的 backend stub。"""

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        _ = request
        yield ChatStreamEvent(type="text_delta", text="hello ")
        yield ChatStreamEvent(
            type="tool_calls",
            tool_calls=[
                ToolCall(
                    call_id="call_1",
                    name="echo",
                    args={"secret": "SECRET_VALUE"},
                    raw_arguments='{"secret":"SECRET_VALUE"}',
                )
            ],
        )
        yield ChatStreamEvent(
            type="completed",
            finish_reason="stop",
            usage={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
            request_id="req_1",
            provider="fake",
        )


class _SlowBackend:
    """慢速 backend，用于触发 cancel/budget 终态。"""

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        _ = request
        await asyncio.sleep(10)
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def _make_context(tmp_path: Path) -> tuple[RunContext, InMemoryWal, list[AgentEvent]]:
    wal = InMemoryWal(locator_str="wal://stream-bridge")
    stream_events: list[AgentEvent] = []
    emitter = WalEmitter(wal=wal, stream=stream_events.append, hooks=[])
    ctx = RunContext(
        run_id="run_stream_bridge",
        run_dir=tmp_path,
        wal=wal,
        wal_locator=wal.locator(),
        wal_emitter=emitter,
        history=[],
        artifacts_dir=tmp_path / "artifacts",
    )
    return ctx, wal, stream_events


def _make_bridge(
    *,
    tmp_path: Path,
    cancel_checker=None,
    max_wall_time_sec: float | None = None,
    env_store: dict[str, str] | None = None,
) -> tuple[StreamingBridge, InMemoryWal, list[AgentEvent]]:
    ctx, wal, stream_events = _make_context(tmp_path)
    loop = LoopController(
        max_steps=10,
        max_wall_time_sec=max_wall_time_sec,
        started_monotonic=time.monotonic(),
        cancel_checker=cancel_checker,
    )
    if max_wall_time_sec is not None and max_wall_time_sec <= 0:
        loop = LoopController(
            max_steps=10,
            max_wall_time_sec=max_wall_time_sec,
            started_monotonic=time.monotonic() - 1.0,
            cancel_checker=cancel_checker,
        )
    bridge = StreamingBridge(
        ctx=ctx,
        loop=loop,
        turn_id="turn_1",
        executor_model="fake-model",
        safety_gate=SafetyGate(
            safety_config=object(),
            get_descriptor=lambda _tool_name: None,
            skills_manager=None,
            is_custom_tool=lambda _tool_name: False,
        ),
        env_store=dict(env_store or {}),
    )
    return bridge, wal, stream_events


@pytest.mark.asyncio
async def test_streaming_bridge_returns_completed_outcome_and_emits_deltas(tmp_path: Path) -> None:
    bridge, wal, stream_events = _make_bridge(
        tmp_path=tmp_path,
        env_store={"TOKEN": "SECRET_VALUE"},
    )

    outcome = await bridge.run(
        backend=_BackendWithDeltas(),
        request=ChatRequest(model="fake-model", messages=[{"role": "user", "content": "hi"}], run_id="r1", turn_id="turn_1"),
    )

    assert outcome == StreamOutcome(
        assistant_text="hello ",
        pending_tool_calls=[
            ToolCall(
                call_id="call_1",
                name="echo",
                args={"secret": "SECRET_VALUE"},
                raw_arguments='{"secret":"SECRET_VALUE"}',
            )
        ],
        usage_payload={
            "model": "fake-model",
            "input_tokens": 3,
            "output_tokens": 5,
            "total_tokens": 8,
            "provider": "fake",
            "request_id": "req_1",
        },
        terminal_state="completed",
        terminal_error=None,
    )

    event_types = [ev.type for ev in stream_events]
    assert event_types == ["llm_response_delta", "llm_response_delta", "llm_usage"]
    delta_payload = stream_events[1].payload["tool_calls"][0]
    assert delta_payload["arguments"] != {"secret": "SECRET_VALUE"}
    assert sum(1 for ev in wal.iter_events() if ev.type == "llm_usage") == 1


@pytest.mark.asyncio
async def test_streaming_bridge_returns_cancelled_without_emitting_terminal_event(tmp_path: Path) -> None:
    cancelled = False

    def cancel_checker() -> bool:
        return cancelled

    bridge, wal, stream_events = _make_bridge(
        tmp_path=tmp_path,
        cancel_checker=cancel_checker,
    )

    async def _trigger_cancel() -> None:
        nonlocal cancelled
        await asyncio.sleep(0.05)
        cancelled = True

    cancel_task = asyncio.create_task(_trigger_cancel())
    try:
        outcome = await bridge.run(
            backend=_SlowBackend(),
            request=ChatRequest(model="fake-model", messages=[{"role": "user", "content": "hi"}], run_id="r1", turn_id="turn_1"),
        )
    finally:
        await asyncio.gather(cancel_task, return_exceptions=True)

    assert outcome.terminal_state == "cancelled"
    assert outcome.terminal_error is None
    assert outcome.assistant_text == ""
    assert outcome.pending_tool_calls == []
    assert not any(ev.type in ("run_cancelled", "run_failed") for ev in stream_events)
    assert not any(ev.type in ("run_cancelled", "run_failed") for ev in wal.iter_events())

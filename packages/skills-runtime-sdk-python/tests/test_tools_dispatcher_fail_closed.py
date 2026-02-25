from __future__ import annotations

from typing import List

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.tools.dispatcher import ToolDispatchInputs, ToolDispatcher
from skills_runtime.tools.protocol import ToolCall, ToolResult


class _DummyRegistry:
    def __init__(self) -> None:
        self.dispatch_calls: List[ToolCall] = []

    def dispatch(self, call: ToolCall, *, turn_id: str, step_id: str) -> ToolResult:  # noqa: ARG002
        self.dispatch_calls.append(call)
        return ToolResult.ok_payload(stdout="ok")


def test_tool_dispatcher_invalid_raw_arguments_fail_closed_and_no_started_event() -> None:
    events: List[AgentEvent] = []
    registry = _DummyRegistry()
    dispatcher = ToolDispatcher(registry=registry, now_rfc3339=lambda: "2026-01-01T00:00:00Z")

    call = ToolCall(call_id="call_bad", name="shell_exec", args={}, raw_arguments='{"argv":')
    result = dispatcher.dispatch_one(
        inputs=ToolDispatchInputs(call=call, run_id="run_1", turn_id="turn_1", step_id="step_1"),
        pending_tool_events=[],
        emit_event=lambda e: events.append(e),
        emit_stream=lambda _e: None,
    )

    assert result.ok is False
    assert result.error_kind == "validation"
    assert registry.dispatch_calls == []
    assert [e.type for e in events] == ["tool_call_finished"]
    assert events[0].payload.get("result", {}).get("error_kind") == "validation"


def test_tool_dispatcher_valid_raw_arguments_emits_started_and_dispatches() -> None:
    events: List[AgentEvent] = []
    registry = _DummyRegistry()
    dispatcher = ToolDispatcher(registry=registry, now_rfc3339=lambda: "2026-01-01T00:00:00Z")

    call = ToolCall(call_id="call_ok", name="shell_exec", args={"argv": ["/bin/echo", "hi"]}, raw_arguments='{"argv":["/bin/echo","hi"]}')
    result = dispatcher.dispatch_one(
        inputs=ToolDispatchInputs(call=call, run_id="run_1", turn_id="turn_1", step_id="step_1"),
        pending_tool_events=[],
        emit_event=lambda e: events.append(e),
        emit_stream=lambda _e: None,
    )

    assert result.ok is True
    assert len(registry.dispatch_calls) == 1
    assert [e.type for e in events] == ["tool_call_started", "tool_call_finished"]


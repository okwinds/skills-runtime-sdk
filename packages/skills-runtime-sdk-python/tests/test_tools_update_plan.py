from __future__ import annotations

import json
from pathlib import Path

from agent_sdk.core.contracts import AgentEvent
from agent_sdk.tools.builtin.update_plan import update_plan
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


def _mk_ctx(tmp_path: Path, events_out: list[AgentEvent]) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="t_update_plan",
        emit_tool_events=False,
        event_sink=events_out.append,
    )


def test_update_plan_ok_one_in_progress(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(
        ToolCall(
            call_id="c1",
            name="update_plan",
            args={"plan": [{"step": "A", "status": "in_progress"}, {"step": "B", "status": "pending"}]},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["plan"][0]["status"] == "in_progress"
    assert any(e.type == "plan_updated" for e in events)


def test_update_plan_ok_zero_in_progress(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(
        ToolCall(call_id="c1", name="update_plan", args={"plan": [{"step": "A", "status": "completed"}]}), ctx
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["plan"][0]["status"] == "completed"


def test_update_plan_explanation_is_stored(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(
        ToolCall(
            call_id="c1",
            name="update_plan",
            args={"plan": [{"step": "A", "status": "pending"}], "explanation": "why"},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["explanation"] == "why"


def test_update_plan_empty_plan_is_validation(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(ToolCall(call_id="c1", name="update_plan", args={"plan": []}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_update_plan_invalid_status_is_validation(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(ToolCall(call_id="c1", name="update_plan", args={"plan": [{"step": "A", "status": "bad"}]}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_update_plan_multiple_in_progress_is_validation(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(
        ToolCall(
            call_id="c1",
            name="update_plan",
            args={"plan": [{"step": "A", "status": "in_progress"}, {"step": "B", "status": "in_progress"}]},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_update_plan_extra_fields_are_forbidden(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(
        ToolCall(call_id="c1", name="update_plan", args={"plan": [{"step": "A", "status": "pending", "x": 1}]}), ctx
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_update_plan_step_must_be_non_empty(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(ToolCall(call_id="c1", name="update_plan", args={"plan": [{"step": "", "status": "pending"}]}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_update_plan_emits_event_with_call_id(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    _ = update_plan(ToolCall(call_id="c123", name="update_plan", args={"plan": [{"step": "A", "status": "pending"}]}), ctx)
    ev = [e for e in events if e.type == "plan_updated"][0]
    assert ev.payload["call_id"] == "c123"


def test_update_plan_utf8_step_ok(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events)
    r = update_plan(
        ToolCall(call_id="c1", name="update_plan", args={"plan": [{"step": "中文步骤", "status": "pending"}]}), ctx
    )
    p = _payload(r)
    assert p["ok"] is True


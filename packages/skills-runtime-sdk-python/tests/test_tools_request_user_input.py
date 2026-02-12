from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from agent_sdk.core.contracts import AgentEvent
from agent_sdk.tools.builtin.request_user_input import request_user_input
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


class _FakeHumanIO:
    def __init__(self, answers: dict[str, str]) -> None:
        self.answers = dict(answers)
        self.calls: list[dict[str, Any]] = []

    def request_human_input(
        self,
        *,
        call_id: str,
        question: str,
        choices: Optional[list[str]],
        context: Optional[dict[str, Any]],
        timeout_ms: Optional[int],
    ) -> str:
        self.calls.append(
            {"call_id": call_id, "question": question, "choices": choices, "context": context, "timeout_ms": timeout_ms}
        )
        return self.answers.get(call_id, "default")


def _mk_ctx(tmp_path: Path, events_out: list[AgentEvent], human: Optional[_FakeHumanIO]) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="t_request_user_input",
        emit_tool_events=False,
        event_sink=events_out.append,
        human_io=human,
    )


def test_request_user_input_without_provider_is_human_required(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    ctx = _mk_ctx(tmp_path, events, None)
    r = request_user_input(
        ToolCall(call_id="c1", name="request_user_input", args={"questions": [{"id": "q1", "header": "H", "question": "Q"}]}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "human_required"
    assert any(e.type == "human_request" for e in events)


def test_request_user_input_single_question_no_options(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({"c1:q1": "ans"})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(
        ToolCall(call_id="c1", name="request_user_input", args={"questions": [{"id": "q1", "header": "H", "question": "Q"}]}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["answers"] == [{"id": "q1", "answer": "ans"}]
    assert human.calls[0]["choices"] is None


def test_request_user_input_options_are_passed_as_choices(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({"c1:q1": "A"})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(
        ToolCall(
            call_id="c1",
            name="request_user_input",
            args={
                "questions": [
                    {
                        "id": "q1",
                        "header": "H",
                        "question": "Q",
                        "options": [{"label": "A", "description": "d1"}, {"label": "B"}],
                    }
                ]
            },
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["answers"][0]["answer"] == "A"
    assert human.calls[0]["choices"] == ["A", "B"]
    assert human.calls[0]["context"]["options"][0]["description"] == "d1"


def test_request_user_input_empty_options_is_validation(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(
        ToolCall(
            call_id="c1",
            name="request_user_input",
            args={"questions": [{"id": "q1", "header": "H", "question": "Q", "options": []}]},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_request_user_input_duplicate_option_labels_is_validation(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(
        ToolCall(
            call_id="c1",
            name="request_user_input",
            args={
                "questions": [
                    {"id": "q1", "header": "H", "question": "Q", "options": [{"label": "A"}, {"label": "A"}]}
                ]
            },
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_request_user_input_multiple_questions(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({"c1:q1": "a1", "c1:q2": "a2"})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(
        ToolCall(
            call_id="c1",
            name="request_user_input",
            args={
                "questions": [
                    {"id": "q1", "header": "H1", "question": "Q1"},
                    {"id": "q2", "header": "H2", "question": "Q2"},
                ]
            },
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["answers"] == [{"id": "q1", "answer": "a1"}, {"id": "q2", "answer": "a2"}]
    assert len([e for e in events if e.type == "human_response"]) == 2


def test_request_user_input_questions_empty_is_validation(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(ToolCall(call_id="c1", name="request_user_input", args={"questions": []}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_request_user_input_missing_fields_is_validation(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(ToolCall(call_id="c1", name="request_user_input", args={"questions": [{"id": "q1"}]}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_request_user_input_extra_fields_forbidden(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(
        ToolCall(
            call_id="c1",
            name="request_user_input",
            args={"questions": [{"id": "q1", "header": "H", "question": "Q", "x": 1}]},
        ),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_request_user_input_utf8_ok(tmp_path: Path) -> None:
    events: list[AgentEvent] = []
    human = _FakeHumanIO({"c1:q1": "好"})
    ctx = _mk_ctx(tmp_path, events, human)
    r = request_user_input(
        ToolCall(call_id="c1", name="request_user_input", args={"questions": [{"id": "q1", "header": "头", "question": "问？"}]}),
        ctx,
    )
    p = _payload(r)
    assert p["ok"] is True
    assert p["data"]["answers"][0]["answer"] == "好"


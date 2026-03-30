from __future__ import annotations

import json
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.tools.protocol import ToolCall


def test_agent_ask_human_without_provider_enters_waiting_human(tmp_path: Path) -> None:
    args = {"question": "需要你确认下一步"}
    call = ToolCall(call_id="c1", name="ask_human", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="should-not-reach"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, human_io=None)
    result = agent.run("ask user for confirmation")

    assert result.status == "waiting_human"
    assert result.wal_locator

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    event_types = [e.type for e in events]
    assert "human_request" in event_types
    assert "run_waiting_human" in event_types
    assert "run_failed" not in event_types
    assert "run_completed" not in event_types

    terminal = [e for e in events if e.type == "run_waiting_human"]
    assert terminal
    assert terminal[-1].payload["tool"] == "ask_human"
    assert terminal[-1].payload["call_id"] == "c1"
    assert terminal[-1].payload["error_kind"] == "human_required"
    assert result.final_output == terminal[-1].payload["message"]


def test_agent_request_user_input_without_provider_enters_waiting_human(tmp_path: Path) -> None:
    args = {
        "questions": [
            {
                "id": "q1",
                "header": "Environment",
                "question": "请选择运行环境",
                "options": [{"label": "dev", "description": "开发环境"}],
            }
        ]
    }
    call = ToolCall(call_id="c1", name="request_user_input", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            )
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, human_io=None)
    result = agent.run("request structured human input")

    assert result.status == "waiting_human"

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    event_types = [e.type for e in events]
    assert "human_request" in event_types
    assert "run_waiting_human" in event_types
    assert "run_failed" not in event_types

    terminal = [e for e in events if e.type == "run_waiting_human"]
    assert terminal
    assert terminal[-1].payload["tool"] == "request_user_input"
    assert terminal[-1].payload["error_kind"] == "human_required"

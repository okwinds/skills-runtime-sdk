from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.state.fork import fork_run
from skills_runtime.state.replay import rebuild_resume_replay_state
from skills_runtime.tools.protocol import ToolCall


class _AssertHasToolMessageBackend:
    """断言 fork 后的 replay resume 能看到 assistant.tool_calls 与 tool message。"""

    def __init__(self, *, expected_tool_call_id: str) -> None:
        self._expected_tool_call_id = expected_tool_call_id

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        messages = request.messages
        assistant_tool_call_idx = None
        tool_result_idx = None
        for idx, m in enumerate(messages):
            if m.get("role") == "assistant" and isinstance(m.get("tool_calls"), list):
                tool_calls = m.get("tool_calls") or []
                if any((tc or {}).get("id") == self._expected_tool_call_id for tc in tool_calls if isinstance(tc, dict)):
                    assistant_tool_call_idx = idx
            if m.get("role") == "tool" and m.get("tool_call_id") == self._expected_tool_call_id:
                tool_result_idx = idx
        assert assistant_tool_call_idx is not None, "expected assistant.tool_calls in replayed history"
        assert tool_result_idx is not None, "expected tool message in replayed history"
        assert assistant_tool_call_idx < tool_result_idx
        yield ChatStreamEvent(type="text_delta", text="ok")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_fork_run_then_replay_resume(tmp_path: Path) -> None:
    src_run_id = "run_src"
    dst_run_id = "run_forked"

    overlay = tmp_path / "runtime.yaml"
    overlay.write_text("run:\n  resume_strategy: replay\n", encoding="utf-8")

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc1", name="list_dir", args={"dir_path": "."})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="done"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )
    Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay]).run("t", run_id=src_run_id)

    src_events = tmp_path / ".skills_runtime_sdk" / "runs" / src_run_id / "events.jsonl"
    assert src_events.exists()

    # 找到 tool_call_finished 的行号（0-based）作为 fork 点
    tool_finished_idx = None
    for idx, raw in enumerate(src_events.read_text(encoding="utf-8").splitlines()):
        obj = json.loads(raw)
        if obj.get("type") == "tool_call_finished":
            tool_finished_idx = idx
            break
    assert tool_finished_idx is not None

    fork_run(workspace_root=tmp_path, src_run_id=src_run_id, dst_run_id=dst_run_id, up_to_index_inclusive=int(tool_finished_idx))

    backend2 = _AssertHasToolMessageBackend(expected_tool_call_id="tc1")
    r = Agent(model="fake-model", backend=backend2, workspace_root=tmp_path, config_paths=[overlay]).run("t2", run_id=dst_run_id)
    assert r.status == "completed"
    assert r.final_output == "ok"


def test_rebuild_resume_replay_state_groups_same_turn_tool_calls() -> None:
    events = [
        {"type": "run_started", "timestamp": "t0", "run_id": "r1", "payload": {}},
        {
            "type": "tool_call_requested",
            "timestamp": "t1",
            "run_id": "r1",
            "turn_id": "turn_1",
            "step_id": "step_1",
            "payload": {"call_id": "c1", "tool": "read_file", "arguments": {"path": "a.txt"}},
        },
        {
            "type": "tool_call_requested",
            "timestamp": "t2",
            "run_id": "r1",
            "turn_id": "turn_1",
            "step_id": "step_2",
            "payload": {"call_id": "c2", "tool": "grep_files", "arguments": {"pattern": "x"}},
        },
    ]

    state = rebuild_resume_replay_state([AgentEvent.model_validate(ev) for ev in events])

    assert len(state.history) == 1
    assert state.history[0]["role"] == "assistant"
    tool_calls = state.history[0]["tool_calls"]
    assert [tool_call["id"] for tool_call in tool_calls] == ["c1", "c2"]

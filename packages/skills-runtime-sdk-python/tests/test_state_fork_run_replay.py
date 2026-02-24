from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.llm.protocol import ChatRequest
from agent_sdk.state.fork import fork_run
from agent_sdk.tools.protocol import ToolCall


class _AssertHasToolMessageBackend:
    """断言 fork 后的 replay resume 能看到 tool message。"""

    def __init__(self, *, expected_tool_call_id: str) -> None:
        self._expected_tool_call_id = expected_tool_call_id

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        messages = request.messages
        found = False
        for m in messages:
            if m.get("role") == "tool" and m.get("tool_call_id") == self._expected_tool_call_id:
                found = True
                break
        assert found, "expected tool message in replayed history"
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

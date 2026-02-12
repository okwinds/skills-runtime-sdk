from __future__ import annotations

from pathlib import Path

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.tools.protocol import ToolCall


def test_agent_custom_tool_decorator_registers_and_dispatches(tmp_path: Path) -> None:
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="c1", name="add", args={"x": 1, "y": 2}, raw_arguments='{"x":1,"y":2}')],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path)

    @agent.tool
    def add(x: int, y: int) -> int:
        """add two ints"""

        return x + y

    result = agent.run("use add tool")
    assert result.final_output == "ok"

    events = list(JsonlWal(Path(result.events_path)).iter_events())
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["tool"] == "add"
    assert finished[0].payload["result"]["stdout"] == "3"


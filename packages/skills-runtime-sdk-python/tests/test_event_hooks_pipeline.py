from __future__ import annotations

import json
from pathlib import Path
from typing import List

from agent_sdk import Agent
from agent_sdk.core.contracts import AgentEvent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.state.wal_protocol import InMemoryWal
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.state.wal_emitter import WalEmitter


def test_wal_emitter_pipeline_order_emit_and_stream_only() -> None:
    """
    断言事件管道顺序稳定：WAL append → hooks → stream。

    覆盖两条路径：
    - emit：append + hooks + stream
    - stream_only：hooks + stream（不重复 append）
    """

    wal = InMemoryWal()
    marks: List[str] = []

    def _hook(ev: AgentEvent) -> None:
        _ = ev
        # hooks 必须在 stream 之前被调用
        assert "stream" not in marks
        marks.append("hook")

    def _stream(ev: AgentEvent) -> None:
        # stream 必须发生在 hooks 之后，且 WAL 已经包含该事件
        assert marks and marks[-1] == "hook"
        assert any(e.to_json() == ev.to_json() for e in wal.iter_events())
        marks.append("stream")

    emitter = WalEmitter(wal=wal, stream=_stream, hooks=[_hook])
    ev = AgentEvent(type="unit_test_event", ts="2026-01-01T00:00:00Z", run_id="r1", payload={"x": 1})

    emitter.emit(ev)
    assert marks == ["hook", "stream"]
    assert sum(1 for e in wal.iter_events() if e.type == "unit_test_event") == 1

    # 旁路事件：不应重复落 WAL，但仍需触发 hooks + stream
    marks.clear()
    emitter.stream_only(ev)
    assert marks == ["hook", "stream"]
    assert sum(1 for e in wal.iter_events() if e.type == "unit_test_event") == 1


def test_event_hooks_receive_tool_side_events_once_and_in_stream_order(tmp_path: Path) -> None:
    """
    tool 旁路事件（ctx.emit_event）必须：
    - WAL 仅追加一次
    - hooks 仅收到一次
    - hooks 的顺序与 run_stream 输出顺序一致
    """

    wal = InMemoryWal()
    hook_types: List[str] = []

    def _hook(ev: AgentEvent) -> None:
        hook_types.append(ev.type)

    call_args = {"plan": [{"step": "s1", "status": "completed"}], "explanation": "ok"}
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="c1",
                                name="update_plan",
                                args=call_args,
                                raw_arguments=json.dumps(call_args, ensure_ascii=False),
                            )
                        ],
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="ok"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=tmp_path,
        wal_backend=wal,
        event_hooks=[_hook],
    )

    stream_events = list(agent.run_stream("do update_plan"))
    stream_types = [e.type for e in stream_events]

    assert hook_types == stream_types
    assert any(e.type == "run_completed" for e in stream_events)
    assert sum(1 for e in wal.iter_events() if e.type == "plan_updated") == 1


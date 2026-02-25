from __future__ import annotations

from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.state.wal_protocol import InMemoryWal
from skills_runtime.tools.protocol import ToolCall


class _AssertReplayHistoryBackend:
    """
    断言 replay resume：从 WAL 重建 history，而不是注入 summary。

    说明：
    - 本用例用于 injected WAL（InMemoryWal）场景，语义限定为同进程共享同一个 wal_backend 实例。
    """

    def __init__(self, *, expected_tool_call_id: str, response_text: str) -> None:
        self._expected_tool_call_id = expected_tool_call_id
        self._response_text = response_text

    async def stream_chat(self, request: ChatRequest):  # type: ignore[no-untyped-def]
        messages = request.messages
        # 不应再看到 Phase 2 的 resume summary 注入。
        for m in messages:
            if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                assert "[Resume Summary]" not in str(m.get("content"))

        found_tool = False
        for m in messages:
            if m.get("role") != "tool":
                continue
            if m.get("tool_call_id") != self._expected_tool_call_id:
                continue
            content = m.get("content")
            if isinstance(content, str) and "\"ok\"" in content:
                found_tool = True
                break
        assert found_tool, "expected tool message reconstructed from WAL"

        yield ChatStreamEvent(type="text_delta", text=self._response_text)
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_resume_replay_reconstructs_history_from_injected_inmemory_wal(tmp_path: Path) -> None:
    run_id = "run_replay_inmem_1"

    overlay = tmp_path / "runtime.yaml"
    overlay.write_text("run:\n  resume_strategy: replay\n", encoding="utf-8")

    wal = InMemoryWal(locator_str="wal://in-memory/replay-test")

    backend1 = FakeChatBackend(
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
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="first-output"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )
    r1 = Agent(
        model="fake-model",
        backend=backend1,
        workspace_root=tmp_path,
        config_paths=[overlay],
        wal_backend=wal,
    ).run("task-1", run_id=run_id)
    assert r1.status == "completed"
    assert r1.final_output == "first-output"

    backend2 = _AssertReplayHistoryBackend(expected_tool_call_id="tc1", response_text="second-output")
    r2 = Agent(
        model="fake-model",
        backend=backend2,
        workspace_root=tmp_path,
        config_paths=[overlay],
        wal_backend=wal,
    ).run("task-2", run_id=run_id)
    assert r2.status == "completed"
    assert r2.final_output == "second-output"

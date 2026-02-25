from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolCall


class _AssertReplayHistoryBackend:
    """
    断言 Phase 4 replay resume：从 WAL 重建 history，而不是注入 summary。
    """

    def __init__(self, *, expected_tool_call_id: str, response_text: str) -> None:
        self._expected_tool_call_id = expected_tool_call_id
        self._response_text = response_text

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        # 不应再看到 Phase 2 的 resume summary 注入。
        for m in request.messages:
            if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                assert "[Resume Summary]" not in str(m.get("content"))

        # 必须能看到之前的 tool message（由 tool_call_finished 事件重建）。
        found_tool = False
        for m in request.messages:
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


def test_agent_resume_replay_reconstructs_history_from_wal(tmp_path: Path) -> None:
    run_id = "run_replay_1"

    overlay = tmp_path / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  resume_strategy: replay",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    # 第一次运行：产生 WAL（包含 tool_call_finished + run_completed）
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
    agent1 = Agent(model="fake-model", backend=backend1, workspace_root=tmp_path, config_paths=[overlay])
    r1 = agent1.run("task-1", run_id=run_id)
    assert r1.status == "completed"
    assert r1.final_output == "first-output"

    # 第二次运行：新进程/新实例，但同 run_id；应走 replay resume
    backend2 = _AssertReplayHistoryBackend(expected_tool_call_id="tc1", response_text="second-output")
    agent2 = Agent(model="fake-model", backend=backend2, workspace_root=tmp_path, config_paths=[overlay])
    r2 = agent2.run("task-2", run_id=run_id)
    assert r2.status == "completed"
    assert r2.final_output == "second-output"


def test_agent_resume_replay_restores_approved_for_session_cache(tmp_path: Path) -> None:
    """
    回归（BL-007 完整性）：replay resume 需要尽量恢复 approvals cache，避免重启后重复 ask。

    断言：
    - 第一次 run：ApprovalProvider 返回 APPROVED_FOR_SESSION，并写入 approval_decided 到 WAL；
    - 第二次 run（同 run_id + replay）：即使 ApprovalProvider “爆炸”，也不应被调用；
      SDK 应使用 replay 恢复的缓存直接走 cached 路径，并继续执行工具。
    """

    from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
    from skills_runtime.state.jsonl_wal import JsonlWal

    class _ApproveForSessionOnce(ApprovalProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
            self.calls += 1
            return ApprovalDecision.APPROVED_FOR_SESSION

    class _ExplodeIfCalled(ApprovalProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
            self.calls += 1
            raise RuntimeError("should not be called when replay approvals cache is restored")

    run_id = "run_replay_approvals_1"

    overlay = tmp_path / "runtime.yaml"
    overlay.write_text("run:\n  resume_strategy: replay\n", encoding="utf-8")

    args = {"path": "approved.txt", "content": "v1", "create_dirs": True}
    call = ToolCall(call_id="c1", name="file_write", args=args)
    backend1 = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="done-1"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    ap1 = _ApproveForSessionOnce()
    r1 = Agent(
        model="fake-model",
        backend=backend1,
        workspace_root=tmp_path,
        approval_provider=ap1,
        config_paths=[overlay],
    ).run("t1", run_id=run_id)
    assert r1.status == "completed"
    assert ap1.calls == 1
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "v1"

    # 第二次 run：同一 run_id + replay。
    # 注意：approval_key 基于“稳定 request”计算；若 args 变化（例如 content 不同），理应重新 ask。
    # 因此本回归用例保持 request 不变，验证缓存能跨进程恢复并生效。
    args2 = {"path": "approved.txt", "content": "v1", "create_dirs": True}
    call2 = ToolCall(call_id="c2", name="file_write", args=args2)
    backend2 = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call2], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="done-2"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )
    ap2 = _ExplodeIfCalled()
    r2 = Agent(
        model="fake-model",
        backend=backend2,
        workspace_root=tmp_path,
        approval_provider=ap2,
        config_paths=[overlay],
    ).run("t2", run_id=run_id)
    assert r2.status == "completed"
    assert r2.final_output == "done-2"
    assert ap2.calls == 0
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "v1"

    # WAL 侧也应记录第二次 run 的 "cached"（验证行为可观测）
    events = list(JsonlWal(Path(r2.wal_locator)).iter_events())
    last_run_started_idx = -1
    for i, ev in enumerate(events):
        if ev.type == "run_started":
            last_run_started_idx = i
    seg = events[last_run_started_idx + 1 :] if last_run_started_idx >= 0 else events
    decided = [e for e in seg if e.type == "approval_decided"]
    assert decided
    assert decided[-1].payload.get("reason") == "cached"

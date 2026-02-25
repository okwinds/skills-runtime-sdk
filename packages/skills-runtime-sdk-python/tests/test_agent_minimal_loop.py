from __future__ import annotations

import json
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.tools.protocol import ToolCall


class _AlwaysApprove(ApprovalProvider):
    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        return ApprovalDecision.APPROVED


class _AlwaysDeny(ApprovalProvider):
    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        return ApprovalDecision.DENIED


def test_agent_minimal_loop_executes_tool_and_completes(tmp_path: Path) -> None:
    args = {"path": "hello.txt", "content": "hi", "create_dirs": True}
    call = ToolCall(call_id="c1", name="file_write", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

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
                    ChatStreamEvent(type="text_delta", text="done"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysApprove())
    result = agent.run("write a file")

    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"
    assert result.final_output == "done"

    wal_path = Path(result.wal_locator)
    assert wal_path.exists()

    events = list(JsonlWal(wal_path).iter_events())
    assert any(e.type == "run_started" for e in events)
    assert any(e.type == "tool_call_requested" for e in events)
    assert any(e.type == "tool_call_finished" for e in events)
    assert any(e.type == "run_completed" for e in events)


def test_agent_denied_approval_does_not_execute_tool(tmp_path: Path) -> None:
    args = {"path": "blocked.txt", "content": "hi", "create_dirs": True}
    call = ToolCall(call_id="c1", name="file_write", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysDeny())
    result = agent.run("try write")

    assert not (tmp_path / "blocked.txt").exists()
    assert result.wal_locator

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished
    assert finished[0].payload["result"]["error_kind"] == "permission"


def test_agent_no_approval_provider_fails_fast_when_approval_required(tmp_path: Path) -> None:
    """
    当某 tool 需要 approval 但未配置 ApprovalProvider 时：
    - 应避免模型进入无意义的反复重试循环
    - 直接以 config_error fail-fast（并写入 run_failed）
    """

    args = {"argv": ["/bin/echo", "hi"]}
    call = ToolCall(call_id="c1", name="shell_exec", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            # should not reach
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="should-not-reach"), ChatStreamEvent(type="completed")]),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=None)
    result = agent.run("try shell")

    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload.get("error_kind") == "config_error"


def test_agent_repeated_denied_approval_aborts_to_prevent_loop(tmp_path: Path) -> None:
    """
    同一 approval_key 被重复 denied 时，SDK 应中止 run，避免无限循环。
    """

    args = {"argv": ["/bin/echo", "hi"]}
    call = ToolCall(call_id="c1", name="shell_exec", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

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
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=_AlwaysDeny())
    result = agent.run("retry denied")

    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload.get("error_kind") == "approval_denied"

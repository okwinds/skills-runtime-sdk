from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from agent_sdk import Agent
from agent_sdk.core.collab_manager import ChildAgentContext, CollabManager
from agent_sdk.core.exec_sessions import ExecSessionManager
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.tools.protocol import ToolCall


class _AlwaysApprove(ApprovalProvider):
    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        _ = request
        _ = timeout_ms
        return ApprovalDecision.APPROVED


def _get_tool_finished_result(*, events_path: Path, tool: str) -> dict:
    for e in JsonlWal(events_path).iter_events():
        if e.type != "tool_call_finished":
            continue
        payload = e.payload or {}
        if payload.get("tool") != tool:
            continue
        return dict(payload.get("result") or {})
    raise AssertionError(f"missing tool_call_finished for tool={tool}")


def test_agent_exec_sessions_injection_allows_exec_command(tmp_path: Path) -> None:
    args = {"cmd": "echo INJECT_OK", "yield_time_ms": 200, "tty": True, "sandbox": "none"}
    call = ToolCall(call_id="c1", name="exec_command", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="done"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=tmp_path,
        approval_provider=_AlwaysApprove(),
        exec_sessions=ExecSessionManager(),
    )
    r = agent.run("run exec_command")
    assert r.status == "completed"

    result = _get_tool_finished_result(events_path=Path(r.events_path), tool="exec_command")
    assert result.get("ok") is True


def test_agent_collab_manager_injection_allows_spawn_and_wait(tmp_path: Path) -> None:
    def runner(message: str, ctx: ChildAgentContext) -> str:
        _ = message
        if ctx.cancel_event.is_set():
            return "cancelled"
        return "child_ok"

    mgr = CollabManager(runner=runner)

    spawn_args = {"message": "hello child", "agent_type": "default"}
    spawn_call = ToolCall(call_id="c_spawn", name="spawn_agent", args=spawn_args, raw_arguments=json.dumps(spawn_args, ensure_ascii=False))
    backend_spawn = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[spawn_call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="spawned"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )
    agent1 = Agent(
        model="fake-model",
        backend=backend_spawn,
        workspace_root=tmp_path,
        approval_provider=_AlwaysApprove(),
        collab_manager=mgr,
    )
    r1 = agent1.run("spawn")
    assert r1.status == "completed"

    spawn_result = _get_tool_finished_result(events_path=Path(r1.events_path), tool="spawn_agent")
    assert spawn_result.get("ok") is True
    child_id: Optional[str] = None
    data = spawn_result.get("data") or {}
    if isinstance(data, dict):
        child_id = data.get("id")
    assert isinstance(child_id, str) and child_id

    wait_args = {"ids": [child_id], "timeout_ms": 5000}
    wait_call = ToolCall(call_id="c_wait", name="wait", args=wait_args, raw_arguments=json.dumps(wait_args, ensure_ascii=False))
    backend_wait = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[wait_call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="waited"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )
    agent2 = Agent(
        model="fake-model",
        backend=backend_wait,
        workspace_root=tmp_path,
        approval_provider=None,
        collab_manager=mgr,
    )
    r2 = agent2.run("wait")
    assert r2.status == "completed"

    wait_result = _get_tool_finished_result(events_path=Path(r2.events_path), tool="wait")
    assert wait_result.get("ok") is True
    results = (wait_result.get("data") or {}).get("results")  # type: ignore[union-attr]
    assert isinstance(results, list) and results


from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from skills_runtime.core.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall, ToolSpec


class _Backend:
    def __init__(self, tool_call: ToolCall) -> None:
        self._call = tool_call
        self._count = 0

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # type: ignore[override]
        _ = request
        # 第一次请求：要求调用 tool
        if self._count == 0:
            self._count += 1
            yield ChatStreamEvent(type="tool_calls", tool_calls=[self._call], finish_reason="tool_calls")
            yield ChatStreamEvent(type="completed", finish_reason="tool_calls")
            return

        # 第二次请求：直接结束（避免 agent 反复触发同一 tool_call 进入循环）
        yield ChatStreamEvent(type="text_delta", text="done")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


class _ApproveAll(ApprovalProvider):
    async def request_approval(  # type: ignore[override]
        self,
        *,
        request: ApprovalRequest,
        timeout_ms: Optional[int] = None,
    ) -> ApprovalDecision:
        return ApprovalDecision.APPROVED


def _event_text(events: list[Any]) -> str:
    return "\n".join(e.to_json() for e in events)


def test_approval_request_for_shell_exec_does_not_include_env_values(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    secret = "SECRET_VALUE_123"
    call = ToolCall(
        call_id="c1",
        name="shell_exec",
        args={"argv": ["python", "-c", "print('ok')"], "env": {"OPENAI_API_KEY": secret}},
        raw_arguments=None,
    )
    agent = Agent(backend=_Backend(call), workspace_root=tmp_path, approval_provider=_ApproveAll())
    events = list(agent.run_stream("run"))

    approval = next(e for e in events if e.type == "approval_requested")
    req = approval.payload["request"]
    assert "env" not in req
    assert req.get("env_keys") == ["OPENAI_API_KEY"]

    # secret 不得出现在任何事件 JSON 中
    assert secret not in _event_text(events)


def test_approval_request_for_file_write_does_not_include_content(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    secret = "SECRET_IN_FILE"
    content = f"hello\n{secret}\n"
    call = ToolCall(
        call_id="c1",
        name="file_write",
        args={"path": "a.txt", "content": content, "create_dirs": True},
        raw_arguments=None,
    )
    agent = Agent(backend=_Backend(call), workspace_root=tmp_path, approval_provider=_ApproveAll())
    events = list(agent.run_stream("write"))

    approval = next(e for e in events if e.type == "approval_requested")
    req = approval.payload["request"]
    assert req.get("path") == "a.txt"
    assert "content" not in req
    assert isinstance(req.get("bytes"), int)
    assert isinstance(req.get("content_sha256"), str)

    assert secret not in _event_text(events)


def test_approval_summary_mentions_action(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    call = ToolCall(call_id="c1", name="shell_exec", args={"argv": ["echo", "hi"]}, raw_arguments=None)
    agent = Agent(backend=_Backend(call), workspace_root=tmp_path, approval_provider=_ApproveAll())
    events = list(agent.run_stream("x"))
    approval = next(e for e in events if e.type == "approval_requested")
    summary = str(approval.payload.get("summary") or "")
    assert "shell_exec" in summary
    assert "echo" in summary


def test_approval_request_for_apply_patch_does_not_include_patch_input(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    secret = "PATCH_SECRET_456"
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: a.txt",
            f"+hello {secret}",
            "*** End Patch",
            "",
        ]
    )
    call = ToolCall(call_id="c1", name="apply_patch", args={"input": patch}, raw_arguments=None)
    agent = Agent(backend=_Backend(call), workspace_root=tmp_path, approval_provider=_ApproveAll())
    events = list(agent.run_stream("patch"))

    approval = next(e for e in events if e.type == "approval_requested")
    req = approval.payload["request"]
    assert "input" not in req
    assert isinstance(req.get("bytes"), int)
    assert isinstance(req.get("content_sha256"), str)

    assert secret not in _event_text(events)

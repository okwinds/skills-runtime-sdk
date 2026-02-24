from __future__ import annotations

import json
import asyncio
from pathlib import Path

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalRequest
from agent_sdk.safety.rule_approvals import ApprovalRule, RuleBasedApprovalProvider
from agent_sdk.tools.protocol import ToolCall


def test_rule_based_provider_fail_closed_by_default() -> None:
    provider = RuleBasedApprovalProvider(rules=[])
    req = ApprovalRequest(approval_key="k", tool="file_write", summary="s", details={"path": "a"})
    decision = asyncio.run(provider.request_approval(request=req, timeout_ms=1))
    assert decision == ApprovalDecision.DENIED


def test_rule_based_provider_matches_tool_and_condition() -> None:
    matched = []

    def _cond(r: ApprovalRequest) -> bool:
        matched.append(r.tool)
        return r.details.get("path") == "ok.txt"

    provider = RuleBasedApprovalProvider(
        rules=[
            ApprovalRule(tool="file_write", condition=_cond, decision=ApprovalDecision.APPROVED),
        ]
    )
    req = ApprovalRequest(approval_key="k", tool="file_write", summary="s", details={"path": "ok.txt"})
    decision = asyncio.run(provider.request_approval(request=req, timeout_ms=1))
    assert matched == ["file_write"]
    assert decision == ApprovalDecision.APPROVED


def test_rule_condition_exception_treated_as_no_match() -> None:
    def _boom(_: ApprovalRequest) -> bool:
        raise RuntimeError("boom")

    provider = RuleBasedApprovalProvider(
        rules=[ApprovalRule(tool="file_write", condition=_boom, decision=ApprovalDecision.APPROVED)],
        default=ApprovalDecision.DENIED,
    )
    req = ApprovalRequest(approval_key="k", tool="file_write", summary="s", details={"path": "ok.txt"})
    decision = asyncio.run(provider.request_approval(request=req, timeout_ms=1))
    assert decision == ApprovalDecision.DENIED


def test_agent_loop_can_use_rule_based_provider_to_approve_file_write(tmp_path: Path) -> None:
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

    provider = RuleBasedApprovalProvider(
        rules=[
            ApprovalRule(
                tool="file_write",
                condition=lambda r: r.tool == "file_write" and r.details.get("path") == "hello.txt",
                decision=ApprovalDecision.APPROVED,
            )
        ]
    )
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, approval_provider=provider)
    result = agent.run("write a file", run_id="run_rules_1")

    assert result.status == "completed"
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"

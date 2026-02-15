"""
审批与安全策略示例（离线，可回归）。

本示例覆盖：
- safety.mode=ask 时，file_write 会触发 approval_requested/approval_decided；
- ApprovalDecision.APPROVED_FOR_SESSION 会写入 session cache，第二次相同动作命中 cached；
- ApprovalDecision.DENIED 会返回 tool result error_kind=permission（工具不执行）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall


class _ScriptedApprovalProvider(ApprovalProvider):
    """按次数返回预置的审批决策（用于离线回归与演示）。"""

    def __init__(self, decisions: list[ApprovalDecision]) -> None:
        self._decisions = list(decisions)
        self.calls: list[ApprovalRequest] = []

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:
        self.calls.append(request)
        if self._decisions:
            return self._decisions.pop(0)
        return ApprovalDecision.DENIED


def _write_overlay(workspace_root: Path, *, safety_mode: str) -> Path:
    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 20",
                "safety:",
                f"  mode: {json.dumps(safety_mode)}",
                "  approval_timeout_ms: 2000",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return overlay


def _run_case_approved_for_session_cache(workspace_root: Path) -> None:
    """
    案例 1：APPROVED_FOR_SESSION 缓存语义。

    期望：
    - 只调用一次 provider（第 2 次命中 cached）
    - 两次 file_write 都成功（第二次仍会执行写入，但不再 ask）
    """

    workspace_root.mkdir(parents=True, exist_ok=True)
    overlay = _write_overlay(workspace_root, safety_mode="ask")

    provider = _ScriptedApprovalProvider([ApprovalDecision.APPROVED_FOR_SESSION])
    backend = FakeChatBackend(
        calls=[
            # 第 1 次：请求写入同一文件
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_w1",
                                name="file_write",
                                args={"path": "out.txt", "content": "hello"},
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            # 第 2 次：再次请求相同写入（应命中 cached）
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_w2",
                                name="file_write",
                                args={"path": "out.txt", "content": "hello"},
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="done"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=provider,
    )
    r = agent.run("write twice", run_id="run_example_step_03_cache")
    assert r.status == "completed"
    assert (workspace_root / "out.txt").read_text(encoding="utf-8") == "hello"
    assert len(provider.calls) == 1, f"expected 1 provider call, got {len(provider.calls)}"
    print("[case] approved_for_session_cache: ok")


def _run_case_denied(workspace_root: Path) -> None:
    """案例 2：DENIED 分支（工具不执行，但 run 仍可继续到最终输出）。"""

    workspace_root.mkdir(parents=True, exist_ok=True)
    overlay = _write_overlay(workspace_root, safety_mode="ask")

    provider = _ScriptedApprovalProvider([ApprovalDecision.DENIED])
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_w1", name="file_write", args={"path": "deny.txt", "content": "no"})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="handled"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=provider,
    )
    r = agent.run("denied", run_id="run_example_step_03_denied")
    assert r.status == "completed"
    assert not (workspace_root / "deny.txt").exists()
    assert len(provider.calls) == 1
    print("[case] denied: ok")


def main() -> int:
    """脚本入口：依次运行两个审批案例。"""

    parser = argparse.ArgumentParser(description="03_approvals_and_safety (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    root = Path(args.workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)

    _run_case_approved_for_session_cache(root / "case_cache")
    _run_case_denied(root / "case_denied")

    print("EXAMPLE_OK: step_by_step_03")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
多 Agent references 驱动流水线示例（Skills-First，离线可回归）。

演示：
- skill_ref_read：读取 skill bundle 的 references/policy.md（需要开启 skills.references.enabled）
- 多 agent 协作：Policy → Patch → QA → Report
- apply_patch/shell_exec/file_write：保留 approvals/WAL 审计链路
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_sdk import Agent, Coordinator
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall


def _write_demo_project(workspace_root: Path) -> None:
    """写入最小 demo 项目文件（app.py），用于演示 patch 与 QA。"""

    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "app.py").write_text(
        "\n".join(
            [
                "def add(a: int, b: int) -> int:",
                "    \"\"\"Return a+b.\"\"\"",
                "    # BUG: wrong operator",
                "    return a - b",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """
    写入示例运行用 overlay（runtime.yaml），并开启 skills.references.enabled。

    参数：
    - workspace_root：工作区根目录
    - skills_root：filesystem skills root（本示例目录下 `skills/`）
    - safety_mode：allow|ask|deny（示例默认 ask）
    """

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 40",
                "safety:",
                f"  mode: {json.dumps(safety_mode)}",
                "  approval_timeout_ms: 2000",
                "sandbox:",
                "  default_policy: none",
                "skills:",
                "  mode: explicit",
                "  max_auto: 0",
                "  strictness:",
                "    unknown_mention: error",
                "    duplicate_name: error",
                "    mention_format: strict",
                "  references:",
                "    enabled: true",
                "    allow_assets: false",
                "  spaces:",
                "    - id: wf-space",
                "      account: examples",
                "      domain: workflow",
                "      sources: [wf-fs]",
                "      enabled: true",
                "  sources:",
                "    - id: wf-fs",
                "      type: filesystem",
                "      options:",
                f"        root: {json.dumps(str(skills_root.resolve()))}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return overlay


class _ScriptedApprovalProvider(ApprovalProvider):
    """按次数返回预置审批决策（用于离线回归与演示）。"""

    def __init__(self, decisions: List[ApprovalDecision]) -> None:
        self._decisions = list(decisions)
        self.calls: List[ApprovalRequest] = []

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:
        _ = timeout_ms
        self.calls.append(request)
        if self._decisions:
            return self._decisions.pop(0)
        return ApprovalDecision.DENIED


def _load_events(events_path: str) -> List[Dict[str, Any]]:
    """读取 WAL（events.jsonl）并返回 JSON object 列表。"""

    p = Path(events_path)
    if not p.exists():
        raise AssertionError(f"events_path does not exist: {events_path}")
    events: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _assert_tool_ok(*, events_path: str, tool_name: str) -> None:
    """断言 WAL 中某个 tool 的 tool_call_finished 存在且 ok=true。"""

    events = _load_events(events_path)
    for ev in events:
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return
    raise AssertionError(f"missing ok tool_call_finished for tool={tool_name}")


def _build_policy_backend() -> FakeChatBackend:
    """
    Policy backend：读取 policy 引用，并输出简短摘要。
    """

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_policy_ref",
                                name="skill_ref_read",
                                args={
                                    "skill_mention": "$[examples:workflow].workflow_policy",
                                    "ref_path": "references/policy.md",
                                    "max_bytes": 8192,
                                },
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已读取 policy.md：将按“最小改动 + 可回归 + 提供证据指针”执行后续步骤。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_patch_backend() -> FakeChatBackend:
    """Patch backend：apply_patch 修复 app.py 的 add。"""

    patch_text = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: app.py",
            "@@",
            " def add(a: int, b: int) -> int:",
            "     \"\"\"Return a+b.\"\"\"",
            "     # BUG: wrong operator",
            "-    return a - b",
            "+    return a + b",
            "*** End Patch",
            "",
        ]
    )

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_apply_patch", name="apply_patch", args={"input": patch_text})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已按 policy 执行最小改动：修复 add 的运算符。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_qa_backend(*, python_executable: str) -> FakeChatBackend:
    """QA backend：shell_exec 断言 add 结果并打印 QA_OK。"""

    argv = [str(python_executable), "-c", "import app; assert app.add(1,2)==3; print('QA_OK')"]
    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_shell_qa", name="shell_exec", args={"argv": argv, "timeout_ms": 5000, "sandbox": "none"})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="QA 已完成（stdout 包含 QA_OK）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_report_backend(*, report_markdown: str) -> FakeChatBackend:
    """Report backend：file_write report.md。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_report", name="file_write", args={"path": "report.md", "content": report_markdown})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="report.md 已生成。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, policy_events_path: str, patch_events_path: str, qa_events_path: str) -> str:
    """组装 report.md（包含证据指针）。"""

    lines = [
        "# Workflow Report (References-driven)",
        "",
        "本示例展示：通过 `skill_ref_read` 读取 skill bundle 的 references，并驱动后续 patch/qa/report。",
        "",
        "## Evidence",
        "",
        f"- Policy events: `{policy_events_path}`",
        f"- Patch events: `{patch_events_path}`",
        f"- QA events: `{qa_events_path}`",
        "",
        "## Notes",
        "",
        "- policy.md 规则：最小改动、可回归、提供证据指针。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    """脚本入口：运行 workflows_03 示例。"""

    parser = argparse.ArgumentParser(description="03_multi_agent_reference_driven_pipeline (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    _write_demo_project(workspace_root)
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # apply_patch
            ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec
            ApprovalDecision.APPROVED_FOR_SESSION,  # file_write
        ]
    )

    primary = Agent(
        model="fake-model",
        backend=FakeChatBackend(calls=[FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="noop"), ChatStreamEvent(type="completed", finish_reason="stop")])]),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    policy = Agent(
        model="fake-model",
        backend=_build_policy_backend(),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    patcher = Agent(
        model="fake-model",
        backend=_build_patch_backend(),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    qa = Agent(
        model="fake-model",
        backend=_build_qa_backend(python_executable=sys.executable),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )

    coord = Coordinator(agents=[primary, policy, patcher, qa])

    policy_task = "$[examples:workflow].workflow_policy\n请读取 references/policy.md 并总结约束。"
    patch_task = "$[examples:workflow].repo_patcher\n请按 policy 约束修复 app.py。"
    qa_task = "$[examples:workflow].repo_qa\n请对修复结果做最小回归验证并输出 QA_OK。"

    r_policy = coord.run_child_task(policy_task, child_index=1)
    r_patch = coord.run_child_task(patch_task, child_index=2)
    r_qa = coord.run_child_task(qa_task, child_index=3)

    _assert_tool_ok(events_path=r_policy.events_path, tool_name="skill_ref_read")
    _assert_tool_ok(events_path=r_patch.events_path, tool_name="apply_patch")
    _assert_tool_ok(events_path=r_qa.events_path, tool_name="shell_exec")

    report_md = _format_report_md(
        policy_events_path=r_policy.events_path,
        patch_events_path=r_patch.events_path,
        qa_events_path=r_qa.events_path,
    )
    reporter = Agent(
        model="fake-model",
        backend=_build_report_backend(report_markdown=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    r_report = reporter.run("$[examples:workflow].repo_reporter\n请生成 report.md（包含证据指针）。", run_id="run_workflows_03_report")
    _assert_tool_ok(events_path=r_report.events_path, tool_name="file_write")

    report_path = workspace_root / "report.md"
    assert report_path.exists(), "report.md is not created"
    assert "Policy events" in report_path.read_text(encoding="utf-8")

    print("EXAMPLE_OK: workflows_03")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


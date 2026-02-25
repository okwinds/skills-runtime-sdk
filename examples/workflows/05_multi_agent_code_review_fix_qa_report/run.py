"""
多 Agent：Review → Fix → QA → Report（Skills-First，离线可回归）。

说明：
- reviewer 只读：read_file → 输出问题与建议；
- fixer 负责落盘：apply_patch；
- QA 负责回归：shell_exec；
- reporter 负责报告：file_write；
- 写/执行类工具全部走 approvals（ask 模式）；
- 每个角色能力通过 skill mention 注入，WAL 中有 `skill_injected` 证据。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime import Coordinator
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall


def _write_demo_project(workspace_root: Path) -> None:
    """
    写入一个最小 demo 文件（用于 review/fix/qa）。

    参数：
    - workspace_root：工作区根目录
    """

    (workspace_root / "calc.py").write_text(
        "\n".join(
            [
                "def divide(a: float, b: float) -> float:",
                "    \"\"\"Return a / b.\"\"\"",
                "    # BUG: wrong operator",
                "    return a * b",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """写入示例运行用 overlay（runtime.yaml）。"""

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
                "  strictness:",
                "    unknown_mention: error",
                "    duplicate_name: error",
                "    mention_format: strict",
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


def _load_events(wal_locator: str) -> List[Dict[str, Any]]:
    """读取 WAL（events.jsonl）并返回 JSON object 列表。"""

    p = Path(wal_locator)
    if not p.exists():
        raise AssertionError(f"wal_locator does not exist: {wal_locator}")
    events: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _assert_skill_injected(*, wal_locator: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    for ev in _load_events(wal_locator):
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_event_exists(*, wal_locator: str, event_type: str) -> None:
    """断言 WAL 中存在某类事件（至少一条）。"""

    if not any(ev.get("type") == event_type for ev in _load_events(wal_locator)):
        raise AssertionError(f"missing event type: {event_type}")


def _assert_tool_ok(*, wal_locator: str, tool_name: str) -> None:
    """断言 WAL 中某个 tool 的 `tool_call_finished` 存在且 ok=true。"""

    for ev in _load_events(wal_locator):
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return
    raise AssertionError(f"missing ok tool_call_finished for tool={tool_name}")


def _build_reviewer_backend() -> FakeChatBackend:
    """Reviewer：read_file(calc.py) → 输出 review 建议。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_read_calc", name="read_file", args={"file_path": "calc.py", "offset": 1, "limit": 200})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="text_delta",
                        text=(
                            "- BUG：divide 目标是 a / b，但实现使用了 a * b。\n"
                            "- 建议：把 return 改为 a / b；并补充 b==0 的最小异常语义（示例里可先不加）。\n"
                            "- QA：python -c 'import calc; assert calc.divide(6,2)==3'\n"
                        ),
                    ),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_fixer_backend() -> FakeChatBackend:
    """Fixer：apply_patch 修复 calc.py。"""

    patch_text = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: calc.py",
            "@@",
            " def divide(a: float, b: float) -> float:",
            "     \"\"\"Return a / b.\"\"\"",
            "     # BUG: wrong operator",
            "-    return a * b",
            "+    return a / b",
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
                    ChatStreamEvent(type="text_delta", text="已通过 apply_patch 修复 divide 的运算符。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_qa_backend(*, python_executable: str) -> FakeChatBackend:
    """QA：shell_exec 最小断言并输出 QA_OK。"""

    argv = [str(python_executable), "-c", "import calc; assert calc.divide(6,2)==3; print('QA_OK')"]
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
                    ChatStreamEvent(type="text_delta", text="QA 已完成（stdout 含 QA_OK）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, steps: List[Dict[str, Any]]) -> str:
    """组装报告 Markdown（确定性）。"""

    lines: List[str] = []
    lines.append("# Workflow Report (Review → Fix → QA → Report)")
    lines.append("")
    for s in steps:
        lines.append(f"## {s['name']}")
        lines.append(f"- Skill: `{s['mention']}`")
        lines.append(f"- Events: `{s['wal_locator']}`")
        summary = str(s.get("summary") or "").strip()
        if summary:
            lines.append("")
            lines.append("```text")
            lines.append(summary)
            lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_reporter_backend(*, report_md: str) -> FakeChatBackend:
    """Reporter：file_write(report.md)。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_report", name="file_write", args={"path": "report.md", "content": report_md})],
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


def main() -> int:
    """脚本入口：运行 workflows_05 示例。"""

    parser = argparse.ArgumentParser(description="05_multi_agent_code_review_fix_qa_report (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    _write_demo_project(workspace_root)

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
    reviewer = Agent(
        model="fake-model",
        backend=_build_reviewer_backend(),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    fixer = Agent(
        model="fake-model",
        backend=_build_fixer_backend(),
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

    coord = Coordinator(agents=[primary, reviewer, fixer, qa])

    review_task = "$[examples:workflow].repo_reviewer\n请 review calc.py 并给出最小修复建议与 QA 断言。"
    fix_task = "$[examples:workflow].repo_patcher\n请修复 calc.py 的 divide 实现，使 divide(6,2)=3。"
    qa_task = "$[examples:workflow].repo_qa\n请运行最小确定性回归验证并输出 QA_OK。"

    r_review = coord.run_child_task(review_task, child_index=1)
    r_fix = coord.run_child_task(fix_task, child_index=2)
    r_qa = coord.run_child_task(qa_task, child_index=3)

    steps = [
        {"name": "Review", "mention": "$[examples:workflow].repo_reviewer", "summary": r_review.summary, "wal_locator": r_review.wal_locator},
        {"name": "Fix", "mention": "$[examples:workflow].repo_patcher", "summary": r_fix.summary, "wal_locator": r_fix.wal_locator},
        {"name": "QA", "mention": "$[examples:workflow].repo_qa", "summary": r_qa.summary, "wal_locator": r_qa.wal_locator},
    ]
    report_md = _format_report_md(steps=steps)

    reporter = Agent(
        model="fake-model",
        backend=_build_reporter_backend(report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    report_task = "$[examples:workflow].repo_reporter\n请将本次 workflow 的结果写入 report.md。"
    r_report = reporter.run(report_task, run_id="run_workflows_05_report")

    # evidence assertions
    _assert_skill_injected(wal_locator=r_review.wal_locator, mention_text="$[examples:workflow].repo_reviewer")
    _assert_skill_injected(wal_locator=r_fix.wal_locator, mention_text="$[examples:workflow].repo_patcher")
    _assert_skill_injected(wal_locator=r_qa.wal_locator, mention_text="$[examples:workflow].repo_qa")
    _assert_skill_injected(wal_locator=r_report.wal_locator, mention_text="$[examples:workflow].repo_reporter")

    _assert_event_exists(wal_locator=r_fix.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_fix.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_fix.wal_locator, tool_name="apply_patch")

    _assert_event_exists(wal_locator=r_qa.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_qa.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_qa.wal_locator, tool_name="shell_exec")

    _assert_event_exists(wal_locator=r_report.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_report.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_report.wal_locator, tool_name="file_write")

    assert "return a / b" in (workspace_root / "calc.py").read_text(encoding="utf-8")
    assert (workspace_root / "report.md").exists()

    print("EXAMPLE_OK: workflows_05")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

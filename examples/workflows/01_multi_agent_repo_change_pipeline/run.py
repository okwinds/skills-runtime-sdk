"""
多 Agent 项目流水线示例（Skills-First，离线可回归）。

本示例演示：
- 每个角色能力都由 Skill（SKILL.md）定义，并通过 mention 注入触发 `skill_injected` 证据事件；
- 多 agent 协作执行一个最小“项目级”流水线：Analyze → Patch → QA → Report；
- Patch/QA/Report 通过 SDK builtin tools 执行（apply_patch/shell_exec/file_write），并在 ask 模式下走 approvals；
- 全流程默认离线可运行（FakeChatBackend + scripted ApprovalProvider）。
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
    """
    在 workspace_root 下创建一个最小 demo 项目文件（用于演示 patch 与 QA）。

    参数：
    - workspace_root：工作区根目录（会创建 `app.py` 与 `README.md`）。
    """

    workspace_root.mkdir(parents=True, exist_ok=True)

    (workspace_root / "README.md").write_text(
        "# Demo Project\n\nThis is a tiny demo project for the workflows example.\n",
        encoding="utf-8",
    )

    (workspace_root / "app.py").write_text(
        "\n".join(
            [
                "def is_even(n: int) -> bool:",
                "    \"\"\"Return True when n is even.\"\"\"",
                "    # BUG: wrong parity check",
                "    return n % 2 == 1",
                "",
                "if __name__ == \"__main__\":",
                "    print(\"is_even(2)=\", is_even(2))",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """
    写入示例运行用 overlay（runtime.yaml）。

    参数：
    - workspace_root：工作区根目录（overlay 写在该目录下）
    - skills_root：filesystem skills root（指向本示例目录下的 `skills/`）
    - safety_mode：allow|ask|deny（示例默认 ask）

    返回：
    - overlay 路径
    """

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 30",
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
    """
    按次数返回预置审批决策（用于离线回归与演示）。

    说明：
    - 本示例的写/执行类工具（apply_patch/shell_exec/file_write）默认会进入 approvals；
    - scripted provider 负责自动批准，避免示例阻塞在等待人类输入。
    """

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
    """
    读取 WAL（events.jsonl）并返回 JSON object 列表。

    参数：
    - events_path：WAL 路径（字符串）

    返回：
    - events：list[dict]
    """

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


def _assert_skill_injected(*, events_path: str, mention_text: str) -> None:
    """
    断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。

    参数：
    - events_path：WAL 路径
    - mention_text：完整 mention（例如 `$[examples:workflow].repo_patcher`）
    """

    events = _load_events(events_path)
    for ev in events:
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_tool_ok(*, events_path: str, tool_name: str) -> None:
    """
    断言 WAL 中某个 tool 的 `tool_call_finished` 存在且 ok=true。

    参数：
    - events_path：WAL 路径
    - tool_name：工具名（例如 apply_patch/shell_exec/file_write）
    """

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


def _assert_approvals_present(*, events_path: str) -> None:
    """
    断言 WAL 中出现 approvals 证据事件。

    参数：
    - events_path：WAL 路径
    """

    events = _load_events(events_path)
    has_req = any(ev.get("type") == "approval_requested" for ev in events)
    has_dec = any(ev.get("type") == "approval_decided" for ev in events)
    if not (has_req and has_dec):
        raise AssertionError("missing approvals evidence (approval_requested/approval_decided)")


def _build_analyze_backend() -> FakeChatBackend:
    """
    构造 Analyze 角色的 Fake backend。

    行为：
    - 第 1 次：请求 read_file(app.py)
    - 第 2 次：输出定位与修复建议
    """

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_read_app",
                                name="read_file",
                                args={"file_path": "app.py", "offset": 1, "limit": 200},
                            )
                        ],
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
                            "问题摘要：is_even 的取模判断写反了。\n"
                            "根因：return n % 2 == 1 导致偶数返回 False。\n"
                            "修复：把判断改为 n % 2 == 0。\n"
                            "QA：python -c 'import app; assert app.is_even(2) is True; assert app.is_even(3) is False'\n"
                        ),
                    ),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_patch_backend() -> FakeChatBackend:
    """
    构造 Patch 角色的 Fake backend。

    行为：
    - 第 1 次：请求 apply_patch 修复 app.py
    - 第 2 次：输出修复完成摘要
    """

    patch_text = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: app.py",
            "@@",
            " def is_even(n: int) -> bool:",
            "     \"\"\"Return True when n is even.\"\"\"",
            "     # BUG: wrong parity check",
            "-    return n % 2 == 1",
            "+    return n % 2 == 0",
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
                    ChatStreamEvent(type="text_delta", text="已通过 apply_patch 修复 app.py 的 is_even 逻辑。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_qa_backend(*, python_executable: str) -> FakeChatBackend:
    """
    构造 QA 角色的 Fake backend。

    参数：
    - python_executable：用于 shell_exec 的 python 路径（建议使用 sys.executable）

    行为：
    - 第 1 次：请求 shell_exec 执行确定性断言，并打印 QA_OK
    - 第 2 次：输出 QA 完成摘要
    """

    argv = [
        str(python_executable),
        "-c",
        "import app; assert app.is_even(2) is True; assert app.is_even(3) is False; print('QA_OK')",
    ]

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_shell_qa",
                                name="shell_exec",
                                args={"argv": argv, "timeout_ms": 5000, "sandbox": "none"},
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="QA 已执行完成（见 tool stdout 证据）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_report_backend(*, report_markdown: str) -> FakeChatBackend:
    """
    构造 Report 角色的 Fake backend。

    参数：
    - report_markdown：要写入 `report.md` 的 Markdown 文本

    行为：
    - 第 1 次：请求 file_write(report.md)
    - 第 2 次：输出完成摘要
    """

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_write_report",
                                name="file_write",
                                args={
                                    "path": "report.md",
                                    "content": report_markdown,
                                    "justification": "workflow 示例：生成本次流水线报告（离线可回归）。",
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
                    ChatStreamEvent(type="text_delta", text="report.md 已生成。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, steps: List[Dict[str, Any]]) -> str:
    """
    组装报告 Markdown。

    参数：
    - steps：步骤列表；每项必须包含 name/mention/summary/events_path

    返回：
    - Markdown 文本
    """

    lines: List[str] = []
    lines.append("# Workflow Report (Skills-First)")
    lines.append("")
    lines.append("本报告由 workflow 示例生成，用于展示 skills-first 多 agent 流水线的证据链与产物。")
    lines.append("")
    lines.append("## Steps")
    lines.append("")

    for s in steps:
        lines.append(f"### {s['name']}")
        lines.append("")
        lines.append(f"- Skill: `{s['mention']}`")
        lines.append(f"- Events: `{s['events_path']}`")
        lines.append("")
        summary = str(s.get("summary") or "").strip()
        if summary:
            lines.append("摘要：")
            lines.append("")
            lines.append("```text")
            lines.append(summary)
            lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    """脚本入口：运行多 agent workflow 示例。"""

    parser = argparse.ArgumentParser(description="01_multi_agent_repo_change_pipeline (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    if not skills_root.exists():
        raise AssertionError(f"skills root not found: {skills_root}")

    _write_demo_project(workspace_root)
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    # approvals：为写/执行类工具预置自动批准决策，保证离线可回归
    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # apply_patch (patch)
            ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec (qa)
            ApprovalDecision.APPROVED_FOR_SESSION,  # file_write (report)
        ]
    )

    # 仅用于占位的 primary agent（Coordinator 需要 agents[0] 作为主 agent）
    primary = Agent(
        model="fake-model",
        backend=FakeChatBackend(calls=[FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="noop"), ChatStreamEvent(type="completed", finish_reason="stop")])]),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )

    analyze = Agent(
        model="fake-model",
        backend=_build_analyze_backend(),
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

    coord = Coordinator(agents=[primary, analyze, patcher, qa])

    analyze_task = "$[examples:workflow].repo_analyzer\n请分析 workspace 内的 app.py 并给出修复建议。"
    patch_task = "$[examples:workflow].repo_patcher\n请修复 app.py 的 bug，并给出最小补丁。"
    qa_task = "$[examples:workflow].repo_qa\n请对修复后的 app.py 执行确定性回归验证，并输出 QA_OK。"

    r_analyze = coord.run_child_task(analyze_task, child_index=1)
    r_patch = coord.run_child_task(patch_task, child_index=2)
    r_qa = coord.run_child_task(qa_task, child_index=3)

    # 断言示例的关键证据链存在：skills 注入、工具成功、approvals 事件
    _assert_skill_injected(events_path=r_analyze.events_path, mention_text="$[examples:workflow].repo_analyzer")
    _assert_skill_injected(events_path=r_patch.events_path, mention_text="$[examples:workflow].repo_patcher")
    _assert_skill_injected(events_path=r_qa.events_path, mention_text="$[examples:workflow].repo_qa")

    _assert_approvals_present(events_path=r_patch.events_path)
    _assert_approvals_present(events_path=r_qa.events_path)

    _assert_tool_ok(events_path=r_patch.events_path, tool_name="apply_patch")
    _assert_tool_ok(events_path=r_qa.events_path, tool_name="shell_exec")

    steps = [
        {
            "name": "Analyze",
            "mention": "$[examples:workflow].repo_analyzer",
            "summary": r_analyze.summary,
            "events_path": r_analyze.events_path,
        },
        {
            "name": "Patch",
            "mention": "$[examples:workflow].repo_patcher",
            "summary": r_patch.summary,
            "events_path": r_patch.events_path,
        },
        {
            "name": "QA",
            "mention": "$[examples:workflow].repo_qa",
            "summary": r_qa.summary,
            "events_path": r_qa.events_path,
        },
    ]
    report_md = _format_report_md(steps=steps)

    reporter = Agent(
        model="fake-model",
        backend=_build_report_backend(report_markdown=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    report_task = "$[examples:workflow].repo_reporter\n请将本次 workflow 的结果写入 report.md。"
    r_report = reporter.run(report_task, run_id="run_workflows_01_report")

    _assert_skill_injected(events_path=r_report.events_path, mention_text="$[examples:workflow].repo_reporter")
    _assert_approvals_present(events_path=r_report.events_path)
    _assert_tool_ok(events_path=r_report.events_path, tool_name="file_write")

    report_path = workspace_root / "report.md"
    assert report_path.exists(), "report.md is not created"
    assert "Workflow Report" in report_path.read_text(encoding="utf-8")

    # 额外 sanity：patch 生效（避免“工具 ok 但内容没改”之类的回归）
    app_text = (workspace_root / "app.py").read_text(encoding="utf-8")
    assert "return n % 2 == 0" in app_text

    print("EXAMPLE_OK: workflows_01")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

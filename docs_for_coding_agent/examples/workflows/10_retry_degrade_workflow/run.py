"""
重试→降级→报告 示例（Skills-First，离线可回归）。

本示例演示：
- Controller：update_plan + file_write(retry_plan.json)
- Attempt：shell_exec（故意失败；失败也必须可审计）
- Degrade：file_write(outputs/fallback.md)
- Reporter：file_write(report.md)（汇总 attempts 的 exit_code + wal_locator）

核心约束：
- 每个角色能力必须由 Skill（SKILL.md）定义；
- 任务文本显式包含 mention，触发 `skill_injected` 证据事件；
- 默认离线可运行（Fake backend + scripted approvals）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """写入示例运行用 overlay（runtime.yaml）。"""

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 60",
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
    out: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


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


def _assert_tool_finished(*, wal_locator: str, tool_name: str) -> Dict[str, Any]:
    """返回某个 tool 的 tool_call_finished.result（找不到则断言失败）。"""

    for ev in _load_events(wal_locator):
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        return dict(payload.get("result") or {})
    raise AssertionError(f"missing tool_call_finished for tool={tool_name}")


def _build_controller_backend(*, retry_plan_json: str) -> FakeChatBackend:
    """Controller：update_plan → file_write(retry_plan.json) → summary。"""

    plan = {
        "explanation": "示例：重试预算（2 次）→ 失败后降级 → 汇总报告",
        "plan": [
            {"step": "定义重试预算", "status": "completed"},
            {"step": "attempt #1", "status": "pending"},
            {"step": "attempt #2", "status": "pending"},
            {"step": "降级（fallback）", "status": "pending"},
            {"step": "汇总报告", "status": "pending"},
        ],
    }

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_plan", name="update_plan", args=plan),
                            ToolCall(call_id="tc_write_plan", name="file_write", args={"path": "retry_plan.json", "content": retry_plan_json}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已写入 retry_plan.json，并更新 plan。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_attempt_backend(*, python_executable: str) -> FakeChatBackend:
    """Attempt：shell_exec（确定性失败，exit_code=2）。"""

    argv = [str(python_executable), "-c", "import sys; print('ATTEMPT_FAIL'); sys.exit(2)"]
    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_attempt", name="shell_exec", args={"argv": argv, "timeout_ms": 5000, "sandbox": "none"})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="attempt 已执行（预期失败）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_degrade_backend(*, fallback_md: str) -> FakeChatBackend:
    """Degrade：file_write(outputs/fallback.md)。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_fallback", name="file_write", args={"path": "outputs/fallback.md", "content": fallback_md})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已生成降级产物 outputs/fallback.md。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, controller_run: Dict[str, str], attempts: List[Dict[str, str]], degrade_run: Dict[str, str]) -> str:
    """组装 report.md（确定性）。"""

    lines: List[str] = []
    lines.append("# Workflow Report（重试 → 降级 → 报告 / Skills-First）")
    lines.append("")
    lines.append("本报告强调：失败也必须可审计（exit_code/证据路径/降级结论）。")
    lines.append("")

    lines.append("## Controller")
    lines.append(f"- Skill: `{controller_run['mention']}`")
    lines.append(f"- Events: `{controller_run['wal_locator']}`")
    lines.append(f"- Artifact: `{controller_run['artifact_path']}`")
    lines.append("")

    lines.append("## Attempts")
    for a in attempts:
        lines.append(f"### {a['name']}")
        lines.append(f"- Skill: `{a['mention']}`")
        lines.append(f"- Events: `{a['wal_locator']}`")
        lines.append(f"- shell_exec.exit_code: `{a['exit_code']}`")
        lines.append(f"- shell_exec.ok: `{a['ok']}`")
        lines.append("")

    lines.append("## Degrade")
    lines.append(f"- Skill: `{degrade_run['mention']}`")
    lines.append(f"- Events: `{degrade_run['wal_locator']}`")
    lines.append(f"- Artifact: `{degrade_run['artifact_path']}`")
    lines.append("")

    lines.append("结论：attempt 失败达到预算后，走降级路径并生成 fallback 产物。")
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
    """脚本入口：运行 workflows_10 示例。"""

    parser = argparse.ArgumentParser(description="10_retry_degrade_workflow (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    approval = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # controller file_write retry_plan.json
            ApprovalDecision.APPROVED_FOR_SESSION,  # attempt #1 shell_exec
            ApprovalDecision.APPROVED_FOR_SESSION,  # attempt #2 shell_exec
            ApprovalDecision.APPROVED_FOR_SESSION,  # degrade file_write
            ApprovalDecision.APPROVED_FOR_SESSION,  # reporter file_write report.md
        ]
    )

    retry_plan = {"retries": 2, "on_exhausted": "degrade", "notes": "示例：失败达到预算后写 fallback 并生成报告"}
    retry_plan_json = json.dumps(retry_plan, ensure_ascii=False, indent=2) + "\n"

    # 1) Controller
    controller = Agent(
        model="fake-model",
        backend=_build_controller_backend(retry_plan_json=retry_plan_json),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    controller_task = "$[examples:workflow].retry_controller\n请定义重试预算为 2 次，并写入 retry_plan.json。"
    r_controller = controller.run(controller_task, run_id="run_workflows_10_controller")

    _assert_skill_injected(wal_locator=r_controller.wal_locator, mention_text="$[examples:workflow].retry_controller")
    _assert_event_exists(wal_locator=r_controller.wal_locator, event_type="plan_updated")
    _assert_event_exists(wal_locator=r_controller.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_controller.wal_locator, event_type="approval_decided")
    assert (workspace_root / "retry_plan.json").exists()

    # 2) Attempt #1
    attempt1 = Agent(
        model="fake-model",
        backend=_build_attempt_backend(python_executable=sys.executable),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    a1_task = "$[examples:workflow].attempt_worker\n执行 attempt #1：运行一个确定性失败的命令（exit_code=2）。"
    r_a1 = attempt1.run(a1_task, run_id="run_workflows_10_attempt_1")

    _assert_skill_injected(wal_locator=r_a1.wal_locator, mention_text="$[examples:workflow].attempt_worker")
    _assert_event_exists(wal_locator=r_a1.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_a1.wal_locator, event_type="approval_decided")
    r1 = _assert_tool_finished(wal_locator=r_a1.wal_locator, tool_name="shell_exec")
    assert int(r1.get("exit_code")) == 2

    # 3) Attempt #2
    attempt2 = Agent(
        model="fake-model",
        backend=_build_attempt_backend(python_executable=sys.executable),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    a2_task = "$[examples:workflow].attempt_worker\n执行 attempt #2：同样运行一个确定性失败的命令（exit_code=2）。"
    r_a2 = attempt2.run(a2_task, run_id="run_workflows_10_attempt_2")

    _assert_skill_injected(wal_locator=r_a2.wal_locator, mention_text="$[examples:workflow].attempt_worker")
    _assert_event_exists(wal_locator=r_a2.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_a2.wal_locator, event_type="approval_decided")
    r2 = _assert_tool_finished(wal_locator=r_a2.wal_locator, tool_name="shell_exec")
    assert int(r2.get("exit_code")) == 2

    # 4) Degrade
    degrade = Agent(
        model="fake-model",
        backend=_build_degrade_backend(fallback_md="# Fallback Output\n\n降级路径：提供最小可用结果。\n"),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    degrade_task = "$[examples:workflow].degrade_worker\n重试耗尽后，请生成降级产物 outputs/fallback.md。"
    r_degrade = degrade.run(degrade_task, run_id="run_workflows_10_degrade")

    _assert_skill_injected(wal_locator=r_degrade.wal_locator, mention_text="$[examples:workflow].degrade_worker")
    _assert_event_exists(wal_locator=r_degrade.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_degrade.wal_locator, event_type="approval_decided")
    assert (workspace_root / "outputs" / "fallback.md").exists()

    # 5) Reporter
    attempts = [
        {
            "name": "attempt #1",
            "mention": "$[examples:workflow].attempt_worker",
            "wal_locator": r_a1.wal_locator,
            "exit_code": str(r1.get("exit_code")),
            "ok": str(r1.get("ok")),
        },
        {
            "name": "attempt #2",
            "mention": "$[examples:workflow].attempt_worker",
            "wal_locator": r_a2.wal_locator,
            "exit_code": str(r2.get("exit_code")),
            "ok": str(r2.get("ok")),
        },
    ]
    controller_run = {"mention": "$[examples:workflow].retry_controller", "wal_locator": r_controller.wal_locator, "artifact_path": "retry_plan.json"}
    degrade_run = {"mention": "$[examples:workflow].degrade_worker", "wal_locator": r_degrade.wal_locator, "artifact_path": "outputs/fallback.md"}
    report_md = _format_report_md(controller_run=controller_run, attempts=attempts, degrade_run=degrade_run)

    reporter = Agent(
        model="fake-model",
        backend=_build_reporter_backend(report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    report_task = "$[examples:workflow].reporter\n请将本次重试/降级流程的证据链写入 report.md。"
    r_report = reporter.run(report_task, run_id="run_workflows_10_report")

    _assert_skill_injected(wal_locator=r_report.wal_locator, mention_text="$[examples:workflow].reporter")
    _assert_event_exists(wal_locator=r_report.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_report.wal_locator, event_type="approval_decided")
    assert (workspace_root / "report.md").exists()

    print("EXAMPLE_OK: workflows_10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

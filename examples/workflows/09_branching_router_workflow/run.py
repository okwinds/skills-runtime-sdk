"""
路由分支（router）示例（Skills-First，离线可回归）。

本示例演示：
- Router：读取输入 → 写 route.json（分支决策可审计）
- Worker：执行对应分支并落盘产物
- Reporter：汇总写 report.md（包含各 run 的 wal_locator 指针）

核心约束：
- 每个角色能力必须由 Skill（SKILL.md）定义；
- 任务文本显式包含 mention，触发 `skill_injected` 证据事件；
- 默认离线可运行（Fake backend + scripted approvals）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall


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


def _build_router_backend(*, route_choice: str, route_json: str) -> FakeChatBackend:
    """Router：read_file(task_input.json) → file_write(route.json) → summary。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_read_input", name="read_file", args={"file_path": "task_input.json", "offset": 1, "limit": 200}),
                            ToolCall(call_id="tc_write_route", name="file_write", args={"path": "route.json", "content": route_json}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text=f"已生成 route.json（route={route_choice}）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_worker_backend(*, artifact_path: str, artifact_markdown: str) -> FakeChatBackend:
    """Worker：file_write(artifact) → summary。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_artifact", name="file_write", args={"path": artifact_path, "content": artifact_markdown})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text=f"已写入分支产物：{artifact_path}。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, route_choice: str, steps: List[Dict[str, str]]) -> str:
    """组装汇总报告（确定性）。"""

    lines: List[str] = []
    lines.append("# Workflow Report（路由分支 / Skills-First）")
    lines.append("")
    lines.append(f"- Route: `{route_choice}`")
    lines.append("")
    for s in steps:
        lines.append(f"## {s['name']}")
        lines.append(f"- Skill: `{s['mention']}`")
        lines.append(f"- Events: `{s['wal_locator']}`")
        artifact = s.get("artifact_path") or ""
        if artifact:
            lines.append(f"- Artifact: `{artifact}`")
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
    """脚本入口：运行 workflows_09 示例。"""

    parser = argparse.ArgumentParser(description="09_branching_router_workflow (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--route", default="A", choices=["A", "B"], help="Route choice (A/B) for deterministic demo")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    route_choice = str(args.route).strip().upper()
    (workspace_root / "task_input.json").write_text(
        json.dumps({"route": route_choice, "note": "示例：用输入决定路由分支（A/B）"}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    route_json = json.dumps({"route": route_choice}, ensure_ascii=False, indent=2) + "\n"

    approval = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # router file_write route.json
            ApprovalDecision.APPROVED_FOR_SESSION,  # worker file_write artifact
            ApprovalDecision.APPROVED_FOR_SESSION,  # reporter file_write report.md
        ]
    )

    # 1) Router
    router = Agent(
        model="fake-model",
        backend=_build_router_backend(route_choice=route_choice, route_json=route_json),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    router_task = "$[examples:workflow].router\n请读取 task_input.json，做路由决策，并写入 route.json。"
    r_router = router.run(router_task, run_id="run_workflows_09_router")

    _assert_skill_injected(wal_locator=r_router.wal_locator, mention_text="$[examples:workflow].router")
    _assert_event_exists(wal_locator=r_router.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_router.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_router.wal_locator, tool_name="file_write")
    assert (workspace_root / "route.json").exists()

    # 2) Worker（根据 route.json 选择分支）
    loaded = json.loads((workspace_root / "route.json").read_text(encoding="utf-8"))
    decided = str(loaded.get("route") or "").strip().upper()
    if decided not in ("A", "B"):
        raise AssertionError(f"invalid route in route.json: {decided!r}")

    if decided == "A":
        mention = "$[examples:workflow].path_a_worker"
        artifact_path = "outputs/path_a.md"
        content = "# Path A Output\n\n本产物来自 A 分支。\n"
        run_id = "run_workflows_09_worker_a"
    else:
        mention = "$[examples:workflow].path_b_worker"
        artifact_path = "outputs/path_b.md"
        content = "# Path B Output\n\n本产物来自 B 分支。\n"
        run_id = "run_workflows_09_worker_b"

    worker = Agent(
        model="fake-model",
        backend=_build_worker_backend(artifact_path=artifact_path, artifact_markdown=content),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    worker_task = f"{mention}\n请执行分支 {decided} 并写入 {artifact_path}。"
    r_worker = worker.run(worker_task, run_id=run_id)

    _assert_skill_injected(wal_locator=r_worker.wal_locator, mention_text=mention)
    _assert_event_exists(wal_locator=r_worker.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_worker.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_worker.wal_locator, tool_name="file_write")
    assert (workspace_root / artifact_path).exists()

    # 3) Reporter
    steps = [
        {"name": "Router", "mention": "$[examples:workflow].router", "wal_locator": r_router.wal_locator, "artifact_path": "route.json"},
        {"name": "Worker", "mention": mention, "wal_locator": r_worker.wal_locator, "artifact_path": artifact_path},
    ]
    report_md = _format_report_md(route_choice=decided, steps=steps)

    reporter = Agent(
        model="fake-model",
        backend=_build_reporter_backend(report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval,
    )
    report_task = "$[examples:workflow].reporter\n请将本次 workflow 结果写入 report.md（包含 wal_locator 指针）。"
    r_report = reporter.run(report_task, run_id="run_workflows_09_report")

    _assert_skill_injected(wal_locator=r_report.wal_locator, mention_text="$[examples:workflow].reporter")
    _assert_event_exists(wal_locator=r_report.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_report.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_report.wal_locator, tool_name="file_write")
    assert (workspace_root / "report.md").exists()

    print("EXAMPLE_OK: workflows_09")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

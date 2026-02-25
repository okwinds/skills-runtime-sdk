"""
Skill Actions（skill_exec）示例（Skills-First，离线可回归）。

演示：
- skills.actions.enabled=true（默认 fail-closed）
- Skill bundle 内 actions/ 脚本 + SKILL.md frontmatter.actions 声明
- agent 通过 builtin tool `skill_exec` 受控执行动作（approvals/WAL 证据）
"""

from __future__ import annotations

import argparse
import json
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
                "  max_steps: 30",
                "safety:",
                f"  mode: {json.dumps(safety_mode)}",
                "  approval_timeout_ms: 2000",
                "sandbox:",
                "  default_policy: none",
                "skills:",
                "  actions:",
                "    enabled: true",
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


def _assert_event_exists(*, wal_locator: str, event_type: str) -> None:
    """断言 WAL 中存在某类事件（至少一条）。"""

    if not any(ev.get("type") == event_type for ev in _load_events(wal_locator)):
        raise AssertionError(f"missing event type: {event_type}")


def _assert_skill_injected(*, wal_locator: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    for ev in _load_events(wal_locator):
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


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


def _build_backend(*, report_md: str) -> FakeChatBackend:
    """skill_exec(build) → read_file(artifact) → file_write(report) → done。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_skill_exec",
                                name="skill_exec",
                                args={"skill_mention": "$[examples:workflow].artifact_builder", "action_id": "build"},
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
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_read_artifact", name="read_file", args={"file_path": "action_artifact.json", "offset": 1, "limit": 200})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
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
                    ChatStreamEvent(type="text_delta", text="actions 已执行并生成产物与报告。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, wal_locator: str) -> str:
    """生成 report.md（确定性）。"""

    return (
        "\n".join(
            [
                "# Workflow Report（skill_exec actions）",
                "",
                f"- Events: `{wal_locator}`",
                "- Artifact: `action_artifact.json`",
                "",
                "说明：本示例通过 `skill_exec` 执行 Skill bundle 内声明的 action（脚本位于 `actions/`）。",
                "",
            ]
        ).rstrip()
        + "\n"
    )


def main() -> int:
    """脚本入口：运行 workflows_07 示例。"""

    parser = argparse.ArgumentParser(description="07_skill_exec_actions_module (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    approvals = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # skill_exec
            ApprovalDecision.APPROVED_FOR_SESSION,  # file_write report
        ]
    )

    # 由于 report_md 需要 wal_locator（运行后才知道），这里先写占位，完成后再补充校验（示例保持简单）
    report_md_placeholder = _format_report_md(wal_locator="<filled_by_wal>")
    backend = _build_backend(report_md=report_md_placeholder)

    task = "\n".join(
        [
            "$[examples:workflow].artifact_builder",
            "$[examples:workflow].repo_reporter",
            "请执行 artifact_builder 的 build action，并生成 report.md。",
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals,
    )
    r = agent.run(task, run_id="run_workflows_07_actions")
    assert r.status == "completed"

    _assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:workflow].artifact_builder")
    _assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:workflow].repo_reporter")

    _assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r.wal_locator, tool_name="skill_exec")
    _assert_tool_ok(wal_locator=r.wal_locator, tool_name="file_write")

    artifact_path = workspace_root / "action_artifact.json"
    assert artifact_path.exists()
    assert "\"ok\": true" in artifact_path.read_text(encoding="utf-8")

    report_path = workspace_root / "report.md"
    assert report_path.exists()

    print("EXAMPLE_OK: workflows_07")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

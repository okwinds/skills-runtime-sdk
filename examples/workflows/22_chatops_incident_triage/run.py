"""
ChatOps 排障助手示例（Skills-First，离线可回归）。

演示：
- skills-first：任务包含 skill mention，触发 `skill_injected` 证据事件；
- read_file：读取 incident.log；
- request_user_input：澄清 1-2 个关键问题（离线：scripted HumanIOProvider 注入答案）；
- update_plan：把推进过程结构化可见（plan_updated 事件）；
- file_write：落盘 runbook.md/report.md（ask 模式走 approvals）；
- WAL：断言 human_request/human_response + plan_updated + tool_call_finished。
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
from skills_runtime.tools.protocol import HumanIOProvider, ToolCall


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


class _ScriptedHumanIO(HumanIOProvider):
    """
    离线 HumanIOProvider：按 question_id 返回预置答案。

    说明：
    - request_user_input 会以 `call_id=<tool_call_id>:<question_id>` 的形式调用本接口；
    - 本实现通过解析 call_id 末尾的 question_id 来选择答案。
    """

    def __init__(self, answers_by_question_id: Dict[str, str]) -> None:
        self._answers = dict(answers_by_question_id)

    def request_human_input(
        self,
        *,
        call_id: str,
        question: str,
        choices: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
    ) -> str:
        _ = (question, choices, context, timeout_ms)
        qid = str(call_id).split(":")[-1]
        if qid in self._answers:
            return str(self._answers[qid])
        return ""


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

    events = _load_events(wal_locator)
    if not any(ev.get("type") == event_type for ev in events):
        raise AssertionError(f"missing event type: {event_type}")


def _assert_tool_ok(*, wal_locator: str, tool_name: str) -> None:
    """断言 WAL 中存在某个 tool 的 tool_call_finished 且 ok=true。"""

    events = _load_events(wal_locator)
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


def _build_backend(*, runbook_md: str, report_md: str) -> FakeChatBackend:
    """构造 Fake backend：read incident → ask 2 questions → update_plan → write runbook/report。"""

    questions = {
        "questions": [
            {
                "id": "scope",
                "header": "影响范围",
                "question": "当前影响范围？（例如：单租户/全量，是否有 500 错误）",
            },
            {
                "id": "change_window",
                "header": "变更窗口",
                "question": "是否刚发生发布/变更？（是/否）",
                "options": [
                    {"label": "是", "description": "更偏向回滚/降级优先排查"},
                    {"label": "否", "description": "更偏向依赖/资源/突发故障排查"},
                ],
            },
        ]
    }

    plan = {
        "explanation": "示例：ChatOps 排障助手推进进度",
        "plan": [
            {"step": "读取 incident.log", "status": "completed"},
            {"step": "澄清关键问题", "status": "completed"},
            {"step": "生成 runbook", "status": "in_progress"},
            {"step": "落盘 report", "status": "pending"},
        ],
    }

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_read_incident", name="read_file", args={"file_path": "incident.log", "offset": 1, "limit": 400})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_user_input", name="request_user_input", args=questions)],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_plan", name="update_plan", args=plan),
                            ToolCall(call_id="tc_write_runbook", name="file_write", args={"path": "runbook.md", "content": runbook_md}),
                            ToolCall(call_id="tc_write_report", name="file_write", args={"path": "report.md", "content": report_md}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已生成排障 runbook 与报告（见 runbook.md/report.md）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 workflows_22（ChatOps 排障助手）。"""

    parser = argparse.ArgumentParser(description="22_chatops_incident_triage (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    mention = "$[examples:workflow].chatops_incident_triage_assistant"
    run_id = "run_workflows_22_chatops_incident_triage"
    wal_locator = (workspace_root / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()

    incident = "\n".join(
        [
            "2026-02-17T11:12:01Z ALERT api-gateway 5xx_rate_high",
            "2026-02-17T11:12:03Z ERROR upstream=db connection refused (timeout=2s)",
            "2026-02-17T11:12:05Z INFO recent_deploy=true service=api-gateway version=1.2.3",
            "",
        ]
    )
    (workspace_root / "incident.log").write_text(incident, encoding="utf-8")

    answers = {"scope": "全量用户均受影响，持续出现 500", "change_window": "是"}
    human_io = _ScriptedHumanIO(answers_by_question_id=answers)

    runbook_md = "\n".join(
        [
            "# Incident Triage Runbook\n",
            "## 0) 安全与沟通\n",
            "- 建立事故频道/工单，明确负责人（IC）与沟通频率。\n",
            "- 若影响扩大，先降级非核心功能，避免雪崩。\n",
            "## 1) 快速定位（5 分钟内）\n",
            "- 看 5xx/延迟曲线；确认是否为全量或单租户。\n",
            "- 检查最近发布/变更；若刚发布且相关，优先回滚/流量切换。\n",
            "## 2) 依赖检查（DB/缓存/下游）\n",
            "- 检查 DB 连通性、连接池耗尽、网络 ACL/DNS。\n",
            "- 检查 DB 指标：CPU/连接数/慢查询。\n",
            "## 3) 临时缓解\n",
            "- 降低并发/限流；开启熔断/重试退避（避免放大故障）。\n",
            "- 若确认发布导致，执行回滚并验证指标恢复。\n",
            "",
        ]
    )
    if not runbook_md.endswith("\n"):
        runbook_md += "\n"

    report_md = "\n".join(
        [
            "# ChatOps Incident Triage Report\n",
            "## Run\n",
            f"- run_id: `{run_id}`\n",
            f"- skill_mention: `{mention}`\n",
            "## Inputs\n",
            "- `incident.log`\n",
            "## Clarifications (Human I/O)\n",
            f"- scope: {answers['scope']}\n",
            f"- change_window: {answers['change_window']}\n",
            "## Outputs\n",
            "- `runbook.md`\n",
            "- `report.md`\n",
            "## Evidence (WAL)\n",
            f"- wal_locator: `{wal_locator}`\n",
        ]
    )
    if not report_md.endswith("\n"):
        report_md += "\n"

    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # runbook.md
            ApprovalDecision.APPROVED_FOR_SESSION,  # report.md
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=_build_backend(runbook_md=runbook_md, report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        human_io=human_io,
        approval_provider=approval_provider,
    )

    task = "\n".join(
        [
            mention,
            "请阅读 incident.log，提出 1-2 个澄清问题（用 request_user_input），然后 update_plan 推进，最后落盘 runbook.md 与 report.md。",
        ]
    )
    r = agent.run(task, run_id=run_id)

    assert r.status == "completed"
    assert (workspace_root / "incident.log").exists()
    assert (workspace_root / "runbook.md").exists()
    assert (workspace_root / "report.md").exists()

    _assert_event_exists(wal_locator=r.wal_locator, event_type="human_request")
    _assert_event_exists(wal_locator=r.wal_locator, event_type="human_response")
    _assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
    _assert_tool_ok(wal_locator=r.wal_locator, tool_name="file_write")

    print("EXAMPLE_OK: workflows_22")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

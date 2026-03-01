"""
排障助手（面向人类的应用示例）：
- 离线可回归（Fake backend）
- 真模型可跑（OpenAICompatible）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# 说明：用户可能用 `python examples/apps/<app>/run.py` 从任意 cwd 启动，
# 此时 repo_root 未必在 sys.path，导致 `import examples.*` 失败。
def _find_repo_root() -> Path:
    """从脚本文件路径向上查找 repo root（包含 `.git` 或 `pyproject.toml`）。"""

    file_value = globals().get("__file__")
    start = Path(file_value).resolve() if file_value else Path.cwd().resolve()
    for parent in [start] + list(start.parents):
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError(f"repo root not found from {start}")


_REPO_ROOT = _find_repo_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.apps._shared.app_support import (
    ScriptedApprovalProvider,
    ScriptedHumanIO,
    TerminalApprovalProvider,
    TerminalHumanIO,
    assert_event_exists,
    assert_skill_injected,
    assert_tool_ok,
    build_openai_compatible_backend,
    env_or_default,
    stream_events_with_min_ux,
    write_overlay_for_app,
)
from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision
from skills_runtime.tools.protocol import ToolCall


def _build_offline_backend(*, incident_log: str, runbook_md: str, report_md: str) -> FakeChatBackend:
    questions = {
        "questions": [
            {"id": "symptom", "header": "现象", "question": "用户侧看到的现象是什么？"},
            {"id": "impact", "header": "影响", "question": "影响范围与优先级？（例如：P0/P1）"},
        ]
    }
    plan_1 = {
        "explanation": "排障：读取日志并澄清",
        "plan": [
            {"step": "准备日志", "status": "completed"},
            {"step": "读取日志", "status": "in_progress"},
            {"step": "澄清问题", "status": "pending"},
            {"step": "输出 runbook", "status": "pending"},
        ],
    }
    plan_2 = {
        "explanation": "排障：输出 runbook 与报告",
        "plan": [
            {"step": "准备日志", "status": "completed"},
            {"step": "读取日志", "status": "completed"},
            {"step": "澄清问题", "status": "completed"},
            {"step": "输出 runbook", "status": "completed"},
        ],
    }

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_log", name="file_write", args={"path": "incident.log", "content": incident_log}),
                            ToolCall(call_id="tc_plan1", name="update_plan", args=plan_1),
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
                        tool_calls=[
                            ToolCall(call_id="tc_read", name="read_file", args={"file_path": "incident.log"}),
                            ToolCall(call_id="tc_input", name="request_user_input", args=questions),
                            ToolCall(call_id="tc_plan2", name="update_plan", args=plan_2),
                            ToolCall(call_id="tc_runbook", name="file_write", args={"path": "runbook.md", "content": runbook_md}),
                            ToolCall(call_id="tc_report", name="file_write", args={"path": "report.md", "content": report_md}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已生成排障 runbook 与报告。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="incident_triage_assistant (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    task = "\n".join(
        [
            "$[examples:app].incident_triager",
            "$[examples:app].runbook_writer",
            "$[examples:app].incident_reporter",
            "你正在运行一个 oncall 排障助手。",
            "必须使用工具完成：read_file(incident.log) → request_user_input 澄清 → update_plan → file_write(runbook.md/report.md)。",
        ]
    )

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=40,
        )

        incident_log = "\n".join(
            [
                "2026-02-25T00:00:01Z ERROR api timeout: upstream=payments latency_ms=12000",
                "2026-02-25T00:00:02Z WARN retry exhausted: route=/checkout user=anon",
                "",
            ]
        )
        runbook_md = "\n".join(
            [
                "# Incident Runbook\n",
                "## 可能原因\n",
                "- 上游 payments 超时/抖动",
                "",
                "## 排查步骤\n",
                "1. 检查上游健康状态与延迟指标\n",
                "2. 检查最近 30min 部署/变更\n",
                "3. 如确认上游问题，启用降级/重试策略\n",
                "",
            ]
        )
        report_md = "\n".join(
            [
                "# Incident Triage Report\n",
                "- input: incident.log\n",
                "- outputs: runbook.md, report.md\n",
                "",
            ]
        )

        backend = _build_offline_backend(incident_log=incident_log, runbook_md=runbook_md, report_md=report_md)
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write(incident.log)
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write(runbook)
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write(report)
            ]
        )
        human_io = ScriptedHumanIO(answers_by_question_id={"symptom": "结账超时", "impact": "P0"})

        agent = Agent(
            model="fake-model",
            backend=backend,
            workspace_root=workspace_root,
            config_paths=[overlay],
            human_io=human_io,
            approval_provider=approval_provider,
        )
        r = agent.run(task, run_id="run_app_incident_triage_assistant_offline")
        assert r.status == "completed"
        assert (workspace_root / "incident.log").exists()
        assert (workspace_root / "runbook.md").exists()
        assert (workspace_root / "report.md").exists()

        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].incident_triager")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].runbook_writer")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].incident_reporter")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_request")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_response")
        assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=r.wal_locator, tool="read_file")
        assert_tool_ok(wal_locator=r.wal_locator, tool="file_write")

        print("EXAMPLE_OK: app_incident_triage_assistant")
        return 0

    llm_base_url = env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1")
    planner_model = env_or_default("SRS_MODEL_PLANNER", "gpt-4o-mini")
    executor_model = env_or_default("SRS_MODEL_EXECUTOR", "gpt-4o-mini")
    overlay = write_overlay_for_app(
        workspace_root=workspace_root,
        skills_root=skills_root,
        safety_mode="ask",
        max_steps=80,
        llm_base_url=llm_base_url,
        planner_model=planner_model,
        executor_model=executor_model,
    )
    backend = build_openai_compatible_backend(config_paths=[overlay])

    agent = Agent(
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        human_io=TerminalHumanIO(),
        approval_provider=TerminalApprovalProvider(),
    )
    final_output, _ = stream_events_with_min_ux(agent=agent, task=task)
    print("\n[final_output]\n")
    print(final_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

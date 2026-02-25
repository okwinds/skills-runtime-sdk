"""
表单访谈 Pro（面向人类的应用示例）：
- 离线可回归（Fake backend）
- 真模型可跑（OpenAICompatible）
- Skills-First（mentions → skill_injected）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# 说明：用户可能用 `python examples/apps/<app>/run.py` 从任意 cwd 启动，
# 此时 repo_root 未必在 sys.path，导致 `import examples.*` 失败。
_REPO_ROOT = Path(__file__).resolve().parents[3]
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


def _build_offline_backend(*, submission_json: str, report_md: str) -> FakeChatBackend:
    """构造离线 Fake backend：访谈 → 计划 → 落盘 → 校验 → 报告。"""

    questions = {
        "questions": [
            {"id": "full_name", "header": "姓名", "question": "你的姓名？"},
            {"id": "email", "header": "邮箱", "question": "你的邮箱？"},
            {
                "id": "product",
                "header": "产品",
                "question": "你要预订的产品？",
                "options": [
                    {"label": "产品A", "description": "通用示例选项"},
                    {"label": "产品B", "description": "通用示例选项"},
                    {"label": "产品C", "description": "通用示例选项"},
                ],
            },
            {
                "id": "quantity",
                "header": "数量",
                "question": "数量（正整数）？",
                "options": [
                    {"label": "1", "description": "一份"},
                    {"label": "2", "description": "两份"},
                    {"label": "3", "description": "三份"},
                ],
            },
        ]
    }

    plan_1 = {
        "explanation": "表单访谈：收集字段",
        "plan": [
            {"step": "收集字段", "status": "in_progress"},
            {"step": "落盘产物", "status": "pending"},
            {"step": "最小校验", "status": "pending"},
        ],
    }
    plan_2 = {
        "explanation": "表单访谈：落盘与校验",
        "plan": [
            {"step": "收集字段", "status": "completed"},
            {"step": "落盘产物", "status": "completed"},
            {"step": "最小校验", "status": "completed"},
        ],
    }

    qa_argv = [
        str(sys.executable),
        "-c",
        "import json; d=json.load(open('submission.json','r',encoding='utf-8')); "
        "assert '@' in d.get('email',''); assert int(d.get('quantity')) >= 1; print('FORM_OK')",
    ]

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_input", name="request_user_input", args=questions)],
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
                            ToolCall(call_id="tc_plan1", name="update_plan", args=plan_1),
                            ToolCall(call_id="tc_write", name="file_write", args={"path": "submission.json", "content": submission_json}),
                            ToolCall(call_id="tc_qa", name="shell_exec", args={"argv": qa_argv, "timeout_ms": 5000, "sandbox": "none"}),
                            ToolCall(call_id="tc_plan2", name="update_plan", args=plan_2),
                            ToolCall(call_id="tc_report", name="file_write", args={"path": "report.md", "content": report_md}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="表单访谈已完成并通过最小校验。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="form_interview_pro (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    task = "\n".join(
        [
            "$[examples:app].form_interviewer",
            "$[examples:app].form_validator",
            "$[examples:app].form_reporter",
            "你正在运行一个表单访谈应用。",
            "必须使用工具完成：request_user_input → update_plan → file_write(submission.json) → shell_exec(QA) → file_write(report.md)。",
            "若 email 不包含 @ 或 quantity 非正整数，需要再次 request_user_input 追问不合法字段（最多 1 次）。",
        ]
    )

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=40,
        )

        answers = {"full_name": "张三", "email": "zhangsan@example.com", "product": "产品A", "quantity": "2"}
        submission_json = json.dumps(answers, ensure_ascii=False, indent=2) + "\n"
        report_md = "\n".join(
            [
                "# Form Interview Report\n",
                "## 收集字段\n",
                f"- full_name: {answers['full_name']}",
                f"- email: {answers['email']}",
                f"- product: {answers['product']}",
                f"- quantity: {answers['quantity']}",
                "",
                "## 产物\n",
                "- submission.json",
                "- report.md",
                "",
            ]
        )

        backend = _build_offline_backend(submission_json=submission_json, report_md=report_md)
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write(submission)
                ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write(report)
            ]
        )
        human_io = ScriptedHumanIO(answers_by_question_id=answers)

        agent = Agent(
            model="fake-model",
            backend=backend,
            workspace_root=workspace_root,
            config_paths=[overlay],
            human_io=human_io,
            approval_provider=approval_provider,
        )
        r = agent.run(task, run_id="run_app_form_interview_pro_offline")

        assert r.status == "completed"
        assert (workspace_root / "submission.json").exists()
        assert (workspace_root / "report.md").exists()

        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].form_interviewer")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].form_validator")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].form_reporter")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_request")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_response")
        assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=r.wal_locator, tool="file_write")
        assert_tool_ok(wal_locator=r.wal_locator, tool="shell_exec")

        print("EXAMPLE_OK: app_form_interview_pro")
        return 0

    # real mode
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
    final_output, _wal_locator = stream_events_with_min_ux(agent=agent, task=task)
    print("\n[final_output]\n")
    print(final_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

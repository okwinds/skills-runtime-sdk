"""
规则解析 Pro（面向人类的应用示例）：
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


def _build_offline_backend(*, plan_json: str, result_json: str, report_md: str) -> FakeChatBackend:
    questions = {
        "questions": [
            {"id": "code_string", "header": "编码", "question": "请输入 code_string（示例：2155..._001）"},
            {"id": "rule_text", "header": "规则", "question": "请输入自然语言解析规则（可多行）"},
        ]
    }

    plan_1 = {
        "explanation": "规则解析：生成 plan",
        "plan": [
            {"step": "收集输入", "status": "completed"},
            {"step": "生成 plan", "status": "in_progress"},
            {"step": "落盘产物", "status": "pending"},
            {"step": "最小 QA", "status": "pending"},
        ],
    }

    plan_2 = {
        "explanation": "规则解析：落盘与 QA",
        "plan": [
            {"step": "收集输入", "status": "completed"},
            {"step": "生成 plan", "status": "completed"},
            {"step": "落盘产物", "status": "completed"},
            {"step": "最小 QA", "status": "completed"},
        ],
    }

    qa_argv = [
        str(sys.executable),
        "-c",
        "import json; d=json.load(open('result.json','r',encoding='utf-8')); "
        "assert 'code_length' in d; assert isinstance(d['contains_underscore'], bool); print('RULES_OK')",
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
                            ToolCall(call_id="tc_plan_write", name="file_write", args={"path": "plan.json", "content": plan_json}),
                            ToolCall(call_id="tc_result_write", name="file_write", args={"path": "result.json", "content": result_json}),
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
                    ChatStreamEvent(type="text_delta", text="已生成 plan/result/report 并通过最小 QA。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="rules_parser_pro (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    task = "\n".join(
        [
            "$[examples:app].rules_planner",
            "$[examples:app].rules_executor",
            "$[examples:app].rules_reporter",
            "你正在运行一个规则解析应用。",
            "必须使用工具完成：request_user_input → update_plan → file_write(plan/result/report) → shell_exec(QA)。",
            "产物必须写入 workspace：plan.json、result.json、report.md。",
        ]
    )

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=30,
        )

        code_string = "21553270020250017013_001"
        rule_text = "从 code_string 中提取：长度、6-9 位、首字符、是否含下划线。"
        answers = {"code_string": code_string, "rule_text": rule_text}

        plan: Dict[str, Any] = {
            "inputs": {"code_string": code_string},
            "rule": rule_text,
            "steps": [
                {"key": "code_length", "op": "len"},
                {"key": "chars_6_to_9", "op": "slice", "start": 5, "end": 9},
                {"key": "first_char", "op": "index", "i": 0},
                {"key": "contains_underscore", "op": "contains", "value": "_"},
            ],
        }
        result: Dict[str, Any] = {
            "code_length": len(code_string),
            "chars_6_to_9": code_string[5:9],
            "first_char": code_string[0],
            "contains_underscore": ("_" in code_string),
        }
        plan_json = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
        result_json = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        report_md = "\n".join(
            [
                "# Rules Parser Report\n",
                "## 输入\n",
                f"- code_string: `{code_string}`\n",
                "## 规则\n",
                f"```\n{rule_text}\n```\n",
                "## 产物\n",
                "- plan.json\n",
                "- result.json\n",
                "",
            ]
        )

        backend = _build_offline_backend(plan_json=plan_json, result_json=result_json, report_md=report_md)
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write(plan)
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write(result)
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
        r = agent.run(task, run_id="run_app_rules_parser_pro_offline")
        assert r.status == "completed"
        assert (workspace_root / "plan.json").exists()
        assert (workspace_root / "result.json").exists()
        assert (workspace_root / "report.md").exists()

        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].rules_planner")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].rules_executor")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].rules_reporter")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_request")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_response")
        assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=r.wal_locator, tool="file_write")
        assert_tool_ok(wal_locator=r.wal_locator, tool="shell_exec")

        print("EXAMPLE_OK: app_rules_parser_pro")
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

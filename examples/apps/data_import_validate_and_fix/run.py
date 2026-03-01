"""
数据导入校验与修复（面向人类的应用示例）：
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


def _build_offline_backend(
    *, input_csv: str, fixed_csv: str, validation_json: str, report_md: str
) -> FakeChatBackend:
    plan_1 = {
        "explanation": "数据导入：读取输入并修复",
        "plan": [
            {"step": "准备输入", "status": "completed"},
            {"step": "读取输入", "status": "in_progress"},
            {"step": "写入修复结果", "status": "pending"},
            {"step": "最小 QA", "status": "pending"},
        ],
    }
    plan_2 = {
        "explanation": "数据导入：完成",
        "plan": [
            {"step": "准备输入", "status": "completed"},
            {"step": "读取输入", "status": "completed"},
            {"step": "写入修复结果", "status": "completed"},
            {"step": "最小 QA", "status": "completed"},
        ],
    }

    qa_argv = [
        str(sys.executable),
        "-c",
        "import csv, json; "
        "rows=list(csv.DictReader(open('fixed.csv','r',encoding='utf-8'))); "
        "assert rows and all('@' in r['email'] for r in rows); "
        "assert all(int(r['quantity'])>=1 for r in rows); "
        "j=json.load(open('validation_report.json','r',encoding='utf-8')); "
        "assert 'dropped_rows' in j; print('DATA_OK')",
    ]

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_input", name="file_write", args={"path": "input.csv", "content": input_csv}),
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
                            ToolCall(call_id="tc_read", name="read_file", args={"file_path": "input.csv"}),
                            ToolCall(call_id="tc_fixed", name="file_write", args={"path": "fixed.csv", "content": fixed_csv}),
                            ToolCall(call_id="tc_validation", name="file_write", args={"path": "validation_report.json", "content": validation_json}),
                            ToolCall(call_id="tc_qa", name="shell_exec", args={"argv": qa_argv, "timeout_ms": 5000, "sandbox": "inherit"}),
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
                    ChatStreamEvent(type="text_delta", text="数据已修复并通过最小 QA。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="data_import_validate_and_fix (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    task = "\n".join(
        [
            "$[examples:app].data_importer",
            "$[examples:app].data_fixer",
            "$[examples:app].data_qa_reporter",
            "你正在运行数据导入校验与修复应用。",
            "修复规则（必须遵守）：",
            "1) 丢弃 email 为空的行；2) quantity 非法或 <1 则设为 1；3) 其余字段保持不变。",
            "必须使用工具完成：read_file(input.csv) → file_write(fixed.csv/validation_report.json/report.md) → shell_exec(QA)。",
        ]
    )

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=50,
        )

        input_csv = "\n".join(
            [
                "full_name,email,quantity",
                "Alice,alice@example.com,2",
                "Bob,,3",
                "Carol,carol@example.com,0",
                "Dan,dan@example.com,not_a_number",
                "",
            ]
        )
        fixed_csv = "\n".join(
            [
                "full_name,email,quantity",
                "Alice,alice@example.com,2",
                "Carol,carol@example.com,1",
                "Dan,dan@example.com,1",
                "",
            ]
        )
        validation = {"dropped_rows": 1, "fixed_rows": 2, "total_rows": 4}
        validation_json = json.dumps(validation, ensure_ascii=False, indent=2) + "\n"
        report_md = "\n".join(
            [
                "# Data Import Fix Report\n",
                "## 规则\n",
                "- 丢弃 email 为空的行\n",
                "- quantity 非法或 <1 则设为 1\n",
                "",
                "## 产物\n",
                "- input.csv\n",
                "- fixed.csv\n",
                "- validation_report.json\n",
                "- report.md\n",
                "",
            ]
        )

        backend = _build_offline_backend(
            input_csv=input_csv,
            fixed_csv=fixed_csv,
            validation_json=validation_json,
            report_md=report_md,
        )
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write input.csv
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write fixed.csv
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write validation_report.json
                ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write report.md
            ]
        )

        agent = Agent(
            model="fake-model",
            backend=backend,
            workspace_root=workspace_root,
            config_paths=[overlay],
            approval_provider=approval_provider,
        )
        r = agent.run(task, run_id="run_app_data_import_validate_and_fix_offline")
        assert r.status == "completed"
        assert (workspace_root / "fixed.csv").exists()
        assert (workspace_root / "validation_report.json").exists()
        assert (workspace_root / "report.md").exists()

        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].data_importer")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].data_fixer")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].data_qa_reporter")
        assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=r.wal_locator, tool="read_file")
        assert_tool_ok(wal_locator=r.wal_locator, tool="file_write")
        assert_tool_ok(wal_locator=r.wal_locator, tool="shell_exec")

        print("EXAMPLE_OK: app_data_import_validate_and_fix")
        return 0

    llm_base_url = env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1")
    planner_model = env_or_default("SRS_MODEL_PLANNER", "gpt-4o-mini")
    executor_model = env_or_default("SRS_MODEL_EXECUTOR", "gpt-4o-mini")
    overlay = write_overlay_for_app(
        workspace_root=workspace_root,
        skills_root=skills_root,
        safety_mode="ask",
        max_steps=120,
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

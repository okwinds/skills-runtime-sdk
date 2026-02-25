"""
CI 失败排障与修复闭环（面向人类的应用示例）：
- 离线可回归（Fake backend）
- 真模型可跑（OpenAICompatible）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def _build_offline_backend(*, app_py: str, test_py: str, report_md: str) -> FakeChatBackend:
    plan_1 = {
        "explanation": "CI triage：写入最小项目并复现失败",
        "plan": [
            {"step": "写入最小项目", "status": "in_progress"},
            {"step": "复现失败", "status": "pending"},
            {"step": "最小修复", "status": "pending"},
            {"step": "回归验证", "status": "pending"},
            {"step": "输出报告", "status": "pending"},
        ],
    }
    plan_2 = {
        "explanation": "CI triage：修复并验证通过",
        "plan": [
            {"step": "写入最小项目", "status": "completed"},
            {"step": "复现失败", "status": "completed"},
            {"step": "最小修复", "status": "completed"},
            {"step": "回归验证", "status": "completed"},
            {"step": "输出报告", "status": "completed"},
        ],
    }

    pytest_argv = [str(sys.executable), "-m", "pytest", "-q"]
    clear_pycache_argv = [
        str(sys.executable),
        "-c",
        "import shutil; shutil.rmtree('__pycache__', ignore_errors=True); print('PYCACHE_CLEARED')",
    ]
    patch = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: app.py",
            "@@",
            "-def add(a: int, b: int) -> int:",
            "-    return a - b",
            "+def add(a: int, b: int) -> int:",
            "+    return a + b",
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
                        tool_calls=[
                            ToolCall(call_id="tc_plan1", name="update_plan", args=plan_1),
                            ToolCall(call_id="tc_app", name="file_write", args={"path": "app.py", "content": app_py}),
                            ToolCall(call_id="tc_test", name="file_write", args={"path": "test_app.py", "content": test_py}),
                            ToolCall(call_id="tc_pytest_fail", name="shell_exec", args={"argv": pytest_argv, "timeout_ms": 15000, "sandbox": "none"}),
                            ToolCall(call_id="tc_patch", name="apply_patch", args={"input": patch}),
                            # 重要：同秒内文件内容变更可能触发 pyc 缓存未失效（mtime/size 不变），导致第二次 pytest 仍跑旧代码。
                            ToolCall(
                                call_id="tc_clear_pycache",
                                name="shell_exec",
                                args={"argv": clear_pycache_argv, "timeout_ms": 5000, "sandbox": "none"},
                            ),
                            ToolCall(call_id="tc_pytest_ok", name="shell_exec", args={"argv": pytest_argv, "timeout_ms": 15000, "sandbox": "none"}),
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
                    ChatStreamEvent(type="text_delta", text="CI 失败已修复并验证通过。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="ci_failure_triage_and_fix (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    task = "\n".join(
        [
            "$[examples:app].ci_log_analyzer",
            "$[examples:app].ci_patcher",
            "$[examples:app].ci_qa_reporter",
            "你正在运行一个 CI 失败排障与修复应用。",
            "必须使用工具完成：file_write(项目文件) → shell_exec(pytest) → apply_patch(最小修复) → shell_exec(pytest) → file_write(report.md)。",
        ]
    )

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=60,
        )

        app_py = "\n".join(
            [
                "def add(a: int, b: int) -> int:",
                "    return a - b",
                "",
            ]
        )
        test_py = "\n".join(
            [
                "from app import add",
                "",
                "",
                "def test_add():",
                "    assert add(1, 2) == 3",
                "",
            ]
        )
        report_md = "\n".join(
            [
                "# CI Triage Report\n",
                "- issue: pytest failed (add function bug)\n",
                "- fix: change `add(a,b)` from subtraction to addition\n",
                "- verification: `python -m pytest -q` passed\n",
                "",
            ]
        )

        backend = _build_offline_backend(app_py=app_py, test_py=test_py, report_md=report_md)
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write app.py
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write test_app.py
                ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec pytest (fail)
                ApprovalDecision.APPROVED_FOR_SESSION,  # apply_patch
                ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec clear __pycache__
                ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec pytest (ok)
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
        r = agent.run(task, run_id="run_app_ci_failure_triage_and_fix_offline")
        assert r.status == "completed"
        assert (workspace_root / "report.md").exists()

        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].ci_log_analyzer")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].ci_patcher")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].ci_qa_reporter")
        assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=r.wal_locator, tool="file_write")
        assert_tool_ok(wal_locator=r.wal_locator, tool="shell_exec")
        assert_tool_ok(wal_locator=r.wal_locator, tool="apply_patch")

        print("EXAMPLE_OK: app_ci_failure_triage_and_fix")
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

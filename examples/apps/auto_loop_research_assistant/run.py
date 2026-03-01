"""
多步研究助手（面向人类的应用示例）：
- 离线可回归（Fake backend）
- 真模型可跑（OpenAICompatible）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def _build_offline_backend(*, kb_md: str, report_md: str) -> FakeChatBackend:
    questions = {"questions": [{"id": "question", "header": "问题", "question": "你想研究什么问题？（示例：为什么会 timeout？）"}]}

    plan_1 = {
        "explanation": "研究助手：准备知识库并收集问题",
        "plan": [
            {"step": "准备知识库", "status": "in_progress"},
            {"step": "收集问题", "status": "pending"},
            {"step": "检索与阅读", "status": "pending"},
            {"step": "输出报告", "status": "pending"},
        ],
    }
    plan_2 = {
        "explanation": "研究助手：检索与输出报告",
        "plan": [
            {"step": "准备知识库", "status": "completed"},
            {"step": "收集问题", "status": "completed"},
            {"step": "检索与阅读", "status": "completed"},
            {"step": "输出报告", "status": "completed"},
        ],
    }

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_plan1", name="update_plan", args=plan_1),
                            ToolCall(call_id="tc_kb", name="file_write", args={"path": "kb.md", "content": kb_md}),
                            ToolCall(call_id="tc_q", name="request_user_input", args=questions),
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
                            ToolCall(call_id="tc_grep", name="grep_files", args={"pattern": "timeout", "path": ".", "include": "*.md", "limit": 10}),
                            ToolCall(call_id="tc_read", name="read_file", args={"file_path": "kb.md"}),
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
                    ChatStreamEvent(type="text_delta", text="研究助手已输出报告。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="auto_loop_research_assistant (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    task = "\n".join(
        [
            "$[examples:app].research_planner",
            "$[examples:app].research_tool_user",
            "$[examples:app].research_reporter",
            "你正在运行一个多步研究助手。",
            "必须使用工具完成：request_user_input → update_plan → grep_files → read_file → file_write(report.md)。",
        ]
    )

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=40,
        )

        kb_md = "\n".join(
            [
                "# KB\n",
                "## Timeout\n",
                "- 可能原因：上游服务慢、网络抖动、连接池耗尽。\n",
                "- 排查建议：检查延迟、错误率、重试策略与超时配置。\n",
                "",
            ]
        )
        report_md = "\n".join(
            [
                "# Research Report\n",
                "## 结论（示例）\n",
                "- timeout 常见原因包括上游慢、网络抖动、连接池耗尽。\n",
                "",
                "## 证据\n",
                "- 使用 grep_files/read_file 在 kb.md 中检索与读取。\n",
                "",
            ]
        )

        backend = _build_offline_backend(kb_md=kb_md, report_md=report_md)
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write kb.md
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write report.md
            ]
        )
        human_io = ScriptedHumanIO(answers_by_question_id={"question": "为什么会 timeout？"})

        agent = Agent(
            model="fake-model",
            backend=backend,
            workspace_root=workspace_root,
            config_paths=[overlay],
            human_io=human_io,
            approval_provider=approval_provider,
        )
        r = agent.run(task, run_id="run_app_auto_loop_research_assistant_offline")
        assert r.status == "completed"
        assert (workspace_root / "kb.md").exists()
        assert (workspace_root / "report.md").exists()

        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].research_planner")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].research_tool_user")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].research_reporter")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_request")
        assert_event_exists(wal_locator=r.wal_locator, event_type="human_response")
        assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=r.wal_locator, tool="grep_files")
        assert_tool_ok(wal_locator=r.wal_locator, tool="read_file")
        assert_tool_ok(wal_locator=r.wal_locator, tool="file_write")

        print("EXAMPLE_OK: app_auto_loop_research_assistant")
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

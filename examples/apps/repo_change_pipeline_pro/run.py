"""
Repo 变更流水线 Pro（面向人类的应用示例）：
- 离线可回归（多次 Fake backend，分角色跑通）
- 真模型可跑（OpenAICompatible）
- Skills-First（每个角色用 mention 注入，WAL 可审计）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 说明：用户可能从任意 cwd 启动本脚本；为避免 `import examples.*` 依赖 cwd，显式注入 repo_root。
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from examples.apps._shared.app_support import (  # noqa: E402
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
from skills_runtime.agent import Agent  # noqa: E402
from skills_runtime.llm.chat_sse import ChatStreamEvent  # noqa: E402
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall  # noqa: E402
from skills_runtime.safety.approvals import ApprovalDecision  # noqa: E402
from skills_runtime.tools.protocol import ToolCall  # noqa: E402


def _write_demo_repo(workspace_root: Path) -> None:
    """
    在 workspace_root 下创建一个最小“repo”（用于演示 patch 与 QA）。

    文件：
    - app.py：包含 bug（is_even 写反）
    - test_app.py：pytest 用例
    """

    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "app.py").write_text(
        "\n".join(
            [
                "def is_even(n: int) -> bool:",
                "    \"\"\"Return True when n is even.\"\"\"",
                "    # BUG: wrong parity check",
                "    return n % 2 == 1",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace_root / "test_app.py").write_text(
        "\n".join(
            [
                "from app import is_even",
                "",
                "",
                "def test_is_even():",
                "    assert is_even(2) is True",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _build_analyzer_backend() -> FakeChatBackend:
    """离线 Analyzer：read_file(app.py/test_app.py) → 输出诊断与补丁建议。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_read_app",
                                name="read_file",
                                args={"file_path": "app.py", "offset": 1, "limit": 200},
                            ),
                            ToolCall(
                                call_id="tc_read_test",
                                name="read_file",
                                args={"file_path": "test_app.py", "offset": 1, "limit": 200},
                            ),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="text_delta",
                        text=(
                            "诊断：`is_even` 的判断写反了（n%2==1）。\n"
                            "建议补丁：把 `return n % 2 == 1` 改为 `return n % 2 == 0`。\n"
                        ),
                    ),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_patcher_backend(*, patch_text: str) -> FakeChatBackend:
    """离线 Patcher：apply_patch(app.py) → 输出完成提示。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_patch", name="apply_patch", args={"input": patch_text})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已应用最小补丁修复 is_even。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_qa_backend() -> FakeChatBackend:
    """离线 QA：清理 __pycache__ → pytest -q。"""

    clear_pycache_argv = [
        str(sys.executable),
        "-c",
        "import shutil; shutil.rmtree('__pycache__', ignore_errors=True); print('PYCACHE_CLEARED')",
    ]
    pytest_argv = [str(sys.executable), "-m", "pytest", "-q"]
    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_clear_pycache",
                                name="shell_exec",
                                args={"argv": clear_pycache_argv, "timeout_ms": 5000, "sandbox": "inherit"},
                            ),
                            ToolCall(
                                call_id="tc_pytest",
                                name="shell_exec",
                                args={"argv": pytest_argv, "timeout_ms": 15000, "sandbox": "inherit"},
                            ),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="QA 通过：pytest 全绿。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_reporter_backend(*, patch_diff: str, report_md: str) -> FakeChatBackend:
    """离线 Reporter：file_write(patch.diff/report.md) → 输出完成提示。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_diff", name="file_write", args={"path": "patch.diff", "content": patch_diff}),
                            ToolCall(call_id="tc_report", name="file_write", args={"path": "report.md", "content": report_md}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已输出 patch.diff 与 report.md。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="repo_change_pipeline_pro (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    _write_demo_repo(workspace_root)

    patch_text = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: app.py",
            "@@",
            "-    return n % 2 == 1",
            "+    return n % 2 == 0",
            "*** End Patch",
            "",
        ]
    )
    patch_diff = patch_text

    report_md = "\n".join(
        [
            "# Repo Change Pipeline Report\n",
            "## Summary\n",
            "- bug: `is_even` parity check wrong\n",
            "- fix: `n % 2 == 1` → `n % 2 == 0`\n",
            "- verification: `python -m pytest -q` passed\n",
            "",
            "## Artifacts\n",
            "- patch.diff\n",
            "- report.md\n",
            "",
        ]
    )

    analyzer_task = "\n".join(
        [
            "$[examples:app].repo_analyzer",
            "你是 repo analyzer：读取 app.py 与 test_app.py，输出 bug 诊断与补丁建议。",
            "必须使用工具：read_file(app.py/test_app.py)。",
        ]
    )
    patcher_task = "\n".join(
        [
            "$[examples:app].repo_patcher",
            "你是 repo patcher：按建议对 app.py 做最小修复。",
            "必须使用工具：apply_patch。",
        ]
    )
    qa_task = "\n".join(
        [
            "$[examples:app].repo_qa_runner",
            "你是 QA：运行 pytest 验证修复是否通过。",
            "必须使用工具：shell_exec（先清理 __pycache__，再跑 pytest）。",
        ]
    )
    reporter_task = "\n".join(
        [
            "$[examples:app].repo_reporter",
            "你是 reporter：输出 patch.diff 与 report.md。",
            "必须使用工具：file_write。",
        ]
    )

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=60,
        )

        # 审批顺序（离线）：apply_patch(1) + shell_exec(2) + file_write(2) = 5
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # apply_patch
                ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec clear __pycache__
                ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec pytest
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write patch.diff
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write report.md
            ]
        )

        analyzer = Agent(
            model="fake-model",
            backend=_build_analyzer_backend(),
            workspace_root=workspace_root,
            config_paths=[overlay],
            approval_provider=approval_provider,
        )
        patcher = Agent(
            model="fake-model",
            backend=_build_patcher_backend(patch_text=patch_text),
            workspace_root=workspace_root,
            config_paths=[overlay],
            approval_provider=approval_provider,
        )
        qa = Agent(
            model="fake-model",
            backend=_build_qa_backend(),
            workspace_root=workspace_root,
            config_paths=[overlay],
            approval_provider=approval_provider,
        )
        reporter = Agent(
            model="fake-model",
            backend=_build_reporter_backend(patch_diff=patch_diff, report_md=report_md),
            workspace_root=workspace_root,
            config_paths=[overlay],
            approval_provider=approval_provider,
        )

        r1 = analyzer.run(analyzer_task, run_id="run_app_repo_change_pipeline_analyzer_offline")
        r2 = patcher.run(patcher_task, run_id="run_app_repo_change_pipeline_patcher_offline")
        r3 = qa.run(qa_task, run_id="run_app_repo_change_pipeline_qa_offline")
        r4 = reporter.run(reporter_task, run_id="run_app_repo_change_pipeline_reporter_offline")

        assert r1.status == "completed"
        assert r2.status == "completed"
        assert r3.status == "completed"
        assert r4.status == "completed"
        assert (workspace_root / "patch.diff").exists()
        assert (workspace_root / "report.md").exists()

        # 证据：每个角色各自 WAL
        assert_skill_injected(wal_locator=r1.wal_locator, mention_text="$[examples:app].repo_analyzer")
        assert_tool_ok(wal_locator=r1.wal_locator, tool="read_file")
        assert_skill_injected(wal_locator=r2.wal_locator, mention_text="$[examples:app].repo_patcher")
        assert_tool_ok(wal_locator=r2.wal_locator, tool="apply_patch")
        assert_event_exists(wal_locator=r2.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r2.wal_locator, event_type="approval_decided")
        assert_skill_injected(wal_locator=r3.wal_locator, mention_text="$[examples:app].repo_qa_runner")
        assert_tool_ok(wal_locator=r3.wal_locator, tool="shell_exec")
        assert_skill_injected(wal_locator=r4.wal_locator, mention_text="$[examples:app].repo_reporter")
        assert_tool_ok(wal_locator=r4.wal_locator, tool="file_write")

        print("EXAMPLE_OK: app_repo_change_pipeline_pro")
        return 0

    llm_base_url = env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1")
    planner_model = env_or_default("SRS_MODEL_PLANNER", "gpt-4o-mini")
    executor_model = env_or_default("SRS_MODEL_EXECUTOR", "gpt-4o-mini")
    overlay = write_overlay_for_app(
        workspace_root=workspace_root,
        skills_root=skills_root,
        safety_mode="ask",
        max_steps=200,
        llm_base_url=llm_base_url,
        planner_model=planner_model,
        executor_model=executor_model,
    )
    backend = build_openai_compatible_backend(config_paths=[overlay])

    # 真模型：用一个 Agent 贯穿流水线（更符合人类使用：一次输入/一次 run_stream）
    task = "\n".join(
        [
            "$[examples:app].repo_analyzer",
            "$[examples:app].repo_patcher",
            "$[examples:app].repo_qa_runner",
            "$[examples:app].repo_reporter",
            "你正在运行一个 repo 变更流水线。",
            "必须完成：read_file(app.py/test_app.py) → apply_patch → shell_exec(pytest) → file_write(patch.diff/report.md)。",
            "约束：修复应最小化；不引入无关重构。",
        ]
    )
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

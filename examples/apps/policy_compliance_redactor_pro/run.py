"""
合规扫描与脱敏闭环 Pro（面向人类的应用示例）：
- 离线可回归（Fake backend）
- 真模型可跑（OpenAICompatible）
- Skills-First（mentions → skill_injected）
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


def _build_offline_backend(*, patch_text: str, patch_diff: str, result_md: str, report_md: str) -> FakeChatBackend:
    """
    构造离线 Fake backend：
    - skill_ref_read(policy)
    - read_file(target)
    - apply_patch
    - file_write(artifacts)
    """

    mention = "$[examples:app].policy_reader"

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_policy",
                                name="skill_ref_read",
                                args={"skill_mention": mention, "ref_path": "references/policy.md"},
                            ),
                            ToolCall(
                                call_id="tc_read_target",
                                name="read_file",
                                args={"file_path": "target.md", "offset": 1, "limit": 200},
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
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_patch", name="apply_patch", args={"input": patch_text}),
                            ToolCall(call_id="tc_write_diff", name="file_write", args={"path": "patch.diff", "content": patch_diff}),
                            ToolCall(call_id="tc_write_result", name="file_write", args={"path": "result.md", "content": result_md}),
                            ToolCall(call_id="tc_write_report", name="file_write", args={"path": "report.md", "content": report_md}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已按 policy 完成合规脱敏与产物落盘。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="policy_compliance_redactor_pro (offline/real)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--mode", choices=["offline", "real"], default="offline", help="Run mode")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    task = "\n".join(
        [
            "$[examples:app].policy_reader",
            "$[examples:app].content_redactor",
            "$[examples:app].compliance_reporter",
            "你正在运行一个合规扫描与脱敏应用。",
            "必须使用工具完成：skill_ref_read(references/policy.md) → read_file(target.md) → apply_patch → file_write(patch.diff/result.md/report.md)。",
            "约束：只做最小替换（不要重写其它文本）。",
        ]
    )

    # 输入文件（workspace 产物）
    target_before = "\n".join(
        [
            "# Target Document (Before)\n",
            "说明：本文件包含一个明文敏感 token（示例）。\n",
            "SECRET_TOKEN=super-secret-demo-token\n",
        ]
    )
    if not target_before.endswith("\n"):
        target_before += "\n"
    (workspace_root / "target.md").write_text(target_before, encoding="utf-8")

    patch_text = "\n".join(
        [
            "*** Begin Patch",
            "*** Update File: target.md",
            "@@",
            "-SECRET_TOKEN=super-secret-demo-token",
            "+SECRET_TOKEN=[REDACTED]",
            "*** End Patch",
            "",
        ]
    )
    patch_diff = patch_text
    result_md = "\n".join(
        [
            "# Policy Compliance Result\n",
            "## Summary\n",
            "- 状态：已修复\n",
            "- 规则：禁止明文敏感 token（见 policy.md）\n",
            "- 替换：`SECRET_TOKEN=...` → `SECRET_TOKEN=[REDACTED]`\n",
            "",
        ]
    )
    if not result_md.endswith("\n"):
        result_md += "\n"
    report_md = "\n".join(
        [
            "# Policy Compliance Patch Report\n",
            "## Inputs\n",
            "- `target.md`\n",
            "## Outputs\n",
            "- `patch.diff`\n",
            "- `result.md`\n",
            "- `report.md`\n",
            "",
        ]
    )
    if not report_md.endswith("\n"):
        report_md += "\n"

    if args.mode == "offline":
        overlay = write_overlay_for_app(
            workspace_root=workspace_root,
            skills_root=skills_root,
            safety_mode="ask",
            max_steps=30,
            enable_references=True,
        )

        backend = _build_offline_backend(
            patch_text=patch_text,
            patch_diff=patch_diff,
            result_md=result_md,
            report_md=report_md,
        )
        approval_provider = ScriptedApprovalProvider(
            decisions=[
                ApprovalDecision.APPROVED_FOR_SESSION,  # apply_patch
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write patch.diff
                ApprovalDecision.APPROVED_FOR_SESSION,  # file_write result.md
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
        r = agent.run(task, run_id="run_app_policy_compliance_redactor_pro_offline")
        assert r.status == "completed"
        assert (workspace_root / "patch.diff").exists()
        assert (workspace_root / "result.md").exists()
        assert (workspace_root / "report.md").exists()

        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].policy_reader")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].content_redactor")
        assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:app].compliance_reporter")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
        assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")
        assert_tool_ok(wal_locator=r.wal_locator, tool="skill_ref_read")
        assert_tool_ok(wal_locator=r.wal_locator, tool="read_file")
        assert_tool_ok(wal_locator=r.wal_locator, tool="apply_patch")
        assert_tool_ok(wal_locator=r.wal_locator, tool="file_write")

        print("EXAMPLE_OK: app_policy_compliance_redactor_pro")
        return 0

    llm_base_url = env_or_default("OPENAI_BASE_URL", "https://api.openai.com/v1")
    planner_model = env_or_default("SRS_MODEL_PLANNER", "gpt-4o-mini")
    executor_model = env_or_default("SRS_MODEL_EXECUTOR", "gpt-4o-mini")
    overlay = write_overlay_for_app(
        workspace_root=workspace_root,
        skills_root=skills_root,
        safety_mode="ask",
        max_steps=80,
        enable_references=True,
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


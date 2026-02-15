"""
skills references + actions 示例（离线，可回归）。

本示例覆盖：
- filesystem skills bundle 的最小形态（SKILL.md + references/ + actions/）；
- skills.references.enabled / skills.actions.enabled 的 fail-closed 默认；
- skill_ref_read 与 skill_exec 的最小闭环（通过 FakeChatBackend 的 tool_calls 驱动）；
- skill_exec 在 Agent gate 中走 approvals（APPROVED）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall


class _ApproveOnceProvider(ApprovalProvider):
    """只要被 ask 就批准（用于离线演示）。"""

    def __init__(self) -> None:
        self.calls: list[ApprovalRequest] = []

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:
        self.calls.append(request)
        return ApprovalDecision.APPROVED


def _write_overlay(workspace_root: Path, *, skills_root: Path) -> Path:
    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 40",
                "safety:",
                "  mode: \"ask\"",
                "  approval_timeout_ms: 2000",
                "skills:",
                "  mode: \"explicit\"",
                "  roots: []",
                "  max_auto: 0",
                "  spaces:",
                "    - id: \"local-space\"",
                "      account: \"local\"",
                "      domain: \"demo\"",
                "      enabled: true",
                "      sources: [\"fs1\"]",
                "  sources:",
                "    - id: \"fs1\"",
                "      type: \"filesystem\"",
                "      options:",
                f"        root: {json.dumps(str(skills_root))}",
                "  references:",
                "    enabled: true",
                "    allow_assets: false",
                "  actions:",
                "    enabled: true",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return overlay


def _create_demo_skill(skills_root: Path) -> str:
    """
    创建一个最小 demo skill，并返回其 mention。

    约定：
    - space 为 local:demo
    - skill_name 为 demo-skill
    """

    skill_dir = skills_root / "demo-skill"
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "actions").mkdir(parents=True, exist_ok=True)

    (skill_dir / "references" / "usage.md").write_text("This is a reference doc.\n", encoding="utf-8")
    (skill_dir / "actions" / "echo.py").write_text("print('ACTION_OK')\n", encoding="utf-8")

    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                "name: demo-skill",
                "description: \"demo skill for references/actions\"",
                "actions:",
                "  echo:",
                "    argv: [\"python\", \"actions/echo.py\"]",
                "---",
                "",
                "# Demo Skill",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return "$[local:demo].demo-skill"


def main() -> int:
    """脚本入口：skill_ref_read → skill_exec（审批）→ final。"""

    parser = argparse.ArgumentParser(description="07_skills_references_and_actions (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    skills_root = workspace_root / "skills_root"
    skills_root.mkdir(parents=True, exist_ok=True)
    mention = _create_demo_skill(skills_root)
    overlay = _write_overlay(workspace_root, skills_root=skills_root)

    provider = _ApproveOnceProvider()
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_ref_1",
                                name="skill_ref_read",
                                args={"skill_mention": mention, "ref_path": "references/usage.md"},
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
                        tool_calls=[
                            ToolCall(
                                call_id="tc_exec_1",
                                name="skill_exec",
                                args={"skill_mention": mention, "action_id": "echo"},
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="done"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=provider,
    )
    r = agent.run("skill demo", run_id="run_example_step_07")
    assert r.status == "completed"
    assert len(provider.calls) == 1

    print("[example] approval_calls_total=1")
    print("EXAMPLE_OK: step_by_step_07")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


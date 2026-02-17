"""
Policy 合规补丁示例（Skills-First，离线可回归）。

演示：
- skills-first：任务包含 skill mention，触发 `skill_injected` 证据事件；
- skill references：用 `skill_ref_read` 读取 `references/policy.md`（overlay 显式开启 references）；
- apply_patch：对 workspace 内 `target.md` 进行最小合规修复（写操作走 approvals）；
- file_write：落盘 patch.diff/result.md/report.md（写操作走 approvals）；
- WAL：断言 skill_injected、tool_call_finished(apply_patch)、approval_requested/approval_decided。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall


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
                "  max_steps: 30",
                "safety:",
                f"  mode: {json.dumps(safety_mode)}",
                "  approval_timeout_ms: 2000",
                "sandbox:",
                "  default_policy: none",
                "skills:",
                "  mode: explicit",
                "  max_auto: 0",
                "  strictness:",
                "    unknown_mention: error",
                "    duplicate_name: error",
                "    mention_format: strict",
                "  references:",
                "    enabled: true",
                "    default_max_bytes: 65536",
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


def _load_events(events_path: str) -> List[Dict[str, Any]]:
    """读取 WAL（events.jsonl）并返回 JSON object 列表。"""

    p = Path(events_path)
    if not p.exists():
        raise AssertionError(f"events_path does not exist: {events_path}")
    events: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _assert_event_exists(*, events_path: str, event_type: str) -> None:
    """断言 WAL 中存在某类事件（至少一条）。"""

    events = _load_events(events_path)
    if not any(ev.get("type") == event_type for ev in events):
        raise AssertionError(f"missing event type: {event_type}")


def _assert_skill_injected(*, events_path: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    events = _load_events(events_path)
    for ev in events:
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_tool_ok(*, events_path: str, tool_name: str) -> None:
    """断言 WAL 中存在某个 tool 的 tool_call_finished 且 ok=true。"""

    events = _load_events(events_path)
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


def _build_backend(*, mention: str, patch_text: str, patch_diff: str, result_md: str, report_md: str) -> FakeChatBackend:
    """构造 Fake backend：skill_ref_read(policy) → read_file(target) → apply_patch → file_write(artifacts)。"""

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
                            ToolCall(call_id="tc_read_target", name="read_file", args={"file_path": "target.md", "offset": 1, "limit": 200}),
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
                            ToolCall(call_id="tc_apply_patch", name="apply_patch", args={"input": patch_text}),
                            ToolCall(call_id="tc_write_patch", name="file_write", args={"path": "patch.diff", "content": patch_diff}),
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
                    ChatStreamEvent(type="text_delta", text="已按 policy 完成合规补丁与产物落盘。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 workflows_20（Policy 合规补丁）。"""

    parser = argparse.ArgumentParser(description="20_policy_compliance_patch (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    mention = "$[examples:workflow].policy_compliance_patcher"
    run_id = "run_workflows_20_policy_compliance_patch"

    # 1) 构造一个“政策禁止”的输入文件（明文敏感 token）
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

    # 2) 确定性补丁：仅替换 token 行
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

    events_path = (workspace_root / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()

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
            "## Run\n",
            f"- run_id: `{run_id}`\n",
            f"- skill_mention: `{mention}`\n",
            "## Inputs\n",
            "- `target.md`（含 1 行明文敏感 token）\n",
            "## Actions\n",
            "- tool: `skill_ref_read` 读取 `references/policy.md`\n",
            "- tool: `apply_patch` 修复 `target.md`\n",
            "- tool: `file_write` 落盘 `patch.diff`/`result.md`/`report.md`\n",
            "## Evidence (WAL)\n",
            f"- events_path: `{events_path}`\n",
            "## Outputs\n",
            "- `target.md`\n",
            "- `patch.diff`\n",
            "- `result.md`\n",
            "- `report.md`\n",
        ]
    )
    if not report_md.endswith("\n"):
        report_md += "\n"

    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # apply_patch
            ApprovalDecision.APPROVED_FOR_SESSION,  # patch.diff
            ApprovalDecision.APPROVED_FOR_SESSION,  # result.md
            ApprovalDecision.APPROVED_FOR_SESSION,  # report.md
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=_build_backend(
            mention=mention,
            patch_text=patch_text,
            patch_diff=patch_diff,
            result_md=result_md,
            report_md=report_md,
        ),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )

    task = "\n".join(
        [
            mention,
            "请读取 skill references 里的 policy.md，检查 target.md 的合规问题，并用 apply_patch 做最小修复；随后落盘 patch.diff/result.md/report.md。",
        ]
    )
    r = agent.run(task, run_id=run_id)

    assert r.status == "completed"
    assert (workspace_root / "target.md").exists()
    assert (workspace_root / "patch.diff").exists()
    assert (workspace_root / "result.md").exists()
    assert (workspace_root / "report.md").exists()

    target_after = (workspace_root / "target.md").read_text(encoding="utf-8")
    assert "SECRET_TOKEN=[REDACTED]" in target_after
    assert "super-secret-demo-token" not in target_after

    _assert_skill_injected(events_path=r.events_path, mention_text=mention)
    _assert_tool_ok(events_path=r.events_path, tool_name="apply_patch")
    _assert_event_exists(events_path=r.events_path, event_type="approval_requested")
    _assert_event_exists(events_path=r.events_path, event_type="approval_decided")

    print("EXAMPLE_OK: workflows_20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


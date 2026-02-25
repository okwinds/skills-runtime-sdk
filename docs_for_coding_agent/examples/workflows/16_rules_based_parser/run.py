"""
规则驱动的结构化解析器示例（Skills-First，离线可回归）。

演示：
- skills-first：任务包含 skill mention，触发 `skill_injected` 证据事件；
- `file_write`：通过 builtin tool 落盘 `plan.json`/`result.json`/`report.md`，走 approvals；
- WAL（events.jsonl）可用于审计与排障。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """写入示例运行用 overlay（runtime.yaml）。

    参数：
    - workspace_root：工作区根目录
    - skills_root：filesystem skills root（本示例目录下 `skills/`）
    - safety_mode：allow|ask|deny（示例默认 ask）
    """

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 20",
                "safety:",
                f"  mode: {json.dumps(safety_mode)}",
                "  approval_timeout_ms: 2000",
                "sandbox:",
                "  default_policy: none",
                "skills:",
                "  strictness:",
                "    unknown_mention: error",
                "    duplicate_name: error",
                "    mention_format: strict",
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


def _load_events(wal_locator: str) -> List[Dict[str, Any]]:
    """读取 WAL（events.jsonl）并返回 JSON object 列表。"""

    p = Path(wal_locator)
    if not p.exists():
        raise AssertionError(f"wal_locator does not exist: {wal_locator}")
    events: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _assert_skill_injected(*, wal_locator: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    events = _load_events(wal_locator)
    for ev in events:
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_file_write_ok(*, wal_locator: str) -> None:
    """断言 WAL 中至少存在 1 条 `file_write` 的成功 tool_call_finished。"""

    events = _load_events(wal_locator)
    for ev in events:
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != "file_write":
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return
    raise AssertionError("missing ok tool_call_finished for tool=file_write")


def _build_backend(*, plan_json: str, result_json: str, report_md: str) -> FakeChatBackend:
    """构造 Fake backend：一次 tool_calls 写入 3 个产物文件，然后输出完成提示。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_plan", name="file_write", args={"path": "plan.json", "content": plan_json}),
                            ToolCall(call_id="tc_result", name="file_write", args={"path": "result.json", "content": result_json}),
                            ToolCall(call_id="tc_report", name="file_write", args={"path": "report.md", "content": report_md}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已生成 plan/result/report（离线示例）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 rules_based_parser workflow（offline）。"""

    parser = argparse.ArgumentParser(description="16_rules_based_parser (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    code_string = "21553270020250017013_001"
    rule_text = "\n".join(
        [
            "从用户提供的 code_string 中提取信息：",
            "1) code_length（长度）",
            "2) chars_6_to_9（第 6 到 9 位字符，1-based）",
            "3) first_char（首字符）",
            "4) contains_underscore（是否包含下划线，bool）",
        ]
    )

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

    # 确定性执行（示例中直接在脚本内执行；真实场景可把 plan 交给执行器模块）。
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
            "# Rules Based Parser Report\n",
            "## 输入\n",
            f"- code_string: `{code_string}`\n",
            "## 规则（自然语言）\n",
            "```\n" + rule_text + "\n```\n",
            "## plan.json\n",
            "```json\n" + plan_json.strip() + "\n```\n",
            "## result.json\n",
            "```json\n" + result_json.strip() + "\n```\n",
        ]
    )
    if not report_md.endswith("\n"):
        report_md += "\n"

    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,
            ApprovalDecision.APPROVED_FOR_SESSION,
            ApprovalDecision.APPROVED_FOR_SESSION,
        ]
    )
    backend = _build_backend(plan_json=plan_json, result_json=result_json, report_md=report_md)

    task = "\n".join(
        [
            "$[examples:workflow].rules_parser",
            "请根据规则生成 plan，并以确定性方式执行后落盘产物。",
        ]
    )
    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    r = agent.run(task, run_id="run_workflows_16_rules_based_parser")

    assert r.status == "completed"
    assert (workspace_root / "plan.json").exists()
    assert (workspace_root / "result.json").exists()
    assert (workspace_root / "report.md").exists()

    _assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:workflow].rules_parser")
    _assert_file_write_ok(wal_locator=r.wal_locator)

    print("EXAMPLE_OK: workflows_16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

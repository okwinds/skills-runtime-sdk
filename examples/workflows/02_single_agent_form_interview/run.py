"""
单 Agent 表单访谈工作流示例（Skills-First，离线可回归）。

演示：
- skills-first：任务包含多个 skill mentions，触发 `skill_injected` 证据事件；
- request_user_input：结构化人机输入（离线：scripted HumanIOProvider 注入答案）；
- update_plan：结构化计划进度（plan_updated 事件）；
- file_write + shell_exec：产物落盘与最小确定性校验（ask 模式走 approvals）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import HumanIOProvider, ToolCall


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
                "  max_steps: 40",
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


class _ScriptedHumanIO(HumanIOProvider):
    """
    离线 HumanIOProvider：按 question_id 返回预置答案。

    说明：
    - request_user_input 会以 `call_id=<tool_call_id>:<question_id>` 的形式调用本接口；
    - 本实现通过解析 call_id 末尾的 question_id 来选择答案。
    """

    def __init__(self, answers_by_question_id: Dict[str, str]) -> None:
        self._answers = dict(answers_by_question_id)

    def request_human_input(
        self,
        *,
        call_id: str,
        question: str,
        choices: Optional[List[str]] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
    ) -> str:
        _ = (question, choices, context, timeout_ms)
        qid = str(call_id).split(":")[-1]
        if qid in self._answers:
            return str(self._answers[qid])
        # fail-closed：示例中未配置的题目直接返回空，触发上层记录并暴露问题
        return ""


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


def _assert_event_exists(*, wal_locator: str, event_type: str) -> None:
    """断言 WAL 中存在某类事件（至少一条）。"""

    events = _load_events(wal_locator)
    if not any(ev.get("type") == event_type for ev in events):
        raise AssertionError(f"missing event type: {event_type}")


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


def _build_backend(*, submission_json: str) -> FakeChatBackend:
    """
    构造 Fake backend：请求 user_input → update_plan/file_write/shell_exec → 最终输出。

    参数：
    - submission_json：将写入 submission.json 的内容（字符串，确定性）
    """

    questions = {
        "questions": [
            {"id": "full_name", "header": "姓名", "question": "你的姓名？"},
            {"id": "email", "header": "邮箱", "question": "你的邮箱？"},
            {"id": "product", "header": "产品", "question": "你要预订的产品名？"},
            {"id": "quantity", "header": "数量", "question": "数量（整数）？"},
        ]
    }

    plan = {
        "explanation": "示例：表单访谈工作流进度",
        "plan": [
            {"step": "收集字段", "status": "completed"},
            {"step": "落盘产物", "status": "in_progress"},
            {"step": "最小校验", "status": "pending"},
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
                        tool_calls=[ToolCall(call_id="tc_user_input", name="request_user_input", args=questions)],
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
                            ToolCall(call_id="tc_plan", name="update_plan", args=plan),
                            ToolCall(
                                call_id="tc_write",
                                name="file_write",
                                args={"path": "submission.json", "content": submission_json},
                            ),
                            ToolCall(
                                call_id="tc_qa",
                                name="shell_exec",
                                args={"argv": qa_argv, "timeout_ms": 5000, "sandbox": "none"},
                            ),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="表单已完成并通过最小校验。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 workflows_02 示例。"""

    parser = argparse.ArgumentParser(description="02_single_agent_form_interview (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()

    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    answers = {"full_name": "张三", "email": "zhangsan@example.com", "product": "demo", "quantity": "2"}
    submission = {"full_name": answers["full_name"], "email": answers["email"], "product": answers["product"], "quantity": answers["quantity"]}
    submission_json = json.dumps(submission, ensure_ascii=False, indent=2) + "\n"

    human_io = _ScriptedHumanIO(answers_by_question_id=answers)
    approval_provider = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # file_write
            ApprovalDecision.APPROVED_FOR_SESSION,  # shell_exec
        ]
    )

    backend = _build_backend(submission_json=submission_json)

    task = "\n".join(
        [
            "$[examples:workflow].form_interviewer",
            "$[examples:workflow].form_validator",
            "$[examples:workflow].form_reporter",
            "请按流程完成表单访谈：收集字段→落盘→最小校验。",
        ]
    )
    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        human_io=human_io,
        approval_provider=approval_provider,
    )
    r = agent.run(task, run_id="run_workflows_02_form")

    assert r.status == "completed"
    assert (workspace_root / "submission.json").exists()
    assert "zhangsan@example.com" in (workspace_root / "submission.json").read_text(encoding="utf-8")

    _assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:workflow].form_interviewer")
    _assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:workflow].form_validator")
    _assert_skill_injected(wal_locator=r.wal_locator, mention_text="$[examples:workflow].form_reporter")

    _assert_event_exists(wal_locator=r.wal_locator, event_type="human_request")
    _assert_event_exists(wal_locator=r.wal_locator, event_type="human_response")
    _assert_event_exists(wal_locator=r.wal_locator, event_type="plan_updated")
    _assert_event_exists(wal_locator=r.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r.wal_locator, event_type="approval_decided")

    print("EXAMPLE_OK: workflows_02")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

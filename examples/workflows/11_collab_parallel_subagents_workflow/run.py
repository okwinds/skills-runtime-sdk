"""
Collab 原语并行子 agent 示例（Skills-First，离线可回归）。

本示例演示：
- Master agent 在自身 loop 内直接调用：
  - spawn_agent：生成子 agent
  - send_input：向子 agent 投递输入
  - wait：等待子 agent 完成并获取 final_output
- 子 agent 本身也必须是 Skills-First：通过 skill mention 注入，写入独立产物
- Aggregator 汇总：写 report.md（包含各 run 的 events_path 指针）

重要说明（为了离线回归确定性）：
- 使用示例内 DeterministicCollabManager，子 agent id 固定为 sub1/sub2/sub3。
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from agent_sdk.tools.protocol import ToolCall


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask") -> Path:
    """写入示例运行用 overlay（runtime.yaml）。"""

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 80",
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
    out: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _assert_skill_injected(*, events_path: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    for ev in _load_events(events_path):
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_tool_ok(*, events_path: str, tool_name: str) -> None:
    """断言 WAL 中某个 tool 的 `tool_call_finished` 存在且 ok=true。"""

    for ev in _load_events(events_path):
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return
    raise AssertionError(f"missing ok tool_call_finished for tool={tool_name}")


@dataclass
class _ChildHandle:
    id: str
    agent_type: str
    inbox: Queue[str]
    cancel_event: threading.Event
    thread: threading.Thread
    status: str = "running"  # running|completed|failed|cancelled
    final_output: Optional[str] = None
    error: Optional[str] = None
    child_events_path: Optional[str] = None


class DeterministicCollabManager:
    """
    示例专用：确定性 CollabManager（离线可回归）。

    约定：
    - spawn 按顺序分配固定 ids：sub1/sub2/sub3
    - wait 返回 handle 快照（含 status/final_output）
    - 子 agent 的真正工作仍由本 SDK 的 Agent.run(...) 完成（skills/WAL 均有效）
    """

    def __init__(self, *, workspace_root: Path, overlay: Path) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._overlay = Path(overlay).resolve()
        self._lock = threading.Lock()
        self._order = ["sub1", "sub2", "sub3"]
        self._idx = 0
        self._agents: Dict[str, _ChildHandle] = {}

    def spawn(self, *, message: str, agent_type: str = "default") -> _ChildHandle:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be non-empty")

        with self._lock:
            if self._idx >= len(self._order):
                raise ValueError("demo manager supports only 3 children")
            agent_id = self._order[self._idx]
            self._idx += 1

        inbox: Queue[str] = Queue()
        cancel_event = threading.Event()
        h = _ChildHandle(
            id=agent_id,
            agent_type=str(agent_type or "default"),
            inbox=inbox,
            cancel_event=cancel_event,
            thread=threading.Thread(target=self._run_child, args=(agent_id, message, str(agent_type or "default"), inbox, cancel_event), daemon=True),
        )
        with self._lock:
            self._agents[agent_id] = h
        h.thread.start()
        return h

    def send_input(self, *, agent_id: str, message: str) -> None:
        h = self.get(str(agent_id))
        if h is None:
            raise KeyError("agent not found")
        h.inbox.put(str(message))

    def close(self, *, agent_id: str) -> None:
        h = self.get(str(agent_id))
        if h is None:
            raise KeyError("agent not found")
        h.cancel_event.set()
        with self._lock:
            cur = self._agents.get(h.id)
            if cur is not None:
                cur.status = "cancelled"

    def wait(self, *, ids: List[str], timeout_ms: Optional[int] = None) -> List[_ChildHandle]:
        deadline = None if timeout_ms is None else (time.monotonic() + timeout_ms / 1000.0)
        handles: List[_ChildHandle] = []
        with self._lock:
            missing = [i for i in ids if str(i) not in self._agents]
            if missing:
                raise KeyError(f"unknown ids: {missing}")
            handles = [self._agents[str(i)] for i in ids]

        for h in handles:
            if not h.thread.is_alive():
                continue
            if deadline is None:
                h.thread.join()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                h.thread.join(timeout=remaining)

        # 返回快照（避免外部修改内部状态）
        out: List[_ChildHandle] = []
        with self._lock:
            for h in handles:
                cur = self._agents.get(h.id)
                if cur is None:
                    continue
                out.append(
                    _ChildHandle(
                        id=cur.id,
                        agent_type=cur.agent_type,
                        inbox=cur.inbox,
                        cancel_event=cur.cancel_event,
                        thread=cur.thread,
                        status=cur.status,
                        final_output=cur.final_output,
                        error=cur.error,
                        child_events_path=cur.child_events_path,
                    )
                )
        return out

    def get(self, agent_id: str) -> Optional[_ChildHandle]:
        with self._lock:
            return self._agents.get(str(agent_id))

    def _run_child(self, agent_id: str, message: str, agent_type: str, inbox: Queue[str], cancel_event: threading.Event) -> None:
        try:
            if cancel_event.is_set():
                self._set_status(agent_id, status="cancelled", final_output=None, error=None, child_events_path=None)
                return

            # 等待输入结束标记（保证 send_input 不会因为时序丢失）
            received: List[str] = []
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not cancel_event.is_set():
                try:
                    m = inbox.get(timeout=0.1)
                except Empty:
                    continue
                if str(m) == "__END__":
                    break
                received.append(str(m))

            # 子 agent 仍需 Skills-First：消息里必须有对应 mention
            if agent_type == "research":
                mention = "$[examples:workflow].subagent_worker_research"
                artifact_path = "outputs/research.md"
                title = "澄清与范围边界"
            elif agent_type == "design":
                mention = "$[examples:workflow].subagent_worker_design"
                artifact_path = "outputs/design.md"
                title = "方案草图与接口草案"
            else:
                mention = "$[examples:workflow].subagent_worker_risk"
                artifact_path = "outputs/risks.md"
                title = "风险与验收口径"

            content_lines = [
                f"# {title}",
                "",
                "（示例产物：内容可迁移到真实项目，不依赖具体业务名词。）",
                "",
                "## Inbox（send_input 证据）",
                "",
            ]
            if received:
                for i, m in enumerate(received, start=1):
                    content_lines.append(f"- msg{i}: {m}")
            else:
                content_lines.append("- （无输入）")
            content_lines.append("")
            content = "\n".join(content_lines).rstrip() + "\n"

            backend = FakeChatBackend(
                calls=[
                    FakeChatCall(
                        events=[
                            ChatStreamEvent(
                                type="tool_calls",
                                tool_calls=[ToolCall(call_id="tc_write_artifact", name="file_write", args={"path": artifact_path, "content": content})],
                                finish_reason="tool_calls",
                            ),
                            ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                        ]
                    ),
                    FakeChatCall(
                        events=[
                            ChatStreamEvent(type="text_delta", text=f"子任务完成：{artifact_path}"),
                            ChatStreamEvent(type="completed", finish_reason="stop"),
                        ]
                    ),
                ]
            )

            approval = _ScriptedApprovalProvider(decisions=[ApprovalDecision.APPROVED_FOR_SESSION])
            agent = Agent(
                model="fake-model",
                backend=backend,
                workspace_root=self._workspace_root,
                config_paths=[self._overlay],
                approval_provider=approval,
            )

            # 用确定性 run_id，便于 report 做指针与回归
            run_id = f"run_workflows_11_{agent_id}"
            task_text = f"{mention}\n{message}\n请写入 {artifact_path}。"
            r = agent.run(task_text, run_id=run_id)

            self._set_status(agent_id, status="completed", final_output=str(r.final_output or ""), error=None, child_events_path=str(r.events_path))
        except Exception as exc:
            self._set_status(agent_id, status="failed", final_output=None, error=str(exc), child_events_path=None)

    def _set_status(
        self,
        agent_id: str,
        *,
        status: str,
        final_output: Optional[str],
        error: Optional[str],
        child_events_path: Optional[str],
    ) -> None:
        with self._lock:
            h = self._agents.get(str(agent_id))
            if h is None:
                return
            h.status = status
            h.final_output = final_output
            h.error = error
            h.child_events_path = child_events_path


def _build_master_backend() -> FakeChatBackend:
    """
    Master：spawn×3 → send_input×6 → wait。

    说明：
    - id 固定为 sub1/sub2/sub3（示例的 DeterministicCollabManager 保证）
    - 使用 __END__ 作为输入结束标记，避免时序导致子 agent 漏收 inbox
    """

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_spawn_1", name="spawn_agent", args={"message": "子任务：Research（写 outputs/research.md）", "agent_type": "research"}),
                            ToolCall(call_id="tc_spawn_2", name="spawn_agent", args={"message": "子任务：Design（写 outputs/design.md）", "agent_type": "design"}),
                            ToolCall(call_id="tc_spawn_3", name="spawn_agent", args={"message": "子任务：Risk（写 outputs/risks.md）", "agent_type": "risks"}),
                            ToolCall(call_id="tc_in_1", name="send_input", args={"id": "sub1", "message": "补充输入：请列出 3 条范围边界。"}),
                            ToolCall(call_id="tc_in_2", name="send_input", args={"id": "sub2", "message": "补充输入：请给出最小接口草案。"}),
                            ToolCall(call_id="tc_in_3", name="send_input", args={"id": "sub3", "message": "补充输入：请列出 3 条风险与验收点。"}),
                            ToolCall(call_id="tc_end_1", name="send_input", args={"id": "sub1", "message": "__END__"}),
                            ToolCall(call_id="tc_end_2", name="send_input", args={"id": "sub2", "message": "__END__"}),
                            ToolCall(call_id="tc_end_3", name="send_input", args={"id": "sub3", "message": "__END__"}),
                            ToolCall(call_id="tc_wait", name="wait", args={"ids": ["sub1", "sub2", "sub3"], "timeout_ms": 5000}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已 spawn 子 agent 并 wait 完成。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, master_events_path: str, child_rows: List[Dict[str, str]]) -> str:
    """组装 report.md（确定性）。"""

    lines: List[str] = []
    lines.append("# Workflow Report（Collab 并行子 agent / Skills-First）")
    lines.append("")
    lines.append("本报告用于展示：master 使用 spawn/wait/send_input 管理子 agent 的证据链。")
    lines.append("")
    lines.append("## Master")
    lines.append(f"- Skill: `$[examples:workflow].master_collab_planner`")
    lines.append(f"- Events: `{master_events_path}`")
    lines.append("")
    lines.append("## Children")
    lines.append("")
    for row in child_rows:
        lines.append(f"### {row['id']} ({row['agent_type']})")
        lines.append(f"- Skill: `{row['mention']}`")
        lines.append(f"- Events: `{row['events_path']}`")
        lines.append(f"- Artifact: `{row['artifact_path']}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_aggregator_backend(*, report_md: str) -> FakeChatBackend:
    """Aggregator：read_file(outputs/*) → file_write(report.md)。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_read_r", name="read_file", args={"file_path": "outputs/research.md", "offset": 1, "limit": 200}),
                            ToolCall(call_id="tc_read_d", name="read_file", args={"file_path": "outputs/design.md", "offset": 1, "limit": 200}),
                            ToolCall(call_id="tc_read_k", name="read_file", args={"file_path": "outputs/risks.md", "offset": 1, "limit": 200}),
                            ToolCall(call_id="tc_write_report", name="file_write", args={"path": "report.md", "content": report_md}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="report.md 已汇总生成。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def main() -> int:
    """脚本入口：运行 workflows_11 示例。"""

    parser = argparse.ArgumentParser(description="11_collab_parallel_subagents_workflow (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    # approvals（spawn_agent/send_input/close_agent 都需要 approval；wait 不需要）
    approvals = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # spawn1
            ApprovalDecision.APPROVED_FOR_SESSION,  # spawn2
            ApprovalDecision.APPROVED_FOR_SESSION,  # spawn3
            ApprovalDecision.APPROVED_FOR_SESSION,  # send_input sub1
            ApprovalDecision.APPROVED_FOR_SESSION,  # send_input sub2
            ApprovalDecision.APPROVED_FOR_SESSION,  # send_input sub3
            ApprovalDecision.APPROVED_FOR_SESSION,  # send_input end sub1
            ApprovalDecision.APPROVED_FOR_SESSION,  # send_input end sub2
            ApprovalDecision.APPROVED_FOR_SESSION,  # send_input end sub3
            ApprovalDecision.APPROVED_FOR_SESSION,  # aggregator file_write report.md
        ]
    )

    mgr = DeterministicCollabManager(workspace_root=workspace_root, overlay=overlay)

    # 1) master：在 agent loop 内直接调用 spawn/wait/send_input
    master = Agent(
        model="fake-model",
        backend=_build_master_backend(),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals,
        collab_manager=mgr,
    )
    master_task = "$[examples:workflow].master_collab_planner\n请 spawn 3 个子 agent，并发送补充输入，然后 wait 完成。"
    r_master = master.run(master_task, run_id="run_workflows_11_master")

    _assert_skill_injected(events_path=r_master.events_path, mention_text="$[examples:workflow].master_collab_planner")
    _assert_tool_ok(events_path=r_master.events_path, tool_name="spawn_agent")
    _assert_tool_ok(events_path=r_master.events_path, tool_name="send_input")
    _assert_tool_ok(events_path=r_master.events_path, tool_name="wait")

    # 2) 汇总：子 agent 的确定性 events_path 与产物
    children = mgr.wait(ids=["sub1", "sub2", "sub3"], timeout_ms=5000)
    assert len(children) == 3

    rows: List[Dict[str, str]] = []
    for h in children:
        if h.agent_type == "research":
            mention = "$[examples:workflow].subagent_worker_research"
            artifact_path = "outputs/research.md"
        elif h.agent_type == "design":
            mention = "$[examples:workflow].subagent_worker_design"
            artifact_path = "outputs/design.md"
        else:
            mention = "$[examples:workflow].subagent_worker_risk"
            artifact_path = "outputs/risks.md"

        assert h.status == "completed", (h.id, h.status, h.error)
        assert h.child_events_path, f"missing child_events_path for {h.id}"
        assert (workspace_root / artifact_path).exists()

        _assert_skill_injected(events_path=str(h.child_events_path), mention_text=mention)
        _assert_tool_ok(events_path=str(h.child_events_path), tool_name="file_write")

        rows.append(
            {
                "id": h.id,
                "agent_type": h.agent_type,
                "mention": mention,
                "artifact_path": artifact_path,
                "events_path": str(h.child_events_path),
            }
        )

    report_md = _format_report_md(master_events_path=r_master.events_path, child_rows=rows)

    aggregator = Agent(
        model="fake-model",
        backend=_build_aggregator_backend(report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals,
    )
    agg_task = "$[examples:workflow].aggregator\n请读取 outputs/* 并生成 report.md（包含 events_path 指针）。"
    r_agg = aggregator.run(agg_task, run_id="run_workflows_11_aggregator")

    _assert_skill_injected(events_path=r_agg.events_path, mention_text="$[examples:workflow].aggregator")
    _assert_tool_ok(events_path=r_agg.events_path, tool_name="file_write")
    assert (workspace_root / "report.md").exists()

    print("EXAMPLE_OK: workflows_11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


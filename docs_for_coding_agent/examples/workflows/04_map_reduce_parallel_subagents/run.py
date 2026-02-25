"""
总分总 + 并行子任务示例（Skills-First，离线可回归）。

本示例演示：
- 总规划（Planner）：update_plan + file_write(subtasks.json)
- 子任务并行执行（Subagents）：多个 agent 并行，各写独立产物
- 汇总（Aggregator）：read_file(inputs) + file_write(report.md)

核心约束：
- 每个角色能力必须由 Skill（SKILL.md）定义；
- 任务文本显式包含 mention，触发 `skill_injected` 证据事件；
- 默认离线可运行（Fake backend + scripted approvals）。
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall


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
                "  max_steps: 60",
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


def _assert_tool_ok(*, wal_locator: str, tool_name: str) -> None:
    """断言 WAL 中某个 tool 的 `tool_call_finished` 存在且 ok=true。"""

    events = _load_events(wal_locator)
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


def _build_planner_backend(*, subtasks_json: str) -> FakeChatBackend:
    """
    Planner backend：update_plan → file_write(subtasks.json) → summary。

    参数：
    - subtasks_json：要写入 subtasks.json 的内容（字符串，确定性）
    """

    plan = {
        "explanation": "示例：总分总并行子任务（总规划）",
        "plan": [
            {"step": "拆解子任务", "status": "completed"},
            {"step": "并行执行", "status": "pending"},
            {"step": "汇总报告", "status": "pending"},
        ],
    }

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_plan", name="update_plan", args=plan),
                            ToolCall(call_id="tc_subtasks", name="file_write", args={"path": "subtasks.json", "content": subtasks_json}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已拆解 3 个互不依赖子任务，并写入 subtasks.json。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_worker_backend(*, artifact_path: str, artifact_markdown: str) -> FakeChatBackend:
    """
    Worker backend：file_write(artifact) → summary。

    参数：
    - artifact_path：产物路径（相对 workspace_root）
    - artifact_markdown：产物内容（字符串，确定性）
    """

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_artifact", name="file_write", args={"path": artifact_path, "content": artifact_markdown})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text=f"产物已写入 {artifact_path}。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _format_report_md(*, subtasks: List[Dict[str, Any]]) -> str:
    """
    生成汇总报告 Markdown（确定性）。

    参数：
    - subtasks：每项包含 id/title/artifact_path/worker_run（wal_locator + summary）
    """

    lines: List[str] = []
    lines.append("# Workflow Report（总分总 + 并行子任务 / Skills-First）")
    lines.append("")
    lines.append("本报告用于展示：总规划 → 并行子任务 → 汇总的证据链与产物。")
    lines.append("")
    lines.append("## Subtasks")
    lines.append("")

    for s in subtasks:
        lines.append(f"### {s['id']}: {s['title']}")
        lines.append("")
        lines.append(f"- Artifact: `{s['artifact_path']}`")
        lines.append(f"- Events: `{s['worker_run']['wal_locator']}`")
        summary = str(s["worker_run"].get("summary") or "").strip()
        if summary:
            lines.append("- Summary:")
            lines.append("")
            lines.append("```text")
            lines.append(summary)
            lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _build_aggregator_backend(*, artifact_paths: List[str], report_md: str) -> FakeChatBackend:
    """
    Aggregator backend：read_file(inputs) → file_write(report.md) → summary。
    """

    read_calls: List[ToolCall] = []
    for i, p in enumerate(artifact_paths):
        read_calls.append(ToolCall(call_id=f"tc_read_{i}", name="read_file", args={"file_path": p, "offset": 1, "limit": 400}))

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=read_calls, finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_report", name="file_write", args={"path": "report.md", "content": report_md})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="report.md 已生成。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _run_worker(
    *,
    workspace_root: Path,
    overlay: Path,
    approval_provider: ApprovalProvider,
    run_id: str,
    task_text: str,
    backend: FakeChatBackend,
) -> Tuple[str, str]:
    """
    在线程内运行一个子 agent。

    返回：
    - (wal_locator, summary)
    """

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approval_provider,
    )
    r = agent.run(task_text, run_id=run_id)
    assert r.status == "completed"
    return (r.wal_locator, r.final_output)


def main() -> int:
    """脚本入口：运行 workflows_04 示例。"""

    parser = argparse.ArgumentParser(description="04_map_reduce_parallel_subagents (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    subtasks_obj = {
        "task_title": "示例：交付一个可迁移的 agent 工作流骨架",
        "subtasks": [
            {"id": "research", "title": "澄清与范围边界", "artifact_path": "outputs/research.md", "skill": "$[examples:workflow].subagent_researcher"},
            {"id": "design", "title": "方案草图与接口草案", "artifact_path": "outputs/design.md", "skill": "$[examples:workflow].subagent_designer"},
            {"id": "risks", "title": "风险与验收口径", "artifact_path": "outputs/risks.md", "skill": "$[examples:workflow].subagent_risk_assessor"},
        ],
    }
    subtasks_json = json.dumps(subtasks_obj, ensure_ascii=False, indent=2) + "\n"

    # 1) 总规划（Planner）
    planner_approval = _ScriptedApprovalProvider(decisions=[ApprovalDecision.APPROVED_FOR_SESSION])  # file_write subtasks.json
    planner = Agent(
        model="fake-model",
        backend=_build_planner_backend(subtasks_json=subtasks_json),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=planner_approval,
    )
    planner_task = "$[examples:workflow].master_planner\n请总规划：拆解 3 个互不依赖子任务，并写 subtasks.json。"
    r_plan = planner.run(planner_task, run_id="run_workflows_04_planner")
    assert r_plan.status == "completed"
    assert (workspace_root / "subtasks.json").exists()

    _assert_skill_injected(wal_locator=r_plan.wal_locator, mention_text="$[examples:workflow].master_planner")
    _assert_event_exists(wal_locator=r_plan.wal_locator, event_type="plan_updated")
    _assert_event_exists(wal_locator=r_plan.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_plan.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_plan.wal_locator, tool_name="file_write")

    loaded = json.loads((workspace_root / "subtasks.json").read_text(encoding="utf-8"))
    subtasks: List[Dict[str, Any]] = list(loaded.get("subtasks") or [])
    assert len(subtasks) == 3

    # 2) 分：并行子任务（Subagents）
    workers: List[Dict[str, Any]] = []
    for s in subtasks:
        sid = str(s["id"])
        artifact_path = str(s["artifact_path"])
        title = str(s["title"])
        mention = str(s["skill"])

        content = "\n".join(
            [
                f"# {title}",
                "",
                "（示例产物：内容可迁移到真实项目，不依赖具体业务名词。）",
                "",
                "- 目标：给出可执行的要点清单",
                "- 输出：本文件作为子任务产物",
                "",
            ]
        ).rstrip() + "\n"

        backend = _build_worker_backend(artifact_path=artifact_path, artifact_markdown=content)
        approval = _ScriptedApprovalProvider(decisions=[ApprovalDecision.APPROVED_FOR_SESSION])
        task = f"{mention}\n子任务：{title}\n请生成产物并写入 {artifact_path}。"
        workers.append(
            {
                "id": sid,
                "title": title,
                "artifact_path": artifact_path,
                "mention": mention,
                "backend": backend,
                "approval": approval,
                "task": task,
                "run_id": f"run_workflows_04_{sid}",
            }
        )

    results_by_id: Dict[str, Dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_to_worker = {
            pool.submit(
                _run_worker,
                workspace_root=workspace_root,
                overlay=overlay,
                approval_provider=w["approval"],
                run_id=w["run_id"],
                task_text=w["task"],
                backend=w["backend"],
            ): w
            for w in workers
        }
        for fut in as_completed(list(fut_to_worker.keys())):
            w = fut_to_worker[fut]
            wal_locator, summary = fut.result()
            results_by_id[str(w["id"])] = {"wal_locator": wal_locator, "summary": summary}

    # 断言每个子任务产物存在 + skills/approvals 证据存在
    for w in workers:
        sid = str(w["id"])
        artifact = workspace_root / str(w["artifact_path"])
        assert artifact.exists(), f"missing artifact: {artifact}"

        run = results_by_id[sid]
        _assert_skill_injected(wal_locator=run["wal_locator"], mention_text=str(w["mention"]))
        _assert_event_exists(wal_locator=run["wal_locator"], event_type="approval_requested")
        _assert_event_exists(wal_locator=run["wal_locator"], event_type="approval_decided")
        _assert_tool_ok(wal_locator=run["wal_locator"], tool_name="file_write")

    # 3) 总：汇总报告（Aggregator）
    enriched: List[Dict[str, Any]] = []
    for s in subtasks:
        sid = str(s["id"])
        enriched.append(
            {
                "id": sid,
                "title": str(s["title"]),
                "artifact_path": str(s["artifact_path"]),
                "worker_run": results_by_id[sid],
            }
        )
    report_md = _format_report_md(subtasks=enriched)

    aggregator_approval = _ScriptedApprovalProvider(decisions=[ApprovalDecision.APPROVED_FOR_SESSION])
    aggregator = Agent(
        model="fake-model",
        backend=_build_aggregator_backend(artifact_paths=[str(s["artifact_path"]) for s in subtasks], report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=aggregator_approval,
    )
    agg_task = "$[examples:workflow].result_aggregator\n请读取 outputs/* 并生成 report.md（包含各子任务 wal_locator 指针）。"
    r_agg = aggregator.run(agg_task, run_id="run_workflows_04_aggregator")
    assert r_agg.status == "completed"
    assert (workspace_root / "report.md").exists()

    _assert_skill_injected(wal_locator=r_agg.wal_locator, mention_text="$[examples:workflow].result_aggregator")
    _assert_event_exists(wal_locator=r_agg.wal_locator, event_type="approval_requested")
    _assert_event_exists(wal_locator=r_agg.wal_locator, event_type="approval_decided")
    _assert_tool_ok(wal_locator=r_agg.wal_locator, tool_name="file_write")

    print("EXAMPLE_OK: workflows_04")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
WAL fork + replay resume 示例（Skills-First，离线可回归）。

实现目标：
- 第一次 run：写 checkpoint 后“故意中断”（Fake backend calls 耗尽 → run_failed）
- Fork Planner：读取 WAL 并建议 fork 点
- Fork：调用 `skills_runtime.state.fork.fork_run(...)` 生成新 run
- 第二次 run：以 replay resume 继续执行并写 final
- Reporter：生成 report.md（含 evidence 指针）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.state.fork import fork_run
from skills_runtime.tools.protocol import ToolCall


def _write_overlay(*, workspace_root: Path, skills_root: Path, safety_mode: str = "ask", resume_strategy: str = "replay") -> Path:
    """
    写入示例运行用 overlay（runtime.yaml）。

    参数：
    - resume_strategy：summary|replay（示例使用 replay）
    """

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "run:",
                "  max_steps: 40",
                f"  resume_strategy: {json.dumps(str(resume_strategy))}",
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
                "      namespace: \"examples:workflow\"",
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


def _load_events_lines(wal_locator: Path) -> List[Dict[str, Any]]:
    """按行读取 events.jsonl。"""

    events: List[Dict[str, Any]] = []
    for raw in wal_locator.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _find_last_ok_tool_index(*, wal_locator: Path, tool_name: str) -> int:
    """
    找到最后一次 `tool_call_finished` 且 ok=true 的行号（0-based）。
    """

    lines = wal_locator.read_text(encoding="utf-8").splitlines()
    last = -1
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        obj = json.loads(s)
        if obj.get("type") != "tool_call_finished":
            continue
        payload = obj.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            last = i
    if last < 0:
        raise AssertionError(f"missing ok tool_call_finished for tool={tool_name}")
    return last


def _assert_skill_injected(*, wal_locator: Path, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    for ev in _load_events_lines(wal_locator):
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_run_started_resume_enabled(*, wal_locator: Path, expected_strategy: str) -> None:
    """
    断言 run_started.resume.enabled=true 且 strategy 匹配。
    """

    for ev in _load_events_lines(wal_locator):
        if ev.get("type") != "run_started":
            continue
        resume = (ev.get("payload") or {}).get("resume") or {}
        if resume.get("enabled") is True and str(resume.get("strategy") or "") == expected_strategy:
            prev = int(resume.get("previous_events") or 0)
            if prev > 0:
                return
    raise AssertionError("missing run_started resume enabled evidence")


def _build_checkpoint_backend(*, checkpoint_content: str) -> FakeChatBackend:
    """
    第一次 run：写 checkpoint 后故意中断（calls 耗尽触发 run_failed）。
    """

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_checkpoint", name="file_write", args={"path": "checkpoint.txt", "content": checkpoint_content})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            )
            # 故意不提供后续 FakeChatCall：agent 将在下一次请求 LLM 时抛 ValueError → run_failed
        ]
    )


def _build_fork_planner_backend(*, src_run_id: str, suggested_index: int) -> FakeChatBackend:
    """
    Fork Planner：read_file(WAL) → 输出建议 index。
    """

    wal_rel = f".skills_runtime_sdk/runs/{src_run_id}/events.jsonl"
    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_read_wal", name="read_file", args={"file_path": wal_rel, "offset": 1, "limit": 200})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text=f"建议 fork 到 up_to_index_inclusive={suggested_index}（最后一次成功 tool_call_finished:file_write）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_resume_backend(*, report_mark: str) -> FakeChatBackend:
    """
    第二次 run：读取 checkpoint 并写 final（完成）。
    """

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_read_checkpoint", name="read_file", args={"file_path": "checkpoint.txt", "offset": 1, "limit": 50})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_write_final", name="file_write", args={"path": "final.txt", "content": report_mark})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已续做完成：final.txt 已写入。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_reporter_backend(*, report_md: str) -> FakeChatBackend:
    """Reporter：file_write(report.md)。"""

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_report", name="file_write", args={"path": "report.md", "content": report_md})],
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


def _format_report_md(
    *,
    src_run_id: str,
    src_wal_locator: str,
    dst_run_id: str,
    dst_wal_locator: str,
    fork_index: int,
) -> str:
    """生成 report.md（确定性）。"""

    return (
        "\n".join(
            [
                "# Workflow Report（WAL fork + replay resume）",
                "",
                f"- src_run_id: `{src_run_id}`",
                f"- src_wal_locator: `{src_wal_locator}`",
                f"- fork_index (0-based): `{fork_index}`",
                f"- dst_run_id: `{dst_run_id}`",
                f"- dst_wal_locator: `{dst_wal_locator}`",
                "",
                "## Artifacts",
                "",
                "- `checkpoint.txt`：第一次 run 写入（断点）",
                "- `final.txt`：第二次 run 写入（续做完成）",
                "",
            ]
        ).rstrip()
        + "\n"
    )


def main() -> int:
    """脚本入口：运行 workflows_06 示例。"""

    parser = argparse.ArgumentParser(description="06_wal_fork_and_resume_pipeline (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask", resume_strategy="replay")

    # approvals：分别给每个 run 的写操作提供批准
    approvals_checkpoint = _ScriptedApprovalProvider(decisions=[ApprovalDecision.APPROVED_FOR_SESSION])
    approvals_resume = _ScriptedApprovalProvider(decisions=[ApprovalDecision.APPROVED_FOR_SESSION])
    approvals_report = _ScriptedApprovalProvider(decisions=[ApprovalDecision.APPROVED_FOR_SESSION])

    # 1) 第一次 run：写 checkpoint 后中断（run_failed）
    src_run_id = "run_workflows_06_src"
    checkpoint_content = "CHECKPOINT_OK\n"
    checkpoint_agent = Agent(
        model="fake-model",
        backend=_build_checkpoint_backend(checkpoint_content=checkpoint_content),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals_checkpoint,
    )
    task_checkpoint = "$[examples:workflow].checkpoint_writer\n请写入 checkpoint.txt 作为断点产物。"
    r1 = checkpoint_agent.run(task_checkpoint, run_id=src_run_id)
    assert r1.status == "failed"
    assert (workspace_root / "checkpoint.txt").exists()

    src_wal_path = Path(r1.wal_locator)
    _assert_skill_injected(wal_locator=src_wal_path, mention_text="$[examples:workflow].checkpoint_writer")

    # 2) Fork Planner：读 WAL 并建议 fork 点（示例：最后一次成功 file_write）
    fork_index = _find_last_ok_tool_index(wal_locator=src_wal_path, tool_name="file_write")

    planner_agent = Agent(
        model="fake-model",
        backend=_build_fork_planner_backend(src_run_id=src_run_id, suggested_index=fork_index),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=_ScriptedApprovalProvider(decisions=[]),
    )
    task_plan = f"$[examples:workflow].wal_fork_planner\nsrc_run_id={src_run_id}。请读取 WAL 并给出 fork index 建议。"
    r_plan = planner_agent.run(task_plan, run_id="run_workflows_06_fork_planner")
    assert r_plan.status == "completed"
    _assert_skill_injected(wal_locator=Path(r_plan.wal_locator), mention_text="$[examples:workflow].wal_fork_planner")

    # 3) Fork：生成 dst_run_id 的 events.jsonl（前缀）
    dst_run_id = "run_workflows_06_dst"
    dst_wal_path = fork_run(workspace_root=workspace_root, src_run_id=src_run_id, dst_run_id=dst_run_id, up_to_index_inclusive=fork_index)
    assert dst_wal_path.exists()

    # 4) 第二次 run：在 dst_run_id 上 replay resume 并完成 final
    resume_agent = Agent(
        model="fake-model",
        backend=_build_resume_backend(report_mark="FINAL_OK\n"),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals_resume,
    )
    task_resume = "$[examples:workflow].resume_finisher\n请读取 checkpoint.txt 并写入 final.txt（续做完成）。"
    r2 = resume_agent.run(task_resume, run_id=dst_run_id)
    assert r2.status == "completed"
    assert (workspace_root / "final.txt").exists()

    dst_wal_path2 = Path(r2.wal_locator)
    _assert_skill_injected(wal_locator=dst_wal_path2, mention_text="$[examples:workflow].resume_finisher")
    _assert_run_started_resume_enabled(wal_locator=dst_wal_path2, expected_strategy="replay")

    # 5) Reporter：汇总
    report_md = _format_report_md(
        src_run_id=src_run_id,
        src_wal_locator=str(src_wal_path),
        dst_run_id=dst_run_id,
        dst_wal_locator=str(dst_wal_path2),
        fork_index=fork_index,
    )
    reporter = Agent(
        model="fake-model",
        backend=_build_reporter_backend(report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals_report,
    )
    task_report = "$[examples:workflow].resume_reporter\n请写 report.md 汇总 fork/resume 结果。"
    r3 = reporter.run(task_report, run_id="run_workflows_06_report")
    assert r3.status == "completed"
    _assert_skill_injected(wal_locator=Path(r3.wal_locator), mention_text="$[examples:workflow].resume_reporter")
    assert (workspace_root / "report.md").exists()

    print("EXAMPLE_OK: workflows_06")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

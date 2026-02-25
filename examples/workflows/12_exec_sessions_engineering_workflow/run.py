"""
exec sessions 工程式交互示例（Skills-First，离线可回归）。

本示例演示：
- session_operator：在 agent loop 内调用 exec_command + write_stdin（交互式 PTY 会话）
- reporter：解析 WAL 中的 tool 输出痕迹（不依赖输出细节），生成确定性 report.md

核心约束：
- 每个角色能力必须由 Skill（SKILL.md）定义；
- 任务文本显式包含 mention，触发 `skill_injected` 证据事件；
- 默认离线可运行（Fake backend + scripted approvals）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills_runtime.agent import Agent
from skills_runtime.core.exec_sessions import ExecSessionManager
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall


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
    out: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _assert_skill_injected(*, wal_locator: str, mention_text: str) -> None:
    """断言 WAL 中出现过指定 mention 的 `skill_injected` 事件。"""

    for ev in _load_events(wal_locator):
        if ev.get("type") != "skill_injected":
            continue
        payload = ev.get("payload") or {}
        if payload.get("mention_text") == mention_text:
            return
    raise AssertionError(f"missing skill_injected event for mention: {mention_text}")


def _assert_tool_ok(*, wal_locator: str, tool_name: str) -> None:
    """断言 WAL 中某个 tool 的 `tool_call_finished` 存在且 ok=true。"""

    for ev in _load_events(wal_locator):
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return
    raise AssertionError(f"missing ok tool_call_finished for tool={tool_name}")


def _detect_skip_no_pty_reason(*, wal_locator: str, tool_name: str) -> Optional[str]:
    """
    检测“明确 PTY 不可用”场景并返回 skip 原因；否则返回 None。

    约束：
    - 仅当 WAL 中存在该 tool 的 `tool_call_finished`，且 result.ok != true；
    - 且 stderr/错误信息包含明显 PTY 不可用信号（例如包含 'pty'）；
    才允许对示例做 SKIP，避免吞掉非 PTY 类回归。
    """

    for ev in _load_events(wal_locator):
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        if payload.get("tool") != tool_name:
            continue
        result = payload.get("result") or {}
        if result.get("ok") is True:
            return None
        stderr = str(result.get("stderr") or "")
        error_kind = str(result.get("error_kind") or "")
        msg = f"{stderr} {error_kind}".strip().lower()
        if "pty" in msg:
            return stderr or f"{tool_name} failed: {error_kind}".strip()
    return None


def _collect_exec_stdout(*, wal_locator: str) -> str:
    """
    汇总 exec_command/write_stdin 的 stdout（用于做“标记出现”判断）。

    注意：
    - 不能依赖输出的精确内容（PTY 可能会回显输入/带控制字符）
    - 这里只用于检测 READY/ECHO/BYE 是否出现
    """

    out = ""
    for ev in _load_events(wal_locator):
        if ev.get("type") != "tool_call_finished":
            continue
        payload = ev.get("payload") or {}
        tool = payload.get("tool")
        if tool not in ("exec_command", "write_stdin"):
            continue
        result = payload.get("result") or {}
        out += str(result.get("stdout") or "")
    return out


def _format_report_md(*, session_wal_locator: str, observed: Dict[str, bool]) -> str:
    """组装 report.md（确定性：只写布尔标记，不写原始 stdout）。"""

    lines: List[str] = []
    lines.append("# Workflow Report（exec sessions 工程式交互 / Skills-First）")
    lines.append("")
    lines.append("本报告只记录“关键标记是否出现”，避免 PTY 输出差异导致回归不稳定。")
    lines.append("")
    lines.append(f"- SessionRun wal_locator: `{session_wal_locator}`")
    lines.append("")
    lines.append("## Observed Markers")
    for k in ["READY", "ECHO:hello", "BYE"]:
        lines.append(f"- {k}: `{bool(observed.get(k, False))}`")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_session_operator_backend(*, python_executable: str) -> FakeChatBackend:
    """
    session_operator：exec_command → write_stdin(hello) → write_stdin(bye) → write_stdin(poll) → summary。

    说明：
    - ExecSessionManager 的 session_id 从 1 开始；本示例只 spawn 一次，因此 write_stdin 使用 session_id=1。
    """

    # 交互式脚本：READY → 读一行 → ECHO:<line> → 再读一行 → BYE → exit 0
    py = str(python_executable)
    cmd = (
        f"{py} -u -c \"import sys; "
        "print('READY'); sys.stdout.flush(); "
        "l1=sys.stdin.readline(); print('ECHO:'+l1.strip()); sys.stdout.flush(); "
        "l2=sys.stdin.readline(); print('BYE'); sys.stdout.flush()\""
    )

    return FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(call_id="tc_exec", name="exec_command", args={"cmd": cmd, "yield_time_ms": 200, "tty": True, "sandbox": "none"}),
                            ToolCall(call_id="tc_w1", name="write_stdin", args={"session_id": 1, "chars": "hello\r", "yield_time_ms": 200}),
                            ToolCall(call_id="tc_w2", name="write_stdin", args={"session_id": 1, "chars": "bye\r", "yield_time_ms": 200}),
                            ToolCall(call_id="tc_poll", name="write_stdin", args={"session_id": 1, "yield_time_ms": 200}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="交互会话已完成（详见 WAL 证据）。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )


def _build_reporter_backend(*, report_md: str) -> FakeChatBackend:
    """reporter：file_write(report.md) → summary。"""

    return FakeChatBackend(
        calls=[
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


def main() -> int:
    """脚本入口：运行 workflows_12 示例。"""

    parser = argparse.ArgumentParser(description="12_exec_sessions_engineering_workflow (skills-first, offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    example_dir = Path(__file__).resolve().parent
    skills_root = (example_dir / "skills").resolve()
    overlay = _write_overlay(workspace_root=workspace_root, skills_root=skills_root, safety_mode="ask")

    approvals = _ScriptedApprovalProvider(
        decisions=[
            ApprovalDecision.APPROVED_FOR_SESSION,  # exec_command
            ApprovalDecision.APPROVED_FOR_SESSION,  # write_stdin hello
            ApprovalDecision.APPROVED_FOR_SESSION,  # write_stdin bye
            ApprovalDecision.APPROVED_FOR_SESSION,  # write_stdin poll
            ApprovalDecision.APPROVED_FOR_SESSION,  # reporter file_write report.md
        ]
    )

    exec_sessions = ExecSessionManager()

    # 1) session_operator：执行交互式会话（Agent loop 内工具调用）
    operator = Agent(
        model="fake-model",
        backend=_build_session_operator_backend(python_executable=sys.executable),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals,
        exec_sessions=exec_sessions,
    )
    op_task = "$[examples:workflow].session_operator\n请启动交互式会话并完成一次输入输出（READY/ECHO/BYE）。"
    r_op = operator.run(op_task, run_id="run_workflows_12_session_operator")

    _assert_skill_injected(wal_locator=r_op.wal_locator, mention_text="$[examples:workflow].session_operator")

    # 某些受限环境（例如缺少 /dev/pts 或 PTY 配额很小）无法分配 PTY（exec_command tty=True）。
    # 该示例的目标是演示“工程式交互 + WAL 证据”，在无 PTY 环境下无法成立；
    # 因此仅对“明确的 PTY 不可用”场景做 skip，避免离线回归在 CI 上漂移。
    no_pty_reason = _detect_skip_no_pty_reason(wal_locator=r_op.wal_locator, tool_name="exec_command")
    if no_pty_reason:
        print(f"[example] skipped: exec_command cannot allocate PTY: {no_pty_reason}")
        print("EXAMPLE_OK: workflows_12 (SKIPPED_NO_PTY)")
        return 0

    _assert_tool_ok(wal_locator=r_op.wal_locator, tool_name="exec_command")
    _assert_tool_ok(wal_locator=r_op.wal_locator, tool_name="write_stdin")

    # 2) reporter：解析 WAL 中的 stdout（只判断关键标记是否出现）并写 report.md
    combined = _collect_exec_stdout(wal_locator=r_op.wal_locator)
    combined_norm = re.sub(r"[\\r\\n]+", "\n", combined)
    observed = {
        "READY": "READY" in combined_norm,
        "ECHO:hello": "ECHO:hello" in combined_norm,
        "BYE": "BYE" in combined_norm,
    }
    report_md = _format_report_md(session_wal_locator=r_op.wal_locator, observed=observed)

    reporter = Agent(
        model="fake-model",
        backend=_build_reporter_backend(report_md=report_md),
        workspace_root=workspace_root,
        config_paths=[overlay],
        approval_provider=approvals,
    )
    rep_task = "$[examples:workflow].reporter\n请写入 report.md，总结 READY/ECHO/BYE 是否出现，并附上 wal_locator。"
    r_rep = reporter.run(rep_task, run_id="run_workflows_12_reporter")

    _assert_skill_injected(wal_locator=r_rep.wal_locator, mention_text="$[examples:workflow].reporter")
    _assert_tool_ok(wal_locator=r_rep.wal_locator, tool_name="file_write")
    assert (workspace_root / "report.md").exists()

    # 最小断言：关键标记都应出现（不依赖输出的精确格式）
    assert observed["READY"] is True
    assert observed["ECHO:hello"] is True
    assert observed["BYE"] is True

    print("EXAMPLE_OK: workflows_12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

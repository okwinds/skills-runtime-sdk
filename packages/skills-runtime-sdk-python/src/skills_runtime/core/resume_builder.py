"""
Resume 相关逻辑（从 core.agent_loop 拆出）。

支持两种策略：
- summary：根据 WAL tail 生成一条 assistant 摘要消息（Phase 2 默认）
- replay：从 WAL 重建 history + approvals 缓存（更完整，但对 WAL 结构要求更高）
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Set

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.state.wal_protocol import WalBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResumeInfo:
    """Resume 计算结果：包含 WAL 事件统计与可选的 replay/summary 产物。"""
    existing_events_all: List[AgentEvent]
    existing_events_count: int
    existing_events_tail: List[AgentEvent]

    resume_replay_history: Optional[List[Dict[str, Any]]]
    resume_replay_denied: Dict[str, int]
    resume_replay_approved: Set[str]

    resume_summary: Optional[str]


def _load_existing_run_events(*, wal: WalBackend, run_id: str) -> tuple[List[AgentEvent], int, List[AgentEvent]]:
    """从 WAL 读取指定 run_id 的历史事件，并返回全量/计数/尾部窗口。"""
    existing_events_all = list(wal.iter_events(run_id=run_id))
    existing_events_count = len(existing_events_all)
    existing_events_tail: List[AgentEvent] = list(deque(existing_events_all, maxlen=200))
    return existing_events_all, existing_events_count, existing_events_tail


def _build_resume_summary(
    *,
    existing_events_count: int,
    existing_events_tail: List[AgentEvent],
    initial_history: Optional[List[Dict[str, Any]]],
    resume_strategy: str,
    resume_replay_history: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """
    Phase 2 Resume：从已存在 WAL 生成一条摘要型 assistant 消息。

    触发条件：
    - WAL 非空；
    - 调用方未显式传入 initial_history（显式历史以调用方为准，不自动注入 resume 摘要）。
    """

    if existing_events_count <= 0:
        return None
    if initial_history is not None:
        return None
    if resume_strategy == "replay" and resume_replay_history is not None:
        return None

    last_run_started: Optional[AgentEvent] = None
    last_terminal: Optional[AgentEvent] = None
    last_tools: List[AgentEvent] = []

    for ev in reversed(existing_events_tail):
        if last_terminal is None and ev.type in ("run_completed", "run_failed", "run_cancelled"):
            last_terminal = ev
        if last_run_started is None and ev.type == "run_started":
            last_run_started = ev
        if ev.type == "tool_call_finished" and len(last_tools) < 5:
            last_tools.append(ev)
        if last_terminal is not None and last_run_started is not None and len(last_tools) >= 5:
            break

    prev_task = ""
    if last_run_started is not None:
        prev_task = str(last_run_started.payload.get("task") or "")

    terminal_type = last_terminal.type if last_terminal is not None else "unknown"
    terminal_text = ""
    if last_terminal is not None:
        if last_terminal.type == "run_completed":
            terminal_text = str(last_terminal.payload.get("final_output") or "")
        else:
            terminal_text = str(last_terminal.payload.get("message") or "")

    lines: List[str] = ["[Resume Summary]"]
    if prev_task:
        lines.append(f"previous_task: {prev_task}")
    lines.append(f"previous_events: {existing_events_count}")
    lines.append(f"previous_terminal: {terminal_type}")
    if terminal_text:
        lines.append(f"previous_terminal_text: {terminal_text}")
        if last_tools:
            lines.append("recent_tools:")
            for e in reversed(last_tools):
                tool = str(e.payload.get("tool") or e.payload.get("name") or "")
                if not tool:
                    tool = "unknown_tool"
                result = e.payload.get("result") or {}
                ok = result.get("ok")
                error_kind = result.get("error_kind")
                lines.append(f"- {tool} ok={ok} error_kind={error_kind}")

    out = "\n".join(lines).strip()
    if len(out) > 4096:
        out = out[:4096] + "\n...<truncated>"
    return out


def prepare_resume(
    *,
    wal: WalBackend,
    run_id: str,
    initial_history: Optional[List[Dict[str, Any]]],
    resume_strategy: str,
) -> ResumeInfo:
    """根据 WAL 与 resume_strategy 生成 ResumeInfo（replay 或 summary）。"""
    existing_events_all, existing_events_count, existing_events_tail = _load_existing_run_events(wal=wal, run_id=run_id)

    resume_replay_history: Optional[List[Dict[str, Any]]] = None
    resume_replay_denied: Dict[str, int] = {}
    resume_replay_approved: Set[str] = set()

    if existing_events_count > 0 and initial_history is None and resume_strategy == "replay":
        try:
            from skills_runtime.state.replay import rebuild_resume_replay_state

            st = rebuild_resume_replay_state(existing_events_all)
            resume_replay_history = st.history
            resume_replay_denied = st.denied_approvals_by_key
            resume_replay_approved = st.approved_for_session_keys
        except Exception:
            # 防御性兜底：回放失败时回退到 Phase 2 summary-based resume；可能因 WAL 损坏等原因。
            logger.warning("Resume replay failed; falling back to summary-based resume", exc_info=True)
            resume_replay_history = None
            resume_replay_denied = {}
            resume_replay_approved = set()

    resume_summary = _build_resume_summary(
        existing_events_count=existing_events_count,
        existing_events_tail=existing_events_tail,
        initial_history=initial_history,
        resume_strategy=resume_strategy,
        resume_replay_history=resume_replay_history,
    )

    return ResumeInfo(
        existing_events_all=existing_events_all,
        existing_events_count=existing_events_count,
        existing_events_tail=existing_events_tail,
        resume_replay_history=resume_replay_history,
        resume_replay_denied=resume_replay_denied,
        resume_replay_approved=resume_replay_approved,
        resume_summary=resume_summary,
    )


__all__ = ["ResumeInfo", "prepare_resume"]

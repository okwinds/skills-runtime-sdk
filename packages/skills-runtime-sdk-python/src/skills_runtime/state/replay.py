"""
WAL 回放与 Resume/Fork 辅助（Phase 4）。

对齐：
- `docs/specs/skills-runtime-sdk/docs/state.md` §5（Phase 4：Fork/Resume 逐事件重建）

本模块的目标是：在不依赖 LLM 的情况下，从 `.skills_runtime_sdk/runs/<run_id>/events.jsonl`
重建出可继续运行所需的“最小运行态”：
- history（消息列表，用于 prompt_manager 组装 messages）
- approvals cache（approved_for_session_keys / denied_approvals_by_key）

说明（取舍）：
- Phase 2 的最小 history 仅包含：assistant 最终输出 + tool outputs（role=tool）。
- WAL 目前不会持久化完整的 system/prompt 组装细节，因此本模块只重建“Agent 运行期维护的 history”。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from skills_runtime.core.contracts import AgentEvent


@dataclass(frozen=True)
class ResumeReplayState:
    """从 WAL 回放得到的 resume 状态。"""

    history: List[Dict[str, Any]]
    approved_for_session_keys: set[str]
    denied_approvals_by_key: Dict[str, int]


def _events_after_last_run_started(events: List[AgentEvent]) -> List[AgentEvent]:
    """
    只回放“最近一次 run_started 之后”的事件片段。

    原因：
    - 同一 `run_id` 可能被 resume 多次，WAL 会包含多个 run_started/terminal 片段；
    - 回放最近一段更贴近“上次运行结束时的 history 语义”，也能避免重复注入旧片段。
    """

    last_idx = -1
    for i, ev in enumerate(events):
        if ev.type == "run_started":
            last_idx = i
    if last_idx < 0:
        return list(events)
    return list(events[last_idx + 1 :])


def rebuild_resume_replay_state(events: List[AgentEvent]) -> ResumeReplayState:
    """
    从 WAL 事件列表重建 Phase 4 replay resume 所需状态。

    参数：
    - events：按 WAL 顺序的 AgentEvent 列表（通常来自 JsonlWal.iter_events()）

    返回：
    - ResumeReplayState：history + approvals cache
    """

    seg = _events_after_last_run_started(events)

    history: List[Dict[str, Any]] = []
    approved_for_session_keys: set[str] = set()
    denied_approvals_by_key: Dict[str, int] = {}

    for ev in seg:
        if ev.type == "tool_call_finished":
            call_id = str(ev.payload.get("call_id") or "").strip()
            result_obj = ev.payload.get("result")
            if not call_id:
                continue
            if not isinstance(result_obj, dict):
                continue
            history.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(result_obj, ensure_ascii=False),
                }
            )
        elif ev.type == "run_completed":
            final_output = ev.payload.get("final_output")
            if isinstance(final_output, str) and final_output:
                history.append({"role": "assistant", "content": final_output})
        elif ev.type == "approval_decided":
            approval_key = str(ev.payload.get("approval_key") or "").strip()
            decision = str(ev.payload.get("decision") or "").strip().lower()
            if not approval_key:
                continue
            if decision == "approved_for_session":
                approved_for_session_keys.add(approval_key)
            elif decision == "denied":
                denied_approvals_by_key[approval_key] = int(denied_approvals_by_key.get(approval_key, 0)) + 1

    return ResumeReplayState(
        history=history,
        approved_for_session_keys=approved_for_session_keys,
        denied_approvals_by_key=denied_approvals_by_key,
    )


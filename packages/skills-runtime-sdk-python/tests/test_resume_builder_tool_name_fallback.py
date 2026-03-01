from __future__ import annotations

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.resume_builder import _build_resume_summary


def test_resume_summary_recent_tools_falls_back_to_name_field() -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 3.2）：
    - consumer 必须优先读取 payload.tool，但旧 WAL 可能只有 payload.name；
    - resume summary 需要保持可读，避免 recent_tools 出现空工具名。
    """

    events_tail = [
        AgentEvent(type="run_started", timestamp="2026-02-09T00:00:00Z", run_id="r1", payload={"task": "t"}),
        AgentEvent(
            type="tool_call_finished",
            timestamp="2026-02-09T00:00:01Z",
            run_id="r1",
            payload={"call_id": "c1", "name": "legacy_tool", "result": {"ok": True, "error_kind": None}},
        ),
        AgentEvent(type="run_failed", timestamp="2026-02-09T00:00:02Z", run_id="r1", payload={"message": "x"}),
    ]

    out = _build_resume_summary(
        existing_events_count=123,
        existing_events_tail=events_tail,
        initial_history=None,
        resume_strategy="summary",
        resume_replay_history=None,
    )
    assert out is not None
    assert "legacy_tool" in out


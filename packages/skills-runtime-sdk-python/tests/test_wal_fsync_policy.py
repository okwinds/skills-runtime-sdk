"""Task 1.3 + 1.4：验证 WAL fsync 分级策略（终态事件 fsync，delta 事件只 flush）。"""

from __future__ import annotations

import time
import unittest.mock as mock
from pathlib import Path

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.state.jsonl_wal import JsonlWal, _TERMINAL_EVENT_TYPES


# ─── Task 1.3：fsync 调用次数验证 ───────────────────────────────────────────

def _make_event(type_: str) -> AgentEvent:
    return AgentEvent(type=type_, timestamp="2026-01-01T00:00:00Z", run_id="r1", payload={})


def test_wal_terminal_events_trigger_fsync(tmp_path: Path) -> None:
    """终态事件 MUST 触发 os.fsync。"""
    wal = JsonlWal(tmp_path / "events.jsonl")

    with mock.patch("os.fsync") as mock_fsync:
        for ev_type in _TERMINAL_EVENT_TYPES:
            wal.append(_make_event(ev_type))

        assert mock_fsync.call_count == len(_TERMINAL_EVENT_TYPES), (
            f"期望 {len(_TERMINAL_EVENT_TYPES)} 次 fsync，实际 {mock_fsync.call_count} 次"
        )


def test_wal_delta_events_do_not_trigger_fsync(tmp_path: Path) -> None:
    """非终态事件 MUST NOT 触发 os.fsync。"""
    delta_types = [
        "run_started",
        "llm_request_started",
        "llm_response_delta",
        "tool_call_requested",
        "tool_call_started",
        "tool_call_finished",
        "approval_requested",
        "approval_decided",
        "skill_injected",
    ]
    wal = JsonlWal(tmp_path / "events.jsonl")

    with mock.patch("os.fsync") as mock_fsync:
        for ev_type in delta_types:
            wal.append(_make_event(ev_type))

        assert mock_fsync.call_count == 0, (
            f"delta 事件不应触发 fsync，实际调用了 {mock_fsync.call_count} 次"
        )


def test_wal_mixed_events_fsync_only_on_terminal(tmp_path: Path) -> None:
    """混合事件序列中，只有终态事件触发 fsync。"""
    wal = JsonlWal(tmp_path / "events.jsonl")

    events_sequence = [
        "run_started",         # delta
        "llm_response_delta",  # delta
        "tool_call_finished",  # delta
        "run_completed",       # TERMINAL → fsync
    ]

    with mock.patch("os.fsync") as mock_fsync:
        for ev_type in events_sequence:
            wal.append(_make_event(ev_type))

        assert mock_fsync.call_count == 1  # 只有 run_completed 触发


# ─── Task 1.4：性能回归测试（50 个 delta 事件耗时 < 每事件 fsync 的估算基准）─

def test_wal_50_delta_events_no_fsync_overhead(tmp_path: Path) -> None:
    """50 个 delta 事件追加耗时 MUST 远低于"假设每次 fsync 5ms"的串行基准（250ms）。"""
    wal = JsonlWal(tmp_path / "events.jsonl")

    # 计时 50 个 delta 事件追加
    start = time.perf_counter()
    for i in range(50):
        wal.append(_make_event("llm_response_delta"))
    elapsed = time.perf_counter() - start

    # 保守上限：50ms（即使是极慢的机器，无 fsync 的 50 次 flush 也不应超过 50ms）
    assert elapsed < 0.050, (
        f"50 个 delta 事件追加耗时 {elapsed*1000:.1f}ms，超过 50ms 上限（无 fsync 时不应有此延迟）"
    )

from __future__ import annotations

from pathlib import Path

from agent_sdk.core.contracts import AgentEvent
from agent_sdk.state.jsonl_wal import JsonlWal


def test_wal_append_and_iter_events_order(tmp_path: Path) -> None:
    wal_path = tmp_path / "events.jsonl"
    wal = JsonlWal(wal_path)

    e1 = AgentEvent(type="run_started", timestamp="2026-02-05T00:00:00Z", run_id="r1", payload={"n": 1})
    e2 = AgentEvent(type="run_completed", timestamp="2026-02-05T00:00:01Z", run_id="r1", payload={"n": 2})

    i0 = wal.append(e1)
    i1 = wal.append(e2)

    assert i0 == 0
    assert i1 == 1
    assert i1 > i0

    events = list(wal.iter_events())
    assert events == [e1, e2]

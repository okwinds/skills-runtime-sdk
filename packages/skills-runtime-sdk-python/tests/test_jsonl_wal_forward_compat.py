from __future__ import annotations

import json
from pathlib import Path

from skills_runtime.state.jsonl_wal import JsonlWal


def test_jsonl_wal_iter_events_ignores_unknown_top_level_fields(tmp_path: Path) -> None:
    wal_path = tmp_path / "events.jsonl"

    obj = {
        "type": "run_started",
        "timestamp": "2026-02-05T00:00:00Z",
        "run_id": "r1",
        "payload": {"n": 1},
        # forward-compat: future writer may add new top-level fields
        "future_field": "ok",
    }
    wal_path.write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")

    wal = JsonlWal(wal_path)
    events = list(wal.iter_events())

    assert len(events) == 1
    assert events[0].type == "run_started"
    assert events[0].run_id == "r1"
    assert events[0].payload == {"n": 1}

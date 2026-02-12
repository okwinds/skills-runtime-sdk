from __future__ import annotations

import json

from agent_sdk.core.contracts import AgentEvent


def test_agent_event_json_roundtrip_fixed_ts() -> None:
    event = AgentEvent(
        type="run_started",
        ts="2026-02-05T00:00:00Z",
        run_id="run_001",
        payload={"task": "hello"},
    )

    raw = event.to_json()
    obj = json.loads(raw)

    # wire 字段名必须与 spec 一致（timestamp），避免前后端/回放口径漂移
    assert obj["type"] == "run_started"
    assert obj["timestamp"] == "2026-02-05T00:00:00Z"
    assert obj["run_id"] == "run_001"
    assert obj["payload"] == {"task": "hello"}
    assert "ts" not in obj

    event2 = AgentEvent.from_json(raw)
    assert event2 == event


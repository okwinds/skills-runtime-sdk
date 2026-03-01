from __future__ import annotations

import json
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.state.wal_protocol import InMemoryWal, WalBackend
from skills_runtime.tools.protocol import ToolCall


class _AlwaysApprove(ApprovalProvider):
    async def request_approval(self, *, request: ApprovalRequest, timeout_ms=None) -> ApprovalDecision:  # type: ignore[override]
        return ApprovalDecision.APPROVED


def test_in_memory_wal_append_and_iter_events_order() -> None:
    wal = InMemoryWal(locator_str="wal://in-memory/test")
    assert wal.locator() == "wal://in-memory/test"

    # 使用最小 AgentEvent 形状（避免依赖额外字段）。
    from skills_runtime.core.contracts import AgentEvent

    e1 = AgentEvent(type="run_started", timestamp="2026-02-05T00:00:00Z", run_id="r1", payload={"n": 1})
    e2 = AgentEvent(type="run_completed", timestamp="2026-02-05T00:00:01Z", run_id="r1", payload={"n": 2})

    i0 = wal.append(e1)
    i1 = wal.append(e2)

    assert i0 == 0
    assert i1 == 1
    assert list(wal.iter_events()) == [e1, e2]


def test_wal_iter_events_run_id_filter(tmp_path: Path) -> None:
    from skills_runtime.core.contracts import AgentEvent

    e1 = AgentEvent(type="run_started", timestamp="2026-02-05T00:00:00Z", run_id="r1", payload={"n": 1})
    e2 = AgentEvent(type="run_started", timestamp="2026-02-05T00:00:00Z", run_id="r2", payload={"n": 2})
    e3 = AgentEvent(type="run_completed", timestamp="2026-02-05T00:00:01Z", run_id="r1", payload={"n": 3})

    wal = InMemoryWal(locator_str="wal://in-memory/filter")
    wal.append(e1)
    wal.append(e2)
    wal.append(e3)

    assert list(wal.iter_events(run_id="r1")) == [e1, e3]
    assert list(wal.iter_events(run_id="r2")) == [e2]

    jsonl: WalBackend = JsonlWal(tmp_path / "events.jsonl")
    jsonl.append(e1)
    jsonl.append(e2)
    jsonl.append(e3)
    assert list(jsonl.iter_events(run_id="r1")) == [e1, e3]
    assert list(jsonl.iter_events(run_id="r2")) == [e2]


def test_jsonl_wal_satisfies_wal_backend(tmp_path: Path) -> None:
    wal: WalBackend = JsonlWal(tmp_path / "events.jsonl")
    assert isinstance(wal.locator(), str) and wal.locator()


def test_agent_run_with_in_memory_wal_does_not_write_events_jsonl(tmp_path: Path) -> None:
    args = {"path": "hello.txt", "content": "hi", "create_dirs": True}
    call = ToolCall(call_id="c1", name="file_write", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="tool_calls", tool_calls=[call], finish_reason="tool_calls"),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="done"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    wal = InMemoryWal(locator_str="wal://in-memory/test-run")
    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=tmp_path,
        approval_provider=_AlwaysApprove(),
        wal_backend=wal,
    )
    result = agent.run("write a file", run_id="r_inmem")

    # tool side effect still happens
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hi"

    # injected wal_backend: no local events.jsonl should be required
    events_jsonl_path = tmp_path / ".skills_runtime_sdk" / "runs" / "r_inmem" / "events.jsonl"
    assert not events_jsonl_path.exists()

    events = list(wal.iter_events())
    assert any(e.type == "run_started" for e in events)
    completed = [e for e in events if e.type == "run_completed"]
    assert completed, "expected run_completed to be written into injected WAL"
    assert completed[-1].payload.get("wal_locator") == "wal://in-memory/test-run#run_id=r_inmem"

    assert result.wal_locator == "wal://in-memory/test-run#run_id=r_inmem"

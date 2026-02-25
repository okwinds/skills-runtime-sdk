from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.tools.protocol import ToolSpec


class _BoomBackend:
    """
    用于回归：stream_chat 在开始阶段直接抛错，Agent 必须产生 run_failed 而不是线程异常退出。
    """

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[Any]:
        _ = request
        raise ValueError("boom")
        yield  # pragma: no cover


def test_agent_emits_run_failed_on_backend_exception(tmp_path: Path) -> None:
    agent = Agent(backend=_BoomBackend(), workspace_root=tmp_path, model="fake")
    result = agent.run("hi")

    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    assert any(e.type == "run_started" for e in events)
    assert any(e.type == "llm_request_started" for e in events)
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload["error_kind"] == "config_error"

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.errors import ContextLengthExceededError
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.tools.protocol import ToolSpec


class _LengthBackend:
    """
    用于回归：backend 在 streaming 过程中抛出 ContextLengthExceededError 时，Agent 必须分类为
    `context_length_exceeded`（而不是笼统的 config_error/unknown）。
    """

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[Any]:
        _ = request
        raise ContextLengthExceededError("context_length_exceeded")
        yield  # pragma: no cover


def test_agent_maps_context_length_exceeded_to_run_failed_kind(tmp_path: Path) -> None:
    agent = Agent(backend=_LengthBackend(), workspace_root=tmp_path, model="fake")
    result = agent.run("hi")

    assert result.status == "failed"
    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    failed = [e for e in events if e.type == "run_failed"]
    assert failed
    assert failed[-1].payload["error_kind"] == "context_length_exceeded"

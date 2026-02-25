from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from skills_runtime.core.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolSpec


class _StubBackend:
    def __init__(self) -> None:
        self.last_messages: Optional[List[Dict[str, Any]]] = None

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # type: ignore[override]
        self.last_messages = request.messages
        yield ChatStreamEvent(type="text_delta", text="ok")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def test_agent_can_start_without_repo_docs_by_using_assets_default_config(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # 关键：workspace_root 指向一个不包含 docs/specs 的空目录
    monkeypatch.chdir(tmp_path)

    agent = Agent(backend=_StubBackend(), workspace_root=tmp_path)
    events = list(agent.run_stream("hi"))
    assert events[0].type == "run_started"
    assert events[-1].type in ("run_completed", "run_failed", "run_cancelled")


def test_config_overlay_can_override_prompt_text(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "prompt:",
                '  system_text: \"SYS\"',
                '  developer_text: \"DEV\"',
                "  include_skills_list: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    backend = _StubBackend()
    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    events = list(agent.run_stream("hi"))

    req = next(e for e in events if e.type == "llm_request_started")
    assert int(req.payload["messages_count"]) == 2

    assert backend.last_messages is not None
    assert backend.last_messages[0]["role"] == "system"
    sys = str(backend.last_messages[0]["content"])
    assert "SYS" in sys
    assert "DEV" in sys

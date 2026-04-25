from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from skills_runtime.core.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolSpec


class _StubBackend:
    def __init__(self) -> None:
        self.last_messages: Optional[List[Dict[str, Any]]] = None
        self.last_tools: Optional[List[ToolSpec]] = None

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # type: ignore[override]
        self.last_messages = request.messages
        self.last_tools = request.tools
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


def test_generation_direct_profile_config_disables_agent_noise(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "prompt:",
                '  profile: "generation_direct"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    backend = _StubBackend()
    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    events = list(agent.run_stream("write a headline"))

    req = next(e for e in events if e.type == "llm_request_started")
    assert int(req.payload["tools_count"]) == 0
    assert backend.last_tools == []
    assert backend.last_messages is not None
    joined = "\n".join(str(m["content"]) for m in backend.last_messages)
    assert "general-purpose agent" not in backend.last_messages[0]["content"]
    assert "Follow a spec-driven + TDD workflow" not in joined
    assert "Available skills" not in joined


def test_structured_transform_profile_uses_builtin_transform_template(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)

    overlay = tmp_path / "overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "prompt:",
                '  profile: "structured_transform"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    backend = _StubBackend()
    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    events = list(agent.run_stream("extract fields"))

    req = next(e for e in events if e.type == "llm_request_started")
    assert int(req.payload["tools_count"]) == 0
    assert backend.last_tools == []
    assert backend.last_messages is not None
    joined = "\n".join(str(m["content"]) for m in backend.last_messages)
    assert "structured output" in backend.last_messages[0]["content"]
    assert "Follow a spec-driven + TDD workflow" not in joined
    assert "Available skills" not in joined


def test_builtin_prompt_profile_templates_are_packaged() -> None:
    base = files("skills_runtime.assets").joinpath("prompts")
    assert "directly complete" in base.joinpath("generation_direct").joinpath("system.md").read_text(encoding="utf-8").lower()
    assert base.joinpath("generation_direct").joinpath("developer.md").read_text(encoding="utf-8") == "\n"
    assert "structured output" in base.joinpath("structured_transform").joinpath("system.md").read_text(encoding="utf-8")
    assert base.joinpath("structured_transform").joinpath("developer.md").read_text(encoding="utf-8") == "\n"

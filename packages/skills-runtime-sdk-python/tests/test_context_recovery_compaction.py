from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.errors import ContextLengthExceededError
from agent_sdk.tools.protocol import HumanIOProvider, ToolSpec


class _CtxOnceThenOkBackend:
    def __init__(self) -> None:
        self._normal_calls = 0

    async def stream_chat(
        self,
        *,
        model: str,  # noqa: ARG002
        messages: List[Dict[str, Any]],  # noqa: ARG002
        tools: Optional[List[ToolSpec]] = None,
        temperature: Optional[float] = None,  # noqa: ARG002
    ) -> AsyncIterator[ChatStreamEvent]:
        if tools is None:
            yield ChatStreamEvent(type="text_delta", text="compacted summary")
            yield ChatStreamEvent(type="completed", finish_reason="stop")
            return

        if self._normal_calls == 0:
            self._normal_calls += 1
            raise ContextLengthExceededError("context_length_exceeded")

        yield ChatStreamEvent(type="text_delta", text="ok")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


class _AlwaysCtxBackend:
    async def stream_chat(
        self,
        *,
        model: str,  # noqa: ARG002
        messages: List[Dict[str, Any]],  # noqa: ARG002
        tools: Optional[List[ToolSpec]] = None,
        temperature: Optional[float] = None,  # noqa: ARG002
    ) -> AsyncIterator[ChatStreamEvent]:
        if tools is None:
            yield ChatStreamEvent(type="text_delta", text="handoff summary")
            yield ChatStreamEvent(type="completed", finish_reason="stop")
            return
        raise ContextLengthExceededError("context_length_exceeded")


class _FixedChoiceHuman(HumanIOProvider):
    def __init__(self, answer: str) -> None:
        self._answer = answer

    def request_human_input(
        self,
        *,
        call_id: str,  # noqa: ARG002
        question: str,  # noqa: ARG002
        choices: Optional[List[str]] = None,  # noqa: ARG002
        context: Optional[Dict[str, Any]] = None,  # noqa: ARG002
        timeout_ms: Optional[int] = None,  # noqa: ARG002
    ) -> str:
        return self._answer


def _write_overlay(tmp_path: Path, yaml_text: str) -> Path:
    p = tmp_path / "overlay.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return p


def test_context_recovery_compact_first_recovers_and_attaches_notice(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        """
run:
  context_recovery:
    mode: compact_first
    max_compactions_per_run: 2
""".lstrip(),
    )

    agent = Agent(workspace_root=tmp_path, backend=_CtxOnceThenOkBackend(), config_paths=[overlay])
    events = list(agent.run_stream("do something", run_id="run_test_compact_first"))

    terminal = [e for e in events if e.type == "run_completed"]
    assert len(terminal) == 1
    payload = terminal[0].payload
    assert payload.get("final_output") == "ok"
    md = payload.get("metadata") or {}
    notices = md.get("notices") or []
    assert any(isinstance(n, dict) and n.get("kind") == "context_compacted" and int(n.get("count") or 0) >= 1 for n in notices)
    artifacts = payload.get("artifacts") or []
    assert isinstance(artifacts, list)
    assert len(artifacts) >= 1


def test_context_recovery_ask_first_no_human_falls_back_to_compact_first(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        """
run:
  context_recovery:
    mode: ask_first
    ask_first_fallback_mode: compact_first
""".lstrip(),
    )

    agent = Agent(workspace_root=tmp_path, backend=_CtxOnceThenOkBackend(), config_paths=[overlay])
    events = list(agent.run_stream("do something", run_id="run_test_ask_first_fallback"))
    assert any(e.type == "context_length_exceeded" for e in events)
    assert any(e.type == "context_compacted" for e in events)
    assert any(e.type == "run_completed" and e.payload.get("final_output") == "ok" for e in events)


def test_context_recovery_ask_first_handoff_emits_completed_with_handoff_artifact(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        """
run:
  context_recovery:
    mode: ask_first
    max_compactions_per_run: 1
""".lstrip(),
    )

    agent = Agent(
        workspace_root=tmp_path,
        backend=_AlwaysCtxBackend(),
        config_paths=[overlay],
        human_io=_FixedChoiceHuman("handoff_new_run"),
    )
    events = list(agent.run_stream("do something", run_id="run_test_ask_first_handoff"))

    terminal = [e for e in events if e.type == "run_completed"]
    assert len(terminal) == 1
    payload = terminal[0].payload
    md = payload.get("metadata") or {}
    handoff = md.get("handoff") or {}
    assert isinstance(handoff, dict)
    assert isinstance(handoff.get("artifact_path"), str)


def test_context_recovery_fail_fast_keeps_existing_behavior(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        """
run:
  context_recovery:
    mode: fail_fast
""".lstrip(),
    )

    agent = Agent(workspace_root=tmp_path, backend=_AlwaysCtxBackend(), config_paths=[overlay])
    events = list(agent.run_stream("do something", run_id="run_test_fail_fast"))
    failed = [e for e in events if e.type == "run_failed"]
    assert len(failed) == 1
    assert failed[0].payload.get("error_kind") == "context_length_exceeded"


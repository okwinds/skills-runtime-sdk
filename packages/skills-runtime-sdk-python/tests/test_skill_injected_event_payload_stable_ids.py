from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from skills_runtime.agent import Agent
from skills_runtime.skills.mentions import SkillMention
from skills_runtime.skills.models import Skill
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.state.wal_protocol import InMemoryWal


def _make_agent_with_in_memory_skill(*, tmp_path: Path) -> Agent:
    namespace = "test"
    skill_name = "demo-skill"

    skills_config: Dict[str, Any] = {
        "spaces": [{"id": "space-test", "namespace": namespace, "sources": ["src-mem"], "enabled": True}],
        "sources": [{"id": "src-mem", "type": "in-memory", "options": {"namespace": namespace}}],
    }
    in_memory_registry = {
        namespace: [
            {
                "skill_name": skill_name,
                "description": "demo",
                "body": "# Demo Skill\n\nUse this skill for testing.\n",
            }
        ]
    }
    skills_manager = SkillsManager(
        workspace_root=tmp_path,
        skills_config=skills_config,
        in_memory_registry=in_memory_registry,
    )

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="ok"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            )
        ]
    )

    return Agent(
        model="fake-model",
        backend=backend,
        workspace_root=tmp_path,
        wal_backend=InMemoryWal(),
        skills_manager=skills_manager,
    )


def _collect_skill_injected_payloads(events) -> List[dict]:
    out: List[dict] = []
    for ev in events:
        if ev.type == "skill_injected":
            assert isinstance(ev.payload, dict)
            out.append(ev.payload)
    return out


class _StubSkillsManager:
    def __init__(self, *, skill: Skill, mention: SkillMention) -> None:
        self._skill = skill
        self._mention = mention

    def scan(self, *args: Any, **kwargs: Any) -> None:
        _ = args
        _ = kwargs
        return None

    def list_skills(self, *, enabled_only: bool = False) -> List[Skill]:
        _ = enabled_only
        return [self._skill]

    def resolve_mentions(self, text: str) -> List[Tuple[Skill, SkillMention]]:
        _ = text
        return [(self._skill, self._mention)]

    def render_injected_skill(self, skill: Skill, *, source: str, mention_text: Optional[str] = None) -> str:
        _ = skill
        _ = source
        _ = mention_text
        return "Injected Skill Body"


def test_skill_injected_payload_includes_stable_ids(tmp_path: Path) -> None:
    agent = _make_agent_with_in_memory_skill(tmp_path=tmp_path)
    events = list(agent.run_stream("Please use $[test].demo-skill to respond."))

    payloads = _collect_skill_injected_payloads(events)
    assert len(payloads) == 1
    payload = payloads[0]

    assert payload["mention_text"] == "$[test].demo-skill"
    assert payload["skill_name"] == "demo-skill"
    assert isinstance(payload["namespace"], str)
    assert isinstance(payload["skill_locator"], str)


def test_skill_injected_payload_is_json_serializable(tmp_path: Path) -> None:
    agent = _make_agent_with_in_memory_skill(tmp_path=tmp_path)
    events = list(agent.run_stream("Please use $[test].demo-skill to respond."))

    payloads = _collect_skill_injected_payloads(events)
    assert len(payloads) == 1
    payload = payloads[0]

    # MUST be JSON-serializable (no Path, bytes, etc.)
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


def test_skill_injected_missing_optional_ids_does_not_prevent_emission(tmp_path: Path) -> None:
    mention = SkillMention(
        namespace="test",
        segments=("test",),
        skill_name="demo-skill",
        mention_text="$[test].demo-skill",
    )
    skill = Skill(
        space_id="",
        source_id="",
        namespace="test",
        skill_name="demo-skill",
        description="demo",
        locator="mem://test/demo-skill",
        path=None,
        body_size=None,
        body_loader=lambda: "# Demo\n",
        required_env_vars=[],
        metadata={},
        scope="in-memory",
    )

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="ok"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            )
        ]
    )

    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=tmp_path,
        wal_backend=InMemoryWal(),
        skills_manager=_StubSkillsManager(skill=skill, mention=mention),  # type: ignore[arg-type]
    )

    events = list(agent.run_stream("Please use $[test].demo-skill to respond."))
    payloads = _collect_skill_injected_payloads(events)
    assert len(payloads) == 1
    payload = payloads[0]

    assert isinstance(payload["namespace"], str)
    assert isinstance(payload["skill_locator"], str)
    assert "space_id" not in payload
    assert "source_id" not in payload
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


def test_skill_injected_payload_does_not_leak_env_values(tmp_path: Path, monkeypatch) -> None:
    secret_value = "DO_NOT_LEAK_TEST_SECRET_VALUE__f2c7b9"
    monkeypatch.setenv("TEST_SECRET", secret_value)

    agent = _make_agent_with_in_memory_skill(tmp_path=tmp_path)
    events = list(agent.run_stream("Please use $[test].demo-skill to respond."))

    payloads = _collect_skill_injected_payloads(events)
    assert len(payloads) == 1
    payload = payloads[0]

    payload_str = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert secret_value not in payload_str

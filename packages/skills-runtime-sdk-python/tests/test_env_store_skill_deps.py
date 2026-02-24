from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest

from agent_sdk.core.agent import Agent
from agent_sdk.core.contracts import AgentEvent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.protocol import ChatRequest
from agent_sdk.skills.loader import load_skill_from_path
from agent_sdk.tools.protocol import ToolSpec


class _StubBackend:
    def __init__(self) -> None:
        self.last_messages: Optional[List[Dict[str, Any]]] = None

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        self.last_messages = request.messages
        yield ChatStreamEvent(type="text_delta", text=f"ok({request.model})")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


class _StubHumanIO:
    def __init__(self, answers: Dict[str, str]) -> None:
        self._answers = dict(answers)
        self.requests: List[Dict[str, Any]] = []

    def request_human_input(self, *, call_id: str, question: str, choices=None, context=None, timeout_ms=None) -> str:  # type: ignore[no-untyped-def]
        self.requests.append({"call_id": call_id, "question": question})
        # 约定：按 ENV_NAME key 提供答案
        for k, v in self._answers.items():
            if k in question:
                return v
        return ""


def _write_skill_with_env_dep(root: Path, *, name: str, env_name: str) -> Path:
    skill_dir = root / name
    (skill_dir / "agents").mkdir(parents=True, exist_ok=True)
    (skill_dir / "agents" / "openai.yaml").write_text(
        "\n".join(
            [
                "dependencies:",
                "  tools:",
                "    - type: env_var",
                f"      value: {env_name}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                "description: \"demo\"",
                "---",
                "",
                "# Body",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_md


def _write_skills_overlay(
    workspace_root: Path,
    *,
    skills_root: Path,
    env_var_missing_policy: Optional[str] = None,
    account: str = "demo",
    domain: str = "local",
    space_id: str = "space-demo",
    source_id: str = "src-fs",
) -> Path:
    """
    写入一个最小 skills overlay（显式 spaces/sources）。

    说明：
    - 本项目已弃用 `skills.roots` 与 `Agent(skills_roots=...)` 的 legacy 路径；
    - 测试必须使用显式 `skills.spaces/sources` 才能保证契约一致。
    """

    try:
        rel = skills_root.resolve().relative_to(workspace_root.resolve())
        root_value = str(rel)
    except Exception:
        root_value = str(skills_root.resolve())

    overlay = workspace_root / "skills_overlay.yaml"
    policy_line = (
        f"  env_var_missing_policy: \"{str(env_var_missing_policy)}\"" if env_var_missing_policy is not None else None
    )
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "skills:",
                *( [policy_line] if policy_line else [] ),
                "  spaces:",
                f"    - id: \"{space_id}\"",
                f"      account: \"{account}\"",
                f"      domain: \"{domain}\"",
                f"      sources: [\"{source_id}\"]",
                "  sources:",
                f"    - id: \"{source_id}\"",
                "      type: \"filesystem\"",
                "      options:",
                f"        root: {root_value}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return overlay


def _events_types(events: List[AgentEvent]) -> List[str]:
    return [e.type for e in events]


def test_skill_loader_parses_required_env_vars(tmp_path: Path) -> None:
    skill_md = _write_skill_with_env_dep(tmp_path, name="dep-skill", env_name="FOO_TOKEN")
    skill = load_skill_from_path(skill_md)
    assert skill.required_env_vars == ["FOO_TOKEN"]


def test_env_var_required_prompts_human_and_does_not_log_value(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="FOO_TOKEN")
    overlay = _write_skills_overlay(tmp_path, skills_root=skills_root)

    secret_value = "super-secret-token-123"
    human = _StubHumanIO({"FOO_TOKEN": secret_value})
    backend = _StubBackend()

    agent = Agent(
        backend=backend,
        workspace_root=tmp_path,
        config_paths=[overlay],
        human_io=human,
        env_vars={},  # session-only env_store
    )

    events = list(agent.run_stream("please use $[demo:local].dep-skill"))
    assert "env_var_required" in _events_types(events)
    assert "env_var_set" in _events_types(events)

    # 事件中不得出现 value
    raw = "\n".join(e.to_json() for e in events)
    assert secret_value not in raw

    # human 收集应发生
    assert human.requests
    assert any("FOO_TOKEN" in r["question"] for r in human.requests)


def test_env_var_present_in_process_env_skips_human(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="BAR_TOKEN")
    overlay = _write_skills_overlay(tmp_path, skills_root=skills_root)

    monkeypatch.setenv("BAR_TOKEN", "from-process")
    backend = _StubBackend()
    human = _StubHumanIO({"BAR_TOKEN": "should-not-be-used"})

    agent = Agent(
        backend=backend,
        workspace_root=tmp_path,
        config_paths=[overlay],
        human_io=human,
        env_vars={},
    )
    events = list(agent.run_stream("please use $[demo:local].dep-skill"))
    assert "env_var_required" not in _events_types(events)
    # 应记录 env_var_set（来源 process_env）或不记录均可；此处只要求不 prompt
    assert human.requests == []


def test_env_var_missing_without_human_io_fails_as_config_error(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="NEEDED")
    overlay = _write_skills_overlay(tmp_path, skills_root=skills_root)

    backend = _StubBackend()
    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    events = list(agent.run_stream("please use $[demo:local].dep-skill"))
    last = events[-1]
    assert last.type == "run_failed"
    assert last.payload.get("error_kind") == "config_error"


def test_env_var_missing_policy_fail_fast_does_not_prompt(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="FOO_TOKEN")
    overlay = _write_skills_overlay(tmp_path, skills_root=skills_root, env_var_missing_policy="fail_fast")

    backend = _StubBackend()
    human = _StubHumanIO({"FOO_TOKEN": "should-not-be-used"})
    agent = Agent(
        backend=backend,
        workspace_root=tmp_path,
        config_paths=[overlay],
        human_io=human,
        env_vars={},
    )

    events = list(agent.run_stream("please use $[demo:local].dep-skill"))
    assert "env_var_required" in _events_types(events)
    assert "human_request" not in _events_types(events)
    assert human.requests == []
    assert events[-1].type == "run_failed"
    assert events[-1].payload.get("error_kind") == "missing_env_var"
    details = events[-1].payload.get("details") or {}
    assert details.get("missing_env_vars") == ["FOO_TOKEN"]


def test_env_var_missing_policy_skip_skill_skips_injection_without_prompt(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="FOO_TOKEN")
    overlay = _write_skills_overlay(tmp_path, skills_root=skills_root, env_var_missing_policy="skip_skill")

    backend = _StubBackend()
    human = _StubHumanIO({"FOO_TOKEN": "should-not-be-used"})
    agent = Agent(
        backend=backend,
        workspace_root=tmp_path,
        config_paths=[overlay],
        human_io=human,
        env_vars={},
    )

    events = list(agent.run_stream("please use $[demo:local].dep-skill"))
    types = _events_types(events)
    assert "env_var_required" in types
    assert "skill_injection_skipped" in types
    assert "skill_injected" not in types
    assert "human_request" not in types
    assert human.requests == []
    assert events[-1].type == "run_completed"


def test_env_store_persists_across_runs_when_dict_is_shared(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="PERSIST")
    overlay = _write_skills_overlay(tmp_path, skills_root=skills_root)

    env_store: Dict[str, str] = {}
    human = _StubHumanIO({"PERSIST": "v1"})
    backend = _StubBackend()
    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay], human_io=human, env_vars=env_store)

    list(agent.run_stream("use $[demo:local].dep-skill"))
    assert env_store.get("PERSIST") == "v1"

    # 第二次不应再 prompt（使用同一个 env_store）
    human2 = _StubHumanIO({"PERSIST": "v2"})
    agent2 = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay], human_io=human2, env_vars=env_store)
    list(agent2.run_stream("use $[demo:local].dep-skill"))
    assert human2.requests == []


def test_env_var_required_and_set_payload_shape(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="SHAPE")
    overlay = _write_skills_overlay(tmp_path, skills_root=skills_root)

    human = _StubHumanIO({"SHAPE": "x"})
    backend = _StubBackend()
    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay], human_io=human, env_vars={})
    events = list(agent.run_stream("use $[demo:local].dep-skill"))
    required = [e for e in events if e.type == "env_var_required"]
    set_ = [e for e in events if e.type == "env_var_set"]
    assert required and set_
    assert required[0].payload["env_var"] == "SHAPE"
    assert required[0].payload["source"] == "skill_dependency"
    assert set_[0].payload["env_var"] == "SHAPE"
    assert set_[0].payload["value_source"] in ("human", "process_env", "provided")


def test_tool_output_is_redacted_from_env_store_values(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # 这里用一个 custom tool 返回 secret，并验证 tool_call_finished.result 不包含 secret
    monkeypatch.chdir(tmp_path)

    secret = "S3CR3T-XYZ"
    from agent_sdk.tools.protocol import ToolCall

    class _ToolCallingBackend:
        def __init__(self) -> None:
            self._called = 0

        async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # type: ignore[override]
            _ = request
            self._called += 1
            if self._called == 1:
                yield ChatStreamEvent(
                    type="tool_calls",
                    tool_calls=[ToolCall(call_id="c1", name="echo_secret", args={}, raw_arguments="{}")],
                    finish_reason="tool_calls",
                )
                yield ChatStreamEvent(type="completed", finish_reason="tool_calls")
                return

            # 第二次调用：不给 tool_calls，直接完成，让 Agent 结束 run
            yield ChatStreamEvent(type="text_delta", text="done")
            yield ChatStreamEvent(type="completed", finish_reason="stop")

    agent = Agent(backend=_ToolCallingBackend(), workspace_root=tmp_path, env_vars={"ANY": secret})

    @agent.tool(name="echo_secret", description="return a secret")
    def echo_secret() -> str:  # type: ignore[no-untyped-def]
        return secret

    events = list(agent.run_stream("call echo_secret tool"))
    raw = "\n".join(e.to_json() for e in events)
    assert secret not in raw


@pytest.mark.parametrize("name", ["PATH", "HOME", "USER"])
def test_env_like_skill_mentions_are_not_required_for_common_env_vars(tmp_path: Path, monkeypatch, name: str) -> None:  # type: ignore[no-untyped-def]
    # 回归：技能 mention 解析不应误把 $PATH 等当作 skill 注入；这里保证“即使文本里出现 $PATH 也不会触发 env_var_required”
    monkeypatch.chdir(tmp_path)
    backend = _StubBackend()
    agent = Agent(backend=backend, workspace_root=tmp_path)
    events = list(agent.run_stream(f"just mention ${name}"))
    assert "env_var_required" not in _events_types(events)

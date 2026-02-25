from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills_runtime import AgentBuilder
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.state.wal_protocol import InMemoryWal
from skills_runtime.tools.protocol import ToolCall


def _write_skills_overlay(workspace_root: Path, *, skills_root: Path) -> Path:
    overlay = workspace_root / "skills_overlay.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "skills:",
                "  spaces:",
                "    - id: \"space-demo\"",
                "      account: \"demo\"",
                "      domain: \"local\"",
                "      sources: [\"src-fs\"]",
                "  sources:",
                "    - id: \"src-fs\"",
                "      type: \"filesystem\"",
                "      options:",
                f"        root: {str(skills_root.resolve())}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return overlay


def _write_skill_with_env_dep(root: Path, *, name: str, env_name: str) -> None:
    skill_dir = root / name
    (skill_dir / "agents").mkdir(parents=True, exist_ok=True)
    (skill_dir / "agents" / "openai.yaml").write_text(
        "\n".join(["dependencies:", "  tools:", "    - type: env_var", f"      value: {env_name}", ""]),
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "\n".join(["---", f"name: {name}", "description: \"demo\"", "---", "", "# Body", "", ""]),
        encoding="utf-8",
    )


def test_agent_builder_builds_functional_agent(tmp_path: Path) -> None:
    backend = FakeChatBackend(
        calls=[FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")])]
    )
    agent = AgentBuilder().workspace_root(tmp_path).backend(backend).model("fake-model").build()
    result = agent.run("hi")
    assert result.status == "completed"
    assert result.final_output == "ok"


def test_agent_builder_injects_wal_backend_and_event_hooks(tmp_path: Path) -> None:
    backend = FakeChatBackend(
        calls=[FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")])]
    )
    wal = InMemoryWal()
    hook_types: List[str] = []

    def _hook(ev: AgentEvent) -> None:
        hook_types.append(ev.type)

    agent = (
        AgentBuilder()
        .workspace_root(tmp_path)
        .backend(backend)
        .model("fake-model")
        .wal_backend(wal)
        .event_hooks([_hook])
        .build()
    )

    stream_events = list(agent.run_stream("hi"))
    stream_types = [e.type for e in stream_events]
    assert hook_types == stream_types
    assert any(e.type == "run_completed" for e in stream_events)
    assert any(e.type == "run_started" for e in wal.iter_events())


def test_cloud_unattended_preset_denies_approval_without_human_interaction(tmp_path: Path) -> None:
    args = {"argv": ["/bin/echo", "hi"]}
    call = ToolCall(call_id="c1", name="shell_exec", args=args, raw_arguments=json.dumps(args, ensure_ascii=False))
    backend = FakeChatBackend(
        calls=[
            FakeChatCall(events=[ChatStreamEvent(type="tool_calls", tool_calls=[call]), ChatStreamEvent(type="completed", finish_reason="tool_calls")]),
            FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")]),
        ]
    )
    wal = InMemoryWal()
    agent = AgentBuilder.cloud_unattended_preset(workspace_root=tmp_path, backend=backend, wal_backend=wal).build()

    events = list(agent.run_stream("try shell"))
    assert any(e.type == "run_completed" for e in events)
    assert not any(e.type == "human_request" for e in events)
    finished = [e for e in events if e.type == "tool_call_finished"]
    assert finished and finished[-1].payload.get("result", {}).get("error_kind") == "permission"


def test_cloud_unattended_preset_fail_fast_env_var_missing(tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    _write_skill_with_env_dep(skills_root, name="dep-skill", env_name="FOO_TOKEN")
    skills_overlay = _write_skills_overlay(tmp_path, skills_root=skills_root)

    backend = FakeChatBackend(
        calls=[FakeChatCall(events=[ChatStreamEvent(type="text_delta", text="ok"), ChatStreamEvent(type="completed", finish_reason="stop")])]
    )
    wal = InMemoryWal()

    agent = (
        AgentBuilder.cloud_unattended_preset(workspace_root=tmp_path, backend=backend, wal_backend=wal)
        .add_config_path(skills_overlay)
        .build()
    )

    events = list(agent.run_stream("please use $[demo:local].dep-skill"))
    assert any(e.type == "env_var_required" for e in events)
    assert any(e.type == "run_failed" for e in events)
    assert not any(e.type == "human_request" for e in events)


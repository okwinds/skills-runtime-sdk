from __future__ import annotations

from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.state.jsonl_wal import JsonlWal


def test_agent_config_paths_are_overlays_on_default(tmp_path: Path) -> None:
    overlay = tmp_path / "runtime.yaml"
    overlay.write_text(
        "\n".join(
            [
                "config_version: 1",
                "llm:",
                "  base_url: http://example.test/v1",
                "  api_key_env: OPENAI_API_KEY",
                "models:",
                "  planner: planner-override",
                "  executor: executor-override",
                "",
            ]
        ),
        encoding="utf-8",
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

    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay], profile_id="profile-test")
    result = agent.run("hi", initial_history=[{"role": "user", "content": "prev"}])

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    started = next(e for e in events if e.type == "run_started")
    models = started.payload["config_summary"]["models"]
    assert models["planner"] == "planner-override"
    assert models["executor"] == "executor-override"
    assert started.payload["config_summary"]["profile_id"] == "profile-test"
    llm = started.payload["config_summary"]["llm"]
    assert llm["base_url"] == "http://example.test/v1"
    assert llm["api_key_env"] == "OPENAI_API_KEY"


def test_agent_supports_multiple_profiles_with_different_models(tmp_path: Path) -> None:
    backend_a = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="ok-a"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            )
        ]
    )
    backend_b = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="ok-b"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            )
        ]
    )

    agent_a = Agent(
        backend=backend_a,
        workspace_root=tmp_path,
        profile_id="profile-a",
        planner_model="planner-a",
        executor_model="executor-a",
    )
    agent_b = Agent(
        backend=backend_b,
        workspace_root=tmp_path,
        profile_id="profile-b",
        planner_model="planner-b",
        executor_model="executor-b",
    )

    result_a = agent_a.run("hi-a")
    result_b = agent_b.run("hi-b")

    events_a = list(JsonlWal(Path(result_a.wal_locator)).iter_events())
    started_a = next(e for e in events_a if e.type == "run_started")
    events_b = list(JsonlWal(Path(result_b.wal_locator)).iter_events())
    started_b = next(e for e in events_b if e.type == "run_started")

    assert started_a.payload["config_summary"]["profile_id"] == "profile-a"
    assert started_b.payload["config_summary"]["profile_id"] == "profile-b"

    assert started_a.payload["config_summary"]["models"] != started_b.payload["config_summary"]["models"]
    assert started_a.payload["config_summary"]["models"] == {"planner": "planner-a", "executor": "executor-a"}
    assert started_b.payload["config_summary"]["models"] == {"planner": "planner-b", "executor": "executor-b"}

from __future__ import annotations

from pathlib import Path

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.state.jsonl_wal import JsonlWal


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

    agent = Agent(backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    result = agent.run("hi", initial_history=[{"role": "user", "content": "prev"}])

    events = list(JsonlWal(Path(result.wal_locator)).iter_events())
    started = next(e for e in events if e.type == "run_started")
    models = started.payload["config_summary"]["models"]
    assert models["planner"] == "planner-override"
    assert models["executor"] == "executor-override"
    llm = started.payload["config_summary"]["llm"]
    assert llm["base_url"] == "http://example.test/v1"
    assert llm["api_key_env"] == "OPENAI_API_KEY"

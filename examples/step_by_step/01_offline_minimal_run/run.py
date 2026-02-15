"""
离线最小 run 示例（FakeChatBackend）。

用途：
- 在不依赖真实模型/外网的情况下，演示 Agent.run 的最小闭环；
- 展示 events_path（WAL）落盘位置，便于排障与回放。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall


def main() -> int:
    """脚本入口：离线最小 run。"""

    parser = argparse.ArgumentParser(description="01_offline_minimal_run (offline)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text("run:\n  max_steps: 3\n", encoding="utf-8")

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="你好，我是离线示例。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            )
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=workspace_root, config_paths=[overlay])
    r = agent.run("请用一句话自我介绍。", run_id="run_example_step_01")

    print(f"[example] status={r.status}")
    print(f"[example] events_path={r.events_path}")
    print("[example] final_output:")
    print(r.final_output)
    print("EXAMPLE_OK: step_by_step_01")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


"""
离线 tool_calls 示例：read_file。

用途：
- 演示 Agent Loop 在收到 tool_calls 后如何执行工具并继续下一轮模型调用；
- 本示例使用 FakeChatBackend，保证离线可回归。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from skills_runtime.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.tools.protocol import ToolCall


def main() -> int:
    """脚本入口：离线 tool_calls（read_file）。"""

    parser = argparse.ArgumentParser(description="02_offline_tool_call_read_file (offline tool_calls)")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text("run:\n  max_steps: 6\n", encoding="utf-8")

    # 准备一个可读文件（工具参数会限制必须在 workspace_root 内）
    target = workspace_root / "hello.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    backend = FakeChatBackend(
        calls=[
            # 第 1 次：模型要求读取文件
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[
                            ToolCall(
                                call_id="tc_read_1",
                                name="read_file",
                                args={"file_path": "hello.txt", "offset": 1, "limit": 5},
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            # 第 2 次：模型基于 tool output 给出最终文本
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="已读取文件并完成任务。"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    agent = Agent(model="fake-model", backend=backend, workspace_root=workspace_root, config_paths=[overlay])
    r = agent.run("请读取 hello.txt 并总结。", run_id="run_example_step_02")

    print(f"[example] status={r.status}")
    print(f"[example] wal_locator={r.wal_locator}")
    print("[example] final_output:")
    print(r.final_output)
    print("EXAMPLE_OK: step_by_step_02")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

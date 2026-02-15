"""
WAL replay + fork 示例（离线、可回归）。

用途：
- 展示 events.jsonl（WAL）落盘路径；
- 展示 fork_run 的最小使用方式；
- 用 replay resume 验证 fork 后的 history 能包含 tool message。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from agent_sdk import Agent
from agent_sdk.llm.chat_sse import ChatStreamEvent
from agent_sdk.llm.fake import FakeChatBackend, FakeChatCall
from agent_sdk.state.fork import fork_run
from agent_sdk.tools.protocol import ToolCall


class _CheckHasToolMessageBackend:
    """仅用于演示：断言 replay history 中能看到 tool message（tool_call_id 匹配）。"""

    def __init__(self, *, expected_tool_call_id: str) -> None:
        self._expected_tool_call_id = expected_tool_call_id

    async def stream_chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        found = any(m.get("role") == "tool" and m.get("tool_call_id") == self._expected_tool_call_id for m in messages)
        if not found:
            raise AssertionError("expected tool message in replayed history")
        yield ChatStreamEvent(type="text_delta", text="ok(replayed)")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def main() -> int:
    """脚本入口：WAL replay + fork。"""

    parser = argparse.ArgumentParser(description="01_wal_replay_and_fork")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    src_run_id = "run_example_state_src"
    dst_run_id = "run_example_state_forked"

    overlay = workspace_root / "runtime.yaml"
    overlay.write_text("run:\n  resume_strategy: replay\n", encoding="utf-8")

    backend = FakeChatBackend(
        calls=[
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[ToolCall(call_id="tc_list_1", name="list_dir", args={"dir_path": "."})],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            ),
            FakeChatCall(
                events=[
                    ChatStreamEvent(type="text_delta", text="done"),
                    ChatStreamEvent(type="completed", finish_reason="stop"),
                ]
            ),
        ]
    )

    Agent(model="fake-model", backend=backend, workspace_root=workspace_root, config_paths=[overlay]).run(
        "列出当前目录。", run_id=src_run_id
    )

    src_events = workspace_root / ".skills_runtime_sdk" / "runs" / src_run_id / "events.jsonl"
    print("[example] src_events_path:", src_events)
    if not src_events.exists():
        raise AssertionError("events.jsonl not found")

    tool_finished_idx = None
    for idx, raw in enumerate(src_events.read_text(encoding="utf-8").splitlines()):
        obj = json.loads(raw)
        if obj.get("type") == "tool_call_finished":
            tool_finished_idx = idx
            break
    if tool_finished_idx is None:
        raise AssertionError("tool_call_finished not found in WAL")

    fork_run(
        workspace_root=workspace_root,
        src_run_id=src_run_id,
        dst_run_id=dst_run_id,
        up_to_index_inclusive=int(tool_finished_idx),
    )

    backend2 = _CheckHasToolMessageBackend(expected_tool_call_id="tc_list_1")
    r = Agent(model="fake-model", backend=backend2, workspace_root=workspace_root, config_paths=[overlay]).run(
        "继续（replay）。", run_id=dst_run_id
    )

    print("[example] forked.status:", r.status)
    print("[example] forked.final_output:", r.final_output)
    print("EXAMPLE_OK: state_wal_replay_fork")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


"""
ToolRegistry + read_file 示例（离线、无 LLM）。

用途：
- 展示 tool 协议：ToolCall -> ToolResult；
- 展示 workspace_root 路径限制（read_file 只能访问 workspace 内）。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from agent_sdk.tools.builtin import register_builtin_tools
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry


def main() -> int:
    """脚本入口：直接 dispatch read_file。"""

    parser = argparse.ArgumentParser(description="01_standard_library_read_file")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)

    f = workspace_root / "hello.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8")

    call = ToolCall(call_id="tc1", name="read_file", args={"file_path": "hello.txt", "offset": 1, "limit": 20})
    ctx = ToolExecutionContext(workspace_root=workspace_root, run_id="run_tools_demo")
    registry = ToolRegistry(ctx=ctx)
    register_builtin_tools(registry)
    result = registry.dispatch(call)

    assert result.ok is True, result.content
    print("[example] tool_result.content:")
    print(result.content)
    print("EXAMPLE_OK: tools_read_file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

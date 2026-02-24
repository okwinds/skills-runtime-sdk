"""
SDK 最小运行示例。

用途：
- 演示如何用 overlay 配置构造 Agent；
- 演示如何消费 run_stream 事件；
- 演示如何拿到 final_output 与 wal_locator。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, List

import yaml

from agent_sdk import Agent
from agent_sdk.config.defaults import load_default_config_dict
from agent_sdk.config.loader import load_config_dicts
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend


def _load_merged_config(config_paths: List[Path]) -> Any:
    """
    加载默认配置与 overlay，并返回合并后的配置对象。

    参数：
    - config_paths：overlay 配置路径列表（后者覆盖前者）。

    返回：
    - 合并后的 `AgentSdkConfig`。
    """

    overlays: List[Dict[str, Any]] = [load_default_config_dict()]
    for path in config_paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            overlays.append(data)
    return load_config_dicts(overlays)


def _build_agent(*, workspace_root: Path, config_paths: List[Path]) -> Agent:
    """
    基于 workspace 与 overlay 构建 Agent。

    参数：
    - workspace_root：工作区根目录。
    - config_paths：overlay 配置路径列表。

    返回：
    - 可运行的 Agent 实例。
    """

    merged = _load_merged_config(config_paths)
    backend = OpenAIChatCompletionsBackend(merged.llm)

    return Agent(
        workspace_root=workspace_root,
        backend=backend,
        config_paths=config_paths,
    )


def main() -> int:
    """
    示例脚本入口。

    命令行参数：
    - --workspace-root：工作区目录；
    - --config：overlay 路径（可重复）；
    - --message：任务文本。
    """

    parser = argparse.ArgumentParser(description="Run minimal Agent demo")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--config", action="append", default=[], help="Overlay YAML path (repeatable)")
    parser.add_argument("--message", default="请简要说明当前仓库的核心模块。", help="Task message")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    config_paths = [Path(p).expanduser().resolve() for p in (args.config or [])]

    agent = _build_agent(workspace_root=workspace_root, config_paths=config_paths)

    final_output = ""
    wal_locator = ""
    print(f"[demo] workspace_root={workspace_root}")
    for event in agent.run_stream(args.message):
        print(f"[event] {event.type}")
        if event.type == "run_completed":
            final_output = str(event.payload.get("final_output") or "")
            wal_locator = str(event.payload.get("wal_locator") or "")

    print("\n[demo] final_output:\n")
    print(final_output)
    print(f"\n[demo] wal_locator={wal_locator}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

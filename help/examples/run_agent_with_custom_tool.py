"""
SDK 自定义 tool 示例。

用途：
- 演示 `Agent.tool` decorator 注册自定义工具；
- 演示模型在 run 过程中调用自定义工具。
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
    """加载默认配置与 overlay，返回合并后的配置对象。"""

    overlays: List[Dict[str, Any]] = [load_default_config_dict()]
    for path in config_paths:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            overlays.append(data)
    return load_config_dicts(overlays)


def _build_agent(*, workspace_root: Path, config_paths: List[Path]) -> Agent:
    """构建 Agent。"""

    merged = _load_merged_config(config_paths)
    backend = OpenAIChatCompletionsBackend(merged.llm)

    return Agent(
        workspace_root=workspace_root,
        backend=backend,
        config_paths=config_paths,
    )


def main() -> int:
    """脚本入口。"""

    parser = argparse.ArgumentParser(description="Run Agent with custom tool demo")
    parser.add_argument("--workspace-root", default=".", help="Workspace root path")
    parser.add_argument("--config", action="append", default=[], help="Overlay YAML path (repeatable)")
    parser.add_argument("--message", default="请调用 add_numbers 计算 13 + 29。", help="Task message")
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    config_paths = [Path(p).expanduser().resolve() for p in (args.config or [])]

    agent = _build_agent(workspace_root=workspace_root, config_paths=config_paths)

    @agent.tool(name="add_numbers", description="计算两个整数并返回和")
    def add_numbers(a: int, b: int) -> int:
        """计算两个整数之和。"""

        return a + b

    result = agent.run(args.message)
    print(f"status={result.status}")
    print(f"wal_locator={result.wal_locator}")
    print("final_output:")
    print(result.final_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

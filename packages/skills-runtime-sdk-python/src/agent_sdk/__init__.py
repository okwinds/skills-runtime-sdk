"""
Skills Runtime SDK（Python）。

说明：
- 本包为 Skills Runtime 的 Python 参考实现（持续增量交付）。
- 上层使用手册与可运行示例：`help/`（推荐从 `help/README.md` 开始）。
- 当前已包含（M4，持续增量）：
  - 核心契约（AgentEvent/AgentError）
  - 状态 WAL（JSONL append-only）
  - 配置加载器（YAML overlay + pydantic 校验）
  - Executor（命令执行 + timeout + stdout/stderr 截断）
  - Tool System（ToolSpec/ToolCall/ToolResult、ToolRegistry、Phase 2 内置 tools）
  - LLM backend（Chat SSE parser + OpenAI-compatible 骨架 + Fake backend）
  - Skills（扫描/mention 解析/注入渲染）
  - PromptManager（模板/固定顺序/历史滑窗）
  - Agent（最小 loop：tool_calls→执行→回注→完成）
"""

from __future__ import annotations

from agent_sdk.core.agent import Agent, RunResult
from agent_sdk.core.agent_builder import AgentBuilder
from agent_sdk.core.coordinator import ChildResult, Coordinator

__all__ = ["Agent", "AgentBuilder", "ChildResult", "Coordinator", "RunResult", "__version__"]

__version__ = "1.0.4.post1"

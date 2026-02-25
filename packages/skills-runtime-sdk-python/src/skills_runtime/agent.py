"""
对外稳定入口：Agent。

说明：
- 推荐用法：`from skills_runtime.agent import Agent`
- 实现位于 `skills_runtime.core.agent.Agent`；此模块仅提供稳定 import 路径与类型重导出。
"""

from __future__ import annotations

from skills_runtime.core.agent import Agent, RunResult

__all__ = ["Agent", "RunResult"]


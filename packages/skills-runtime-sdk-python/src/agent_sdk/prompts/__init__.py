"""
Prompt 管理（模板、上下文、历史滑窗）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/prompt-manager.md`
"""

from __future__ import annotations

from agent_sdk.prompts.history import trim_history
from agent_sdk.prompts.manager import PromptManager, PromptTemplates

__all__ = ["PromptManager", "PromptTemplates", "trim_history"]


"""
⚠️ BREAKING：此命名空间已废弃并被硬移除。

本仓库的 Python SDK 已将 import 根命名空间从 `agent_sdk` 暴力升级为 `skills_runtime`。

说明：
- 这是一个“墓碑模块（tombstone）”，目的仅是让旧 import 立刻失败并给出可操作的迁移提示；
- 不提供任何兼容转发（不导出任何旧符号），避免历史包袱与文档漂移源。
"""

from __future__ import annotations

raise ModuleNotFoundError(
    "The Python import name has been renamed from 'agent_sdk' to 'skills_runtime'. "
    "Update your code to: `from skills_runtime.agent import Agent`."
)


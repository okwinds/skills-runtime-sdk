"""Tool System（协议 + 注册表 + 内置工具）。"""

from __future__ import annotations

from skills_runtime.tools.protocol import HumanIOProvider, ToolCall, ToolResult, ToolResultPayload, ToolSpec

__all__ = [
    "protocol",
    "registry",
    "HumanIOProvider",
    "ToolCall",
    "ToolResult",
    "ToolResultPayload",
    "ToolSpec",
]

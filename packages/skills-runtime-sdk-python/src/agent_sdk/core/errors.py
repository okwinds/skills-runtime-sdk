"""
SDK 内部错误分类（异常类型）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/core-contracts.md` §6（错误分类）

说明：
- Phase 2 只实现最小异常类型集合，便于在模块间传递“错误层级”语义。
- 对外工具返回建议使用 `ToolResult.error_kind`，异常仅用于内部控制流与测试断言。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


class AgentSdkError(Exception):
    """SDK 内部错误基类（不建议直接抛出）。"""


@dataclass(frozen=True)
class FrameworkIssue:
    """框架结构化问题对象（可用于 scan 报告中的 errors/warnings）。"""

    code: str
    message: str
    details: Dict[str, Any]


class FrameworkError(AgentSdkError):
    """框架层结构化错误（英文 `code/message/details`）。"""

    def __init__(self, *, code: str, message: str, details: Dict[str, Any] | None = None) -> None:
        """创建框架错误。

        参数：
        - `code`：稳定错误码（英文大写下划线）
        - `message`：英文错误消息
        - `details`：结构化上下文信息
        """

        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Dict[str, Any] = details or {}

    def __str__(self) -> str:
        """返回用于日志的字符串表示。"""

        return f"{self.code}: {self.message}"

    def to_issue(self) -> FrameworkIssue:
        """把异常转换为可序列化问题对象。"""

        return FrameworkIssue(code=self.code, message=self.message, details=dict(self.details))


class UserError(FrameworkError):
    """用户输入/配置导致的错误（保留兼容；本质属于框架错误）。"""

    def __init__(self, message: str, *, code: str = "USER_ERROR", details: Dict[str, Any] | None = None) -> None:
        """创建 `UserError`。

        参数：
        - `message`：可读错误信息
        - `code`：错误码（默认 `USER_ERROR`）
        - `details`：结构化补充信息
        """

        super().__init__(code=code, message=message, details=details or {})


class ToolError(AgentSdkError):
    """工具执行失败（timeout、exit_code、sandbox_denied 等）。"""


class StateError(AgentSdkError):
    """状态持久化/恢复错误（WAL 读写、回放失败等）。"""


class LlmError(AgentSdkError):
    """LLM 通信/协议错误（网络、限流、wire 解析等）。"""

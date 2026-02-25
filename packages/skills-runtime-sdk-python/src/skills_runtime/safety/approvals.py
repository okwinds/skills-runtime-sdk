"""
Approvals（人工审批）协议与工具函数（Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/safety.md` §4（ApprovalProvider）
- `docs/specs/skills-runtime-sdk/docs/core-contracts.md` §5（ApprovalKey）
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ApprovalDecision(str, Enum):
    """审批决策枚举（最小集合）。"""

    APPROVED = "approved"
    APPROVED_FOR_SESSION = "approved_for_session"
    DENIED = "denied"
    ABORT = "abort"


class ApprovalRequest(BaseModel):
    """
    审批请求（面向 UI/人类）。

    字段：
    - approval_key：稳定 key（用于 session 级缓存）
    - tool：工具名（shell_exec/file_write/...）
    - summary：人类可读摘要（不得包含密钥）
    - details：结构化详情（argv/path 等）
    """

    model_config = ConfigDict(extra="forbid")

    approval_key: str
    tool: str
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class ApprovalProvider(Protocol):
    """
    审批适配层（SDK 不直接读 stdin/弹窗）。

    Phase 2 最小接口：
    - request_approval：请求用户做出审批决策
    """

    async def request_approval(
        self,
        *,
        request: ApprovalRequest,
        timeout_ms: Optional[int] = None,
    ) -> ApprovalDecision:
        """
        请求用户对一次“危险操作”做出审批决策。

        约束：
        - `request.summary/details` 不得包含 secrets 明文；
        - `timeout_ms` 为 None 表示由实现自行决定等待策略（Web UI 常见为“无限等待直到用户点击”）。
        """

        ...


def compute_approval_key(*, tool: str, request: Dict[str, Any]) -> str:
    """
    计算 approval_key（canonical JSON sha256）。

    参数：
    - tool：工具名
    - request：工具请求的可审计表示（建议仅含稳定字段）
    """

    canonical = {"tool": tool, "request": request}
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

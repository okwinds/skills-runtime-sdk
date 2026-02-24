"""
核心契约（Core Contracts）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/core-contracts.md`（AgentEvent 公共字段定义）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentError(BaseModel):
    """
    统一错误结构（可放入事件 payload 或作为返回/异常信息载体）。

    字段语义：
    - kind：错误分类（用于程序化处理/统计）
    - message：面向开发者的错误说明（避免包含密钥）
    - retryable：是否建议重试
    - cause：可选的底层原因（建议为字符串摘要；避免塞入原始异常对象）
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    message: str
    retryable: bool = False
    cause: Optional[str] = None


class AgentEvent(BaseModel):
    """
    AgentEvent：统一事件流条目（最小可用）。

    字段（对齐 core-contracts）：
    - type：事件类型
    - timestamp：RFC3339 时间字符串（序列化 key 固定为 `timestamp`）
    - run_id：run 标识
    - turn_id/step_id：可选（M1 不强制使用，但保留以避免后续破坏性变更）
    - payload：JSON object（dict），用于承载事件专用字段
    """

    model_config = ConfigDict(extra="forbid")

    type: str
    timestamp: str = Field(description="RFC3339 时间字符串；wire key 固定为 timestamp。")
    run_id: str
    turn_id: Optional[str] = None
    step_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""

        return self.model_dump_json(exclude_none=True)

    @classmethod
    def from_json(cls, raw_json: str) -> "AgentEvent":
        """从 JSON 字符串反序列化为 `AgentEvent`。"""

        return cls.model_validate_json(raw_json)

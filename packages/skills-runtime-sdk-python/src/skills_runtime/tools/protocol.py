"""
Tool 协议（ToolSpec / ToolCall / ToolResult）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools.md`
- `docs/specs/skills-runtime-sdk/docs/core-contracts.md`（工具输出 error_kind 口径）

本模块只定义“可实现级”的最小协议：
- ToolSpec：注册表条目（OpenAI function calling 兼容 JSON schema）
- ToolCall：执行输入（call_id/name/args）
- ToolResultPayload：执行输出的统一结构（ToolResult 的 content/details 使用它序列化）
- ToolResult：执行输出（ok/content/error_kind/message/details）
- tool_spec_to_openai_tool：将 ToolSpec 映射为 chat.completions tools[] 形状
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ToolSpec(BaseModel):
    """
    Tool 注册信息（function calling 兼容）。

    字段：
    - name：工具名（全局唯一，稳定）
    - description：工具说明
    - parameters：JSON Schema（必须为 object schema）
    - requires_approval：可选；提示该 tool 通常需要审批（最终由 safety/policy 决定）
    - sandbox_policy：可选；默认 sandbox 执行策略（inherit|none|restricted）
    - idempotency：可选；用于重试策略与审计（safe|unsafe|unknown）
    - output_schema：可选；SDK 内部校验/文档用
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    requires_approval: Optional[bool] = None
    sandbox_policy: Optional[str] = None
    idempotency: Optional[str] = None
    output_schema: Optional[Dict[str, Any]] = None


class ToolCall(BaseModel):
    """
    Tool 调用（内部表示）。

    字段：
    - call_id：本次调用的唯一 id（用于关联 tool output 回注）
    - name：工具名
    - args：解析后的参数 dict（json.loads(function.arguments) 的结果）
    - raw_arguments：原始 arguments 字符串（可选；用于 debug/错误恢复）
    """

    model_config = ConfigDict(extra="forbid")

    call_id: str
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)
    raw_arguments: Optional[str] = None


class ToolResultPayload(BaseModel):
    """
    Tool 执行结果 payload（统一输出封装）。

    对齐：
    - `docs/specs/skills-runtime-sdk/docs/core-contracts.md` §4.3（ToolResultPayload）

    说明：
    - 本结构用于两处：
      1) 回注模型：作为 JSON 字符串写入 tool message content（稳定、可解析）
      2) 事件/WAL：作为 object 写入 `tool_call_finished.result`（便于检索与回放）
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_ms: int = Field(default=0, ge=0)
    truncated: bool = False
    data: Optional[Dict[str, Any]] = None
    error_kind: Optional[str] = None
    retryable: bool = False
    retry_after_ms: Optional[int] = None


class ToolResult(BaseModel):
    """
    Tool 执行结果（统一 envelope）。

    字段（最小集合）：
    - ok：是否成功
    - content：回注给 LLM 的内容（建议为 JSON 字符串）
    - error_kind：错误分类（timeout/validation/not_found/human_required/permission/unknown...）
    - message：面向开发者/调用方的一句话说明（避免包含密钥）
    - details：结构化结果（事件/WAL 建议存 object；content 仍建议为 JSON 字符串）
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    content: str
    error_kind: Optional[str] = None
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    @classmethod
    def from_payload(cls, payload: ToolResultPayload, *, message: Optional[str] = None) -> "ToolResult":
        """
        从 ToolResultPayload 构造 ToolResult。

        参数：
        - payload：统一结构化结果（会被序列化为 JSON 字符串写入 content）
        - message：可选的一句话说明（用于调用方/日志；不建议写入密钥）
        """

        obj = payload.model_dump(exclude_none=True)
        return cls(
            ok=payload.ok,
            content=json.dumps(obj, ensure_ascii=False),
            error_kind=payload.error_kind,
            message=message,
            details=obj,
        )

    @classmethod
    def ok_payload(cls, *, stdout: str = "", data: Optional[Dict[str, Any]] = None, duration_ms: int = 0) -> "ToolResult":
        """便捷构造：成功结果。"""

        return cls.from_payload(
            ToolResultPayload(ok=True, stdout=stdout, stderr="", exit_code=0, duration_ms=duration_ms, data=data)
        )

    @classmethod
    def error_payload(
        cls,
        *,
        error_kind: str,
        stderr: str,
        data: Optional[Dict[str, Any]] = None,
        duration_ms: int = 0,
        retryable: bool = False,
        retry_after_ms: Optional[int] = None,
    ) -> "ToolResult":
        """便捷构造：失败结果（错误信息放入 stderr）。"""

        return cls.from_payload(
            ToolResultPayload(
                ok=False,
                stdout="",
                stderr=stderr,
                exit_code=None,
                duration_ms=duration_ms,
                truncated=False,
                data=data,
                error_kind=error_kind,
                retryable=retryable,
                retry_after_ms=retry_after_ms,
            )
        )

    @classmethod
    def ok_json(cls, payload: Dict[str, Any]) -> "ToolResult":
        """
        便捷构造：以“原样 JSON”作为 content 的成功结果。

        说明：
        - 该方法用于 Phase 2 的最小实现与单测夹具。
        - 若 payload 需要严格对齐 core-contracts 的 ToolResultPayload 字段，请改用 `from_payload()`。
        """

        return cls(ok=True, content=json.dumps(payload, ensure_ascii=False), details=payload)

    @classmethod
    def error_json(
        cls,
        *,
        error_kind: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> "ToolResult":
        """
        便捷构造：失败结果（兼容 tools.md 的 error_kind/message/details 口径）。

        说明：
        - content 会默认包含 `ok/error_kind/message`，并在存在 details 时附带 `details`。
        """

        payload: Dict[str, Any] = {"ok": False, "error_kind": error_kind, "message": message}
        if details is not None:
            payload["details"] = details
        return cls(
            ok=False,
            content=json.dumps(payload, ensure_ascii=False),
            error_kind=error_kind,
            message=message,
            details=details,
        )


@runtime_checkable
class HumanIOProvider(Protocol):
    """
    人类输入适配接口（ask_human 的上层实现由调用方提供）。

    方法：
    - request_human_input：向人类请求输入并返回 answer
    """

    def request_human_input(
        self,
        *,
        call_id: str,
        question: str,
        choices: Optional[list[str]] = None,
        context: Optional[Dict[str, Any]] = None,
        timeout_ms: Optional[int] = None,
    ) -> str:
        """
        向人类请求输入并返回回答文本。

        参数：
        - `call_id`：稳定标识，用于 UI/事件关联同一次请求与响应。
        - `question`：展示给用户的问题文本。
        - `choices`：可选项（可用于 UI 的下拉/按钮）。
        - `context`：可选的 UI 上下文（不得包含 secrets 明文）。
        - `timeout_ms`：超时毫秒；None 表示由实现自行决定等待策略。
        """

        ...


def tool_spec_to_openai_tool(spec: ToolSpec) -> Dict[str, Any]:
    """
    将 `ToolSpec` 映射为 OpenAI chat.completions 的 tools[] entry。

    返回形状（function calling）：
    {
      "type": "function",
      "function": { "name": "...", "description": "...", "parameters": {...} }
    }
    """

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }

"""
内置工具：web_search（Codex parity；Phase 5）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-web-and-image.md`

注意：
- 本工具默认 fail-closed：未配置 provider 时返回 validation（disabled）。
- 离线回归必须使用 fake provider（不得依赖外网）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext


class _WebSearchArgs(BaseModel):
    """web_search 输入参数。"""

    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, description="搜索关键字（trim 后非空）")
    recency: Optional[int] = Field(default=None, ge=0, description="只返回最近 N 天（可选）")
    limit: int = Field(default=10, ge=1, description="最多返回条数（>=1）")


WEB_SEARCH_SPEC = ToolSpec(
    name="web_search",
    description="执行联网搜索并返回结构化结果（默认关闭；需注入 provider）。",
    parameters={
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "搜索关键字（trim 后非空）"},
            "recency": {"type": "integer", "minimum": 0, "description": "只返回最近 N 天（可选）"},
            "limit": {"type": "integer", "minimum": 1, "description": "最多返回条数（>=1）"},
        },
        "required": ["q"],
        "additionalProperties": False,
    },
    requires_approval=False,
    idempotency="safe",
)


def web_search(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """
    执行 web_search（需注入 provider）。

    约定：
    - ctx.web_search_provider 需要提供 `search(q, recency, limit) -> list[dict]`
    """

    start = time.monotonic()
    try:
        args = _WebSearchArgs.model_validate(call.args)
    except Exception as e:
        return ToolResult.error_payload(error_kind="validation", stderr=str(e))

    q = str(args.q).strip()
    if not q:
        return ToolResult.error_payload(error_kind="validation", stderr="q must not be empty after trim")

    provider = ctx.web_search_provider
    if provider is None:
        return ToolResult.error_payload(
            error_kind="validation",
            stderr="web_search is disabled (no provider configured)",
            data={"disabled": True},
        )

    try:
        results = provider.search(q=q, recency=args.recency, limit=int(args.limit))  # type: ignore[attr-defined]
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        payload_err = ToolResultPayload(
            ok=False,
            stdout="",
            stderr=str(e),
            exit_code=None,
            duration_ms=duration_ms,
            truncated=False,
            data={"q": q},
            error_kind="unknown",
            retryable=True,
            retry_after_ms=None,
        )
        return ToolResult.from_payload(payload_err)

    safe_results: List[Dict[str, Any]] = []
    for it in list(results or []):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "") or "")
        url = str(it.get("url", "") or "")
        snippet = str(it.get("snippet", "") or "")
        safe_results.append({"title": title, "url": url, "snippet": snippet})

    duration_ms = int((time.monotonic() - start) * 1000)
    payload = ToolResultPayload(
        ok=True,
        stdout="",
        stderr="",
        exit_code=0,
        duration_ms=duration_ms,
        truncated=False,
        data={"results": safe_results},
        error_kind=None,
        retryable=False,
        retry_after_ms=None,
    )
    return ToolResult.from_payload(payload)

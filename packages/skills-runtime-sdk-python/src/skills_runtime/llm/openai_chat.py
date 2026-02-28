"""
OpenAI-compatible `/v1/chat/completions` backend（Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/llm-backend.md`
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from skills_runtime.config.loader import AgentSdkLlmConfig
from skills_runtime.llm.chat_sse import ChatCompletionsSseParser, ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.tools.protocol import ToolSpec, tool_spec_to_openai_tool


class OpenAIChatCompletionsBackend:
    """
    OpenAI-compatible chat.completions 实现（网络层）。

    说明：
    - Phase 2 优先保证 SSE 解析与 tool_calls 拼接口径一致；网络重试/backoff 在 Phase 3 完善。
    """

    def __init__(self, cfg: AgentSdkLlmConfig, *, api_key: Optional[str] = None) -> None:
        """
        创建 OpenAI-compatible chat.completions backend。

        参数：
        - `cfg`：LLM 配置（base_url、api_key_env、timeout 等）。
        - `api_key`：可选的 API key 覆盖（仅内存；优先于环境变量）。
        """

        self._cfg = cfg
        self._api_key_override = api_key

    def _endpoint(self) -> str:
        """返回 `/v1/chat/completions` 的完整 URL（基于 cfg.base_url 拼接）。"""

        base = self._cfg.base_url.rstrip("/")
        return f"{base}/chat/completions"

    def _auth_header(self) -> Dict[str, str]:
        """
        构造 Authorization header。

        异常：
        - 若缺少 API key（override 与 env 均为空）则抛 `ValueError`，由上层分类为配置错误。
        """

        key = self._api_key_override or os.environ.get(self._cfg.api_key_env, "")
        if not key:
            raise ValueError(f"缺少 API key 环境变量：{self._cfg.api_key_env}")
        return {"Authorization": f"Bearer {key}"}

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        """
        发起 streaming chat.completions 请求，并解析 SSE 事件流。

        说明：
        - 通过 ChatRequest 承载请求参数；
        - provider 特有扩展通过 request.extra 传递（可被忽略，但必须可预测）。
        """

        payload: Dict[str, Any] = {"model": request.model, "messages": request.messages, "stream": True}
        if request.tools:
            payload["tools"] = [tool_spec_to_openai_tool(s) for s in request.tools]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = int(request.max_tokens)
        if request.top_p is not None:
            payload["top_p"] = float(request.top_p)
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)

        def _retryable_status(code: int) -> bool:
            """判断 HTTP status 是否适合重试（保守）。"""

            if code == 429:
                return True
            if 500 <= code <= 599:
                return True
            return False

        def _retry_after_ms_from_headers(headers: httpx.Headers) -> Optional[int]:
            """
            从 `Retry-After` 头解析等待毫秒数。

            约束：
            - 仅支持整数秒的最小实现；无法解析则返回 None。
            """

            ra = headers.get("Retry-After")
            if not ra:
                return None
            try:
                sec = int(str(ra).strip())
                if sec <= 0:
                    return None
                return sec * 1000
            except (ValueError, TypeError):
                return None

        retry_cfg = getattr(self._cfg, "retry", None)
        base_delay_sec = float(getattr(retry_cfg, "base_delay_sec", 0.5) or 0.5)
        cap_delay_sec = float(getattr(retry_cfg, "cap_delay_sec", 8.0) or 8.0)
        jitter_ratio = float(getattr(retry_cfg, "jitter_ratio", 0.1) or 0.1)
        if jitter_ratio < 0:
            jitter_ratio = 0.0
        if jitter_ratio > 1:
            jitter_ratio = 1.0

        max_retries = int(getattr(retry_cfg, "max_retries", 0) or 0) if retry_cfg is not None else 0

        on_retry = request.extra.get("on_retry")

        async def _sleep_backoff_ms(
            *, attempt: int, retry_after_ms: Optional[int], notify_delay: Optional[callable] = None
        ) -> float:
            """
            等待退避时间（指数退避 + 抖动）。

            说明：
            - attempt 从 0 开始；
            - 优先使用 `Retry-After`（若存在），否则使用指数退避；
            - 抖动用于避免 thundering herd（但必须保持上限可控）。
            """

            if retry_after_ms is not None:
                delay = retry_after_ms / 1000.0
            else:
                base = base_delay_sec * (2 ** attempt)
                base = min(cap_delay_sec, base)
                jitter = random.uniform(0.0, base * jitter_ratio)
                delay = base + jitter
                delay = min(cap_delay_sec, delay)
            if notify_delay is not None:
                try:
                    notify_delay(float(delay))
                except Exception:
                    # 防御性兜底：notify_delay 由外部注入，可能抛出任意异常；不影响退避逻辑。
                    pass
            await asyncio.sleep(delay)
            return float(delay)

        timeout = httpx.Timeout(self._cfg.timeout_sec)
        headers = {"Content-Type": "application/json"}
        headers.update(self._auth_header())

        emitted_any = False
        attempt = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    parser = ChatCompletionsSseParser()
                    async with client.stream("POST", self._endpoint(), json=payload, headers=headers) as resp:
                        # 重要：streaming 模式下若直接 raise_for_status，HTTPStatusError 里的 response
                        # 往往没有缓存 body，导致上层无法解析 OpenAI 风格 {"error":{"message":...}}。
                        # 这里在非 2xx 时先读取响应 body（错误 JSON），再抛异常，保证可观测性。
                        if resp.status_code >= 400:
                            try:
                                await resp.aread()
                            except httpx.HTTPError:
                                pass
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[len("data:") :].strip()
                            for ev in parser.feed_data(data):
                                emitted_any = True
                                yield ev
                        for ev in parser.finish():
                            emitted_any = True
                            yield ev
                return
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if emitted_any or attempt >= max_retries or not _retryable_status(status):
                    raise
                retry_after_ms = _retry_after_ms_from_headers(exc.response.headers)
                delay_sec = await _sleep_backoff_ms(
                    attempt=attempt,
                    retry_after_ms=retry_after_ms,
                    notify_delay=(
                        (lambda d: on_retry(
                            {
                                "provider": "openai",
                                "error_kind": "http_status",
                                "status_code": int(status),
                                "attempt": int(attempt),
                                "max_retries": int(max_retries),
                                "retry_after_ms": int(retry_after_ms) if retry_after_ms is not None else None,
                                "delay_ms": int(d * 1000),
                            }
                        ))
                        if callable(on_retry)
                        else None
                    ),
                )
                attempt += 1
                continue
            except (httpx.TimeoutException, httpx.RequestError):
                # 网络错误：仅在未输出任何事件时允许重试，避免重复输出
                if emitted_any or attempt >= max_retries:
                    raise
                delay_sec = await _sleep_backoff_ms(
                    attempt=attempt,
                    retry_after_ms=None,
                    notify_delay=(
                        (lambda d: on_retry(
                            {
                                "provider": "openai",
                                "error_kind": "request_error",
                                "attempt": int(attempt),
                                "max_retries": int(max_retries),
                                "retry_after_ms": None,
                                "delay_ms": int(d * 1000),
                            }
                        ))
                        if callable(on_retry)
                        else None
                    ),
                )
                attempt += 1
                continue

    # 说明：本仓库不再提供 legacy `stream_chat(model, messages, ...)` 兼容入口。

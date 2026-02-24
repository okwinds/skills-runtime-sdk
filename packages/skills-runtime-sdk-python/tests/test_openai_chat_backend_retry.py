from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import pytest

from agent_sdk.config.loader import AgentSdkLlmConfig
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend
from agent_sdk.llm.protocol import ChatRequest


class _FakeStreamResponse:
    """
    伪造 httpx streaming response，用于离线回归 OpenAIChatCompletionsBackend 的 retry/backoff。

    说明：
    - 只实现 backend 运行所需的最小接口：status_code/headers/request/aread/raise_for_status/aiter_lines。
    - `aiter_lines` 支持按顺序产出行，并可在指定位置抛出 RequestError。
    """

    def __init__(
        self,
        *,
        status_code: int,
        headers: Optional[Dict[str, str]] = None,
        lines: Optional[List[str]] = None,
        raise_in_aiter: Optional[Exception] = None,
        request: Optional[httpx.Request] = None,
    ) -> None:
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self.request = request or httpx.Request("POST", "http://example.test/v1/chat/completions")
        self._lines = list(lines or [])
        self._raise_in_aiter = raise_in_aiter
        self.aread_called = False

    async def aread(self) -> bytes:
        self.aread_called = True
        return b"{}"

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        resp = httpx.Response(
            status_code=self.status_code,
            headers=self.headers,
            content=b"{}",
            request=self.request,
        )
        raise httpx.HTTPStatusError("HTTP error", request=self.request, response=resp)

    async def aiter_lines(self) -> AsyncIterator[str]:
        for i, line in enumerate(self._lines):
            yield line
            if self._raise_in_aiter is not None and i == 0:
                raise self._raise_in_aiter

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


class _Scenario:
    """
    以“每次创建 AsyncClient 代表一次 attempt”的方式组织响应序列。

    说明：
    - backend 的 retry 循环每次都会创建一个新的 `httpx.AsyncClient`；
    - 我们通过 `client_creations` 来验证是否发生了重试。
    """

    def __init__(self, responses: List[_FakeStreamResponse]) -> None:
        self.responses = responses
        self.client_creations = 0

    def make_client(self, *args: Any, **kwargs: Any) -> "_FakeAsyncClient":
        idx = self.client_creations
        self.client_creations += 1
        return _FakeAsyncClient(self, idx)


class _FakeAsyncClient:
    def __init__(self, scenario: _Scenario, idx: int) -> None:
        self._scenario = scenario
        self._idx = idx

    def stream(self, *args: Any, **kwargs: Any) -> _FakeStreamResponse:
        return self._scenario.responses[self._idx]

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


def _run_stream(backend: OpenAIChatCompletionsBackend) -> List[str]:
    """
    运行一次 stream_chat，并把事件类型收集成列表（便于断言）。
    """

    async def _go() -> List[str]:
        types: List[str] = []
        async for ev in backend.stream_chat(model="gpt-test", messages=[{"role": "user", "content": "hi"}], tools=None):
            types.append(str(getattr(ev, "type", "")))
        return types

    return asyncio.run(_go())


def test_openai_chat_retries_on_429_with_retry_after(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent_sdk.llm.openai_chat as mod

    sleeps: List[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(float(delay))

    # attempt0：429（带 Retry-After=1）→ 可重试；attempt1：200 成功完成
    ok_lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
        "data: DONE",
    ]
    scenario = _Scenario(
        [
            _FakeStreamResponse(status_code=429, headers={"Retry-After": "1"}, lines=[]),
            _FakeStreamResponse(status_code=200, lines=ok_lines),
        ]
    )

    monkeypatch.setattr(mod.httpx, "AsyncClient", scenario.make_client)
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    cfg = AgentSdkLlmConfig(base_url="http://example.test/v1", api_key_env="OPENAI_API_KEY", max_retries=3, timeout_sec=1)
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")
    types = _run_stream(backend)

    assert scenario.client_creations == 2, "expected one retry (two attempts)"
    assert sleeps and abs(sleeps[0] - 1.0) < 1e-6, "expected Retry-After=1s to be respected"
    assert "text_delta" in types
    assert "completed" in types


def test_openai_chat_retries_on_request_error_before_emitting(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent_sdk.llm.openai_chat as mod

    sleeps: List[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(float(delay))

    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    # attempt0：aiter_lines 第一次调用后抛 ReadError（RequestError 子类）且未输出任何事件 → 允许重试
    request = httpx.Request("POST", "http://example.test/v1/chat/completions")
    err = httpx.ReadError("read boom", request=request)
    scenario = _Scenario(
        [
            _FakeStreamResponse(status_code=200, lines=["data: " + json.dumps({"choices": [{"delta": {}}]})], raise_in_aiter=err),
            _FakeStreamResponse(
                status_code=200,
                lines=["data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]})],
            ),
        ]
    )

    monkeypatch.setattr(mod.httpx, "AsyncClient", scenario.make_client)

    cfg = AgentSdkLlmConfig(base_url="http://example.test/v1", api_key_env="OPENAI_API_KEY", max_retries=3, timeout_sec=1)
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")
    types = _run_stream(backend)

    assert scenario.client_creations == 2, "expected retry after request error"
    assert sleeps and abs(sleeps[0] - 0.5) < 1e-6, "attempt0 backoff should be 0.5s when no Retry-After"
    assert "text_delta" in types


def test_openai_chat_does_not_retry_after_emitting_any_event(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent_sdk.llm.openai_chat as mod

    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)

    request = httpx.Request("POST", "http://example.test/v1/chat/completions")
    err = httpx.ReadError("read boom", request=request)

    # attempt0：先输出一个 text_delta（emitted_any=True），再抛 ReadError → 必须不重试
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
    ]
    scenario = _Scenario([_FakeStreamResponse(status_code=200, lines=lines, raise_in_aiter=err)])
    monkeypatch.setattr(mod.httpx, "AsyncClient", scenario.make_client)

    cfg = AgentSdkLlmConfig(base_url="http://example.test/v1", api_key_env="OPENAI_API_KEY", max_retries=3, timeout_sec=1)
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")

    async def _go() -> None:
        async for _ in backend.stream_chat(model="gpt-test", messages=[{"role": "user", "content": "hi"}], tools=None):
            pass

    with pytest.raises(httpx.RequestError):
        asyncio.run(_go())

    assert scenario.client_creations == 1, "should not retry after emitted_any=True"


def test_openai_chat_retries_on_500_then_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent_sdk.llm.openai_chat as mod

    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)

    ok_lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
        "data: DONE",
    ]
    scenario = _Scenario(
        [
            _FakeStreamResponse(status_code=500, lines=[]),
            _FakeStreamResponse(status_code=200, lines=ok_lines),
        ]
    )

    monkeypatch.setattr(mod.httpx, "AsyncClient", scenario.make_client)

    cfg = AgentSdkLlmConfig(base_url="http://example.test/v1", api_key_env="OPENAI_API_KEY", max_retries=2, timeout_sec=1)
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")
    types = _run_stream(backend)

    assert scenario.client_creations == 2, "expected one retry (two attempts)"
    assert "text_delta" in types
    assert "completed" in types


def test_openai_chat_does_not_retry_on_400(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent_sdk.llm.openai_chat as mod

    scenario = _Scenario([_FakeStreamResponse(status_code=400, lines=[])])
    monkeypatch.setattr(mod.httpx, "AsyncClient", scenario.make_client)

    cfg = AgentSdkLlmConfig(base_url="http://example.test/v1", api_key_env="OPENAI_API_KEY", max_retries=3, timeout_sec=1)
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")

    async def _go() -> None:
        async for _ in backend.stream_chat(model="gpt-test", messages=[{"role": "user", "content": "hi"}], tools=None):
            pass

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_go())

    assert scenario.client_creations == 1, "should not retry on non-retryable 4xx"


def test_openai_chat_enforces_retry_max_retries(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent_sdk.llm.openai_chat as mod

    sleeps: List[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(float(delay))

    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    # 500 持续失败：应在 max_retries=1 后停止（总 attempts=2）
    scenario = _Scenario(
        [
            _FakeStreamResponse(status_code=500, lines=[]),
            _FakeStreamResponse(status_code=500, lines=[]),
            _FakeStreamResponse(status_code=500, lines=[]),  # 不应到达
        ]
    )
    monkeypatch.setattr(mod.httpx, "AsyncClient", scenario.make_client)

    cfg = AgentSdkLlmConfig(
        base_url="http://example.test/v1",
        api_key_env="OPENAI_API_KEY",
        max_retries=99,  # legacy 字段不应影响 retry.max_retries 显式覆盖
        timeout_sec=1,
        retry={"max_retries": 1, "base_delay_sec": 0.5, "cap_delay_sec": 8.0, "jitter_ratio": 0.0},
    )
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")

    async def _go() -> None:
        async for _ in backend.stream_chat(model="gpt-test", messages=[{"role": "user", "content": "hi"}], tools=None):
            pass

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_go())

    assert scenario.client_creations == 2, "expected attempts=1+max_retries"
    assert sleeps and abs(sleeps[0] - 0.5) < 1e-6


def test_openai_chat_retry_is_observable_via_on_retry_callback(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agent_sdk.llm.openai_chat as mod

    observed: List[Dict[str, Any]] = []

    def _on_retry(info: Dict[str, Any]) -> None:
        observed.append(dict(info))

    async def _fake_sleep(delay: float) -> None:
        # 不阻塞单测
        _ = delay

    monkeypatch.setattr(mod.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    ok_lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}),
        "data: DONE",
    ]
    scenario = _Scenario(
        [
            _FakeStreamResponse(status_code=500, lines=[]),
            _FakeStreamResponse(status_code=200, lines=ok_lines),
        ]
    )
    monkeypatch.setattr(mod.httpx, "AsyncClient", scenario.make_client)

    cfg = AgentSdkLlmConfig(
        base_url="http://example.test/v1",
        api_key_env="OPENAI_API_KEY",
        timeout_sec=1,
        retry={"max_retries": 1, "base_delay_sec": 0.5, "cap_delay_sec": 8.0, "jitter_ratio": 0.0},
    )
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")

    async def _go() -> None:
        req = ChatRequest(model="gpt-test", messages=[{"role": "user", "content": "hi"}], extra={"on_retry": _on_retry})
        async for _ in backend.stream_chat_v2(req):
            pass

    asyncio.run(_go())

    assert observed, "expected on_retry to be called at least once"
    assert observed[0].get("provider") == "openai"
    assert observed[0].get("error_kind") == "http_status"

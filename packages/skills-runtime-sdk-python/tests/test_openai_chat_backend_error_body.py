from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import httpx

from agent_sdk.config.loader import AgentSdkLlmConfig
from agent_sdk.llm.openai_chat import OpenAIChatCompletionsBackend
from agent_sdk.llm.protocol import ChatRequest


class _FakeResponse:
    def __init__(self) -> None:
        self.status_code = 401
        self._content: bytes = b""
        self.aread_called = False
        self.request = httpx.Request("POST", "http://example.test/v1/chat/completions")

    async def aread(self) -> bytes:
        self.aread_called = True
        self._content = b'{"error":{"message":"Invalid API key"}}'
        return self._content

    def raise_for_status(self) -> None:
        # 如果没有先 aread，则 response.content 为空；上层难以提取 error.message
        resp = httpx.Response(status_code=self.status_code, content=self._content, request=self.request)
        raise httpx.HTTPStatusError("HTTP error", request=self.request, response=resp)

    async def aiter_lines(self) -> AsyncIterator[str]:
        if False:  # pragma: no cover
            yield ""  # never

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


class _FakeClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.resp = _FakeResponse()

    def stream(self, *args: Any, **kwargs: Any) -> _FakeResponse:
        return self.resp

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None


def test_openai_chat_backend_reads_error_body_before_raise(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # patch httpx.AsyncClient used by backend
    import agent_sdk.llm.openai_chat as mod

    fake_client = _FakeClient()
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: fake_client)

    cfg = AgentSdkLlmConfig(base_url="http://example.test/v1", api_key_env="OPENAI_API_KEY")
    backend = OpenAIChatCompletionsBackend(cfg, api_key="sk-test")

    async def _run() -> None:
        with pytest.raises(httpx.HTTPStatusError):
            req = ChatRequest(model="gpt-4", messages=[{"role": "user", "content": "hi"}], tools=None)
            async for _ in backend.stream_chat(req):
                pass

    import pytest

    asyncio.run(_run())
    assert fake_client.resp.aread_called is True

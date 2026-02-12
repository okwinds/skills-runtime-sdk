from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from agent_sdk.tools.builtin.web_search import web_search
from agent_sdk.tools.protocol import ToolCall
from agent_sdk.tools.registry import ToolExecutionContext


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    return json.loads(result.content)


class _FakeWebSearchProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_exc: Optional[Exception] = None

    def search(self, *, q: str, recency: Optional[int], limit: int) -> list[dict[str, Any]]:
        self.calls.append({"q": q, "recency": recency, "limit": limit})
        if self.raise_exc is not None:
            raise self.raise_exc
        return [
            {"title": "t1", "url": "u1", "snippet": "s1"},
            {"title": 2, "url": 3, "snippet": None},
            "bad",  # type: ignore[list-item]
        ]


def _mk_ctx(tmp_path: Path, provider: Optional[_FakeWebSearchProvider]) -> ToolExecutionContext:
    return ToolExecutionContext(
        workspace_root=tmp_path,
        run_id="t_web_search",
        emit_tool_events=False,
        web_search_provider=provider,
    )


def test_web_search_disabled_without_provider(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, None)
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_web_search_q_trim_empty_is_validation(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, _FakeWebSearchProvider())
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "   "}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_web_search_ok_with_provider(tmp_path: Path) -> None:
    provider = _FakeWebSearchProvider()
    ctx = _mk_ctx(tmp_path, provider)
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "hello", "limit": 2, "recency": 3}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    assert len(p["data"]["results"]) >= 1
    assert provider.calls[0]["q"] == "hello"
    assert provider.calls[0]["limit"] == 2
    assert provider.calls[0]["recency"] == 3


def test_web_search_filters_non_dict_entries(tmp_path: Path) -> None:
    provider = _FakeWebSearchProvider()
    ctx = _mk_ctx(tmp_path, provider)
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x"}), ctx)
    p = _payload(r)
    assert p["ok"] is True
    assert all(isinstance(it, dict) for it in p["data"]["results"])


def test_web_search_provider_exception_is_unknown_and_retryable(tmp_path: Path) -> None:
    provider = _FakeWebSearchProvider()
    provider.raise_exc = RuntimeError("boom")
    ctx = _mk_ctx(tmp_path, provider)
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x"}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "unknown"
    assert p["retryable"] is True


def test_web_search_limit_must_be_ge_1(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, _FakeWebSearchProvider())
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x", "limit": 0}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_web_search_recency_must_be_ge_0(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, _FakeWebSearchProvider())
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x", "recency": -1}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_web_search_additional_fields_forbidden(tmp_path: Path) -> None:
    ctx = _mk_ctx(tmp_path, _FakeWebSearchProvider())
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x", "x": 1}), ctx)
    p = _payload(r)
    assert p["ok"] is False
    assert p["error_kind"] == "validation"


def test_web_search_result_fields_are_strings(tmp_path: Path) -> None:
    provider = _FakeWebSearchProvider()
    ctx = _mk_ctx(tmp_path, provider)
    r = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x"}), ctx)
    p = _payload(r)
    it = p["data"]["results"][1]
    assert isinstance(it["title"], str)
    assert isinstance(it["url"], str)
    assert isinstance(it["snippet"], str)


def test_web_search_default_limit_is_10(tmp_path: Path) -> None:
    provider = _FakeWebSearchProvider()
    ctx = _mk_ctx(tmp_path, provider)
    _ = web_search(ToolCall(call_id="c1", name="web_search", args={"q": "x"}), ctx)
    assert provider.calls[0]["limit"] == 10


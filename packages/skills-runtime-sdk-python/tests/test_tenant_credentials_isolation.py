from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import pytest

from skills_runtime.bootstrap import build_agent


class _CapturingBackend:
    """
    用于测试的 fake OpenAI backend：只捕获构造时传入的 `api_key`，不做任何网络请求。

    注意：
    - `build_agent(...)` 内部使用 `from skills_runtime.llm.openai_chat import OpenAIChatCompletionsBackend`
      的方式按需导入，因此测试用 monkeypatch 替换该符号即可拦截构造。
    """

    def __init__(self, cfg: object, *, api_key: Optional[str] = None) -> None:
        self.cfg = cfg
        self.api_key = api_key


def _patch_openai_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("skills_runtime.llm.openai_chat.OpenAIChatCompletionsBackend", _CapturingBackend)


def test_build_agent_inmemory_llm_api_key_override_beats_os_environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_openai_backend(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "key-env")

    agent = build_agent(
        workspace_root=tmp_path,
        session_settings={"llm": {"api_key_env": "OPENAI_API_KEY"}},
        tenant_id="tenant-a",
        llm_api_key="key-a",
    )

    assert getattr(agent, "_backend").api_key == "key-a"


def test_build_agent_llm_api_key_ref_requires_resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_openai_backend(monkeypatch)

    with pytest.raises(ValueError):
        build_agent(
            workspace_root=tmp_path,
            session_settings={"llm": {"api_key_env": "OPENAI_API_KEY"}},
            tenant_id="tenant-a",
            llm_api_key_ref="cred-a",
        )


def test_concurrent_builds_isolate_tenant_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_openai_backend(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "key-env")

    def _resolve(ref: str, tenant_id: Optional[str]) -> str:
        if (ref, tenant_id) == ("cred-a", "tenant-a"):
            return "key-a"
        if (ref, tenant_id) == ("cred-b", "tenant-b"):
            return "key-b"
        raise KeyError((ref, tenant_id))

    def _build(*, tenant_id: str, ref: str, resolver: Callable[[str, Optional[str]], str]) -> str:
        agent = build_agent(
            workspace_root=tmp_path,
            session_settings={"llm": {"api_key_env": "OPENAI_API_KEY"}},
            tenant_id=tenant_id,
            llm_api_key_ref=ref,
            resolve_llm_api_key=resolver,
        )
        return getattr(agent, "_backend").api_key

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_a = pool.submit(_build, tenant_id="tenant-a", ref="cred-a", resolver=_resolve)
        fut_b = pool.submit(_build, tenant_id="tenant-b", ref="cred-b", resolver=_resolve)
        key_a = fut_a.result(timeout=10)
        key_b = fut_b.result(timeout=10)

    assert key_a == "key-a"
    assert key_b == "key-b"


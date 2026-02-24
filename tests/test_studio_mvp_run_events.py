from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _enable_imports() -> None:
    """
    使 Studio MVP backend 与 SDK 可在“无需安装”的情况下被 import。
    """

    root = _repo_root()
    sdk_src = root / "packages" / "skills-runtime-sdk-python" / "src"
    studio_backend_src = root / "packages" / "skills-runtime-studio-mvp" / "backend" / "src"

    sys.path.insert(0, str(sdk_src))
    sys.path.insert(0, str(studio_backend_src))


def _reload_studio_api_app() -> object:
    """
    以指定 workspace_root 重新加载 `studio_api.app`（其模块级全局会在 import 时解析 env）。
    """

    for name in list(sys.modules.keys()):
        if name == "studio_api" or name.startswith("studio_api."):
            sys.modules.pop(name, None)

    import studio_api.app as studio_app

    return studio_app


def test_create_run_always_emits_terminal_event_on_worker_exception(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    回归：即使 worker 线程内异常（例如 overlay 文件不存在），也必须：
    1) 预创建 events.jsonl（SSE 不应因文件缺失而空转）
    2) 追加 run_failed（SSE 很快可读到 terminal event）
    """

    _enable_imports()

    monkeypatch.setenv("STUDIO_WORKSPACE_ROOT", str(tmp_path))
    # 触发高概率崩溃源：overlay 路径缺失（_build_agent 读 overlay 时应抛异常）
    monkeypatch.setenv("SKILLS_RUNTIME_SDK_CONFIG_PATHS", "missing-overlay.yaml")

    studio_app = _reload_studio_api_app()

    storage = getattr(studio_app, "_STORAGE")
    session = storage.create_session(title=None, filesystem_sources=None)

    create_run_fn = getattr(studio_app, "create_run")
    create_run_req = getattr(studio_app, "CreateRunReq")

    out = asyncio.run(create_run_fn(session.session_id, create_run_req(message="hello")))
    run_id = str(out.get("run_id") or "")
    assert run_id.startswith("run_")

    events_jsonl_path = (tmp_path / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()
    assert events_jsonl_path.exists(), "events.jsonl 必须在 create_run 返回 run_id 前存在（允许为空文件）"

    class _DummyRequest:
        client = ("test", 0)

        async def is_disconnected(self) -> bool:
            return False

    async def _read_first_sse_chunk() -> bytes:
        stream_jsonl_as_sse = getattr(studio_app, "stream_jsonl_as_sse")
        agen = stream_jsonl_as_sse(request=_DummyRequest(), jsonl_path=events_jsonl_path, poll_interval_sec=0.01)
        async for chunk in agen:
            return chunk
        return b""

    chunk = asyncio.run(asyncio.wait_for(_read_first_sse_chunk(), timeout=2.0))
    assert b"event: run_failed" in chunk

    text = chunk.decode("utf-8", errors="replace")
    data_line = next((line for line in text.splitlines() if line.startswith("data: ")), "")
    assert data_line.startswith("data: ")

    obj = json.loads(data_line[len("data: ") :])
    assert obj.get("type") == "run_failed"
    assert obj.get("run_id") == run_id

    payload = obj.get("payload") or {}
    assert isinstance(payload, dict)
    assert isinstance(payload.get("error_kind"), str) and payload["error_kind"]
    assert isinstance(payload.get("message"), str) and payload["message"]
    assert payload.get("wal_locator") == str(events_jsonl_path)

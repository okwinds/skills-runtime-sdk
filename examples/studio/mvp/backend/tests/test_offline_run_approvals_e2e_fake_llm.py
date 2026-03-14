import importlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastapi.testclient import TestClient
import asyncio

from studio_api.sse import stream_jsonl_as_sse


def _load_app_mod(tmp_path: Path):
    os.environ["STUDIO_WORKSPACE_ROOT"] = str(tmp_path)
    os.environ["STUDIO_LLM_BACKEND"] = "fake"
    if "studio_api.app" in sys.modules:
        importlib.reload(sys.modules["studio_api.app"])
    else:
        import studio_api.app  # noqa: F401
    import studio_api.app as mod
    return mod


def _events_jsonl_path(tmp_path: Path, run_id: str) -> Path:
    return (tmp_path / ".skills_runtime_sdk" / "runs" / run_id / "events.jsonl").resolve()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _wait_for_event(path: Path, *, ev_type: str, timeout_sec: float = 3.0) -> Dict[str, Any]:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        events = _read_jsonl(path)
        hit = next((e for e in events if e.get("type") == ev_type), None)
        if isinstance(hit, dict):
            return hit
        time.sleep(0.01)
    raise AssertionError(f"event not observed within timeout: {ev_type}")


class _DummyRequest:
    client = None

    async def is_disconnected(self) -> bool:
        return False


def _parse_sse_chunks(chunks: List[bytes]) -> List[Tuple[str, Dict[str, Any]]]:
    buf = b"".join(chunks)
    out: List[Tuple[str, Dict[str, Any]]] = []
    for block in buf.split(b"\n\n"):
        text = block.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        ev_type = ""
        data = ""
        for line in text.splitlines():
            if line.startswith("event:"):
                ev_type = line[len("event:") :].strip()
            if line.startswith("data:"):
                data = line[len("data:") :].strip()
        if not ev_type or not data:
            continue
        obj = json.loads(data)
        out.append((ev_type, obj))
    return out


def _collect_sse_from_jsonl_until(path: Path, *, terminal_events: set[str], timeout_sec: float = 5.0) -> List[Tuple[str, Dict[str, Any]]]:
    async def _collect() -> List[bytes]:
        out: List[bytes] = []
        async for chunk in stream_jsonl_as_sse(
            request=_DummyRequest(),
            jsonl_path=path,
            poll_interval_sec=0.01,
            terminal_events=terminal_events,
        ):
            out.append(chunk)
        return out

    # 该 generator 会在 terminal_events 命中后主动停止；timeout 用于避免测试环境意外卡死。
    chunks = asyncio.run(asyncio.wait_for(_collect(), timeout=timeout_sec))
    return _parse_sse_chunks(chunks)


def test_offline_run_approvals_e2e_fake_llm(tmp_path: Path) -> None:
    mod = _load_app_mod(tmp_path)
    client = TestClient(mod.app)

    # create session
    s = client.post("/api/v1/sessions", json={"title": "t"})
    assert s.status_code == 201, s.text
    session_id = s.json().get("session_id")
    assert isinstance(session_id, str) and session_id

    # create run (background thread starts)
    r = client.post(f"/api/v1/sessions/{session_id}/runs", json={"message": "hello"})
    assert r.status_code == 201, r.text
    run_id = r.json().get("run_id")
    assert isinstance(run_id, str) and run_id

    events_jsonl_path = _events_jsonl_path(tmp_path, run_id)
    _wait_for_event(events_jsonl_path, ev_type="run_started", timeout_sec=3.0)

    # SSE connect #1: read until approval_requested then disconnect
    stream_url = f"/api/v1/runs/{run_id}/events/stream"
    approval_key = None
    sse1 = _collect_sse_from_jsonl_until(events_jsonl_path, terminal_events={"approval_requested"}, timeout_sec=5.0)
    for ev_type, obj in sse1:
        if ev_type == "approval_requested":
            approval_key = (obj.get("payload") or {}).get("approval_key")
    assert isinstance(approval_key, str) and approval_key

    # pending approvals endpoint should show it
    pending = client.get(f"/api/v1/runs/{run_id}/approvals/pending")
    assert pending.status_code == 200, pending.text
    approvals = pending.json().get("approvals") or []
    assert any(isinstance(a, dict) and a.get("approval_key") == approval_key for a in approvals)

    # decide approval
    decided = client.post(f"/api/v1/runs/{run_id}/approvals/{approval_key}", json={"decision": "approved"})
    assert decided.status_code == 200, decided.text
    assert decided.json().get("ok") is True

    # SSE connect #2: read until run_completed (terminal)
    sse2 = _collect_sse_from_jsonl_until(events_jsonl_path, terminal_events={"run_completed"}, timeout_sec=5.0)
    completed = [obj for ev_type, obj in sse2 if ev_type == "run_completed"]
    assert len(completed) == 1
    assert (completed[0].get("payload") or {}).get("final_output") == "done"

    # side effect: file_write should have created output file inside workspace root
    out = (tmp_path / "studio_fake_llm_output.txt").resolve()
    assert out.exists() is True
    assert "hello from fake llm" in out.read_text(encoding="utf-8")

    # after completion, pending approvals should be empty
    after = client.get(f"/api/v1/runs/{run_id}/approvals/pending")
    assert after.status_code == 200, after.text
    assert after.json().get("approvals") == []

    # SSE reconnect after completion: should replay historical events (including the earlier approval_requested)
    sse3 = _collect_sse_from_jsonl_until(events_jsonl_path, terminal_events={"run_completed"}, timeout_sec=5.0)
    assert any(
        ev_type == "approval_requested" and (obj.get("payload") or {}).get("approval_key") == approval_key
        for ev_type, obj in sse3
    )
    assert any(ev_type == "run_completed" and (obj.get("payload") or {}).get("final_output") == "done" for ev_type, obj in sse3)

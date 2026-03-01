import importlib
import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


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


def _read_jsonl(path: Path):
    if not path.exists():
        return []
    out = []
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


def _wait_for_event(path: Path, *, ev_type: str, timeout_sec: float = 3.0):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        events = _read_jsonl(path)
        hit = next((e for e in events if e.get("type") == ev_type), None)
        if isinstance(hit, dict):
            return hit
        time.sleep(0.01)
    raise AssertionError(f"event not observed within timeout: {ev_type}")


def test_run_failed_error_kind_is_not_exception_class_name_for_unknown_exception(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.11）：
    Studio MVP 的兜底 run_failed.error_kind 不得写入异常类名；未知异常必须映射到稳定 fallback。
    """

    mod = _load_app_mod(tmp_path)
    client = TestClient(mod.app)

    def _boom(*_a, **_kw):
        raise RuntimeError("boom")

    mod._build_agent = _boom  # type: ignore[attr-defined]

    s = client.post("/api/v1/sessions", json={"title": "t"})
    assert s.status_code == 201, s.text
    session_id = s.json().get("session_id")
    assert isinstance(session_id, str) and session_id

    r = client.post(f"/api/v1/sessions/{session_id}/runs", json={"message": "hello"})
    assert r.status_code == 201, r.text
    run_id = r.json().get("run_id")
    assert isinstance(run_id, str) and run_id

    events_jsonl_path = _events_jsonl_path(tmp_path, run_id)
    ev = _wait_for_event(events_jsonl_path, ev_type="run_failed", timeout_sec=3.0)
    payload = ev.get("payload") or {}
    assert isinstance(payload, dict)

    assert payload.get("error_kind") == "unknown"
    assert payload.get("error_kind") != "RuntimeError"


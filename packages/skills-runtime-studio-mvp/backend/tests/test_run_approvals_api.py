import importlib
import os
import sys
import threading
import time
from typing import Any, Dict, Tuple

from fastapi.testclient import TestClient

from skills_runtime.safety.approvals import ApprovalDecision, ApprovalRequest


def _load_app_mod(tmp_path):
    os.environ["STUDIO_WORKSPACE_ROOT"] = str(tmp_path)
    if "studio_api.app" in sys.modules:
        importlib.reload(sys.modules["studio_api.app"])
    else:
        import studio_api.app  # noqa: F401
    import studio_api.app as mod
    return mod


def _start_pending_approval(*, mod, run_id: str, approval_key: str) -> Tuple[threading.Thread, Dict[str, Any]]:
    """
    Start a background worker that requests approval (and blocks) so the API can observe a pending item.

    Returns the worker thread and a dict that will contain {"decision": ApprovalDecision} once resolved.
    """

    got: Dict[str, Any] = {}

    async def _await_decision() -> ApprovalDecision:
        provider = mod._APPROVALS.provider_for_run(run_id=run_id)
        req = ApprovalRequest(
            approval_key=approval_key,
            tool="shell_exec",
            summary="echo hi",
            details={"argv": ["/bin/echo", "hi"]},
        )
        return await provider.request_approval(request=req, timeout_ms=2_000)

    def _worker() -> None:
        import asyncio

        got["decision"] = asyncio.run(_await_decision())

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t, got


def _wait_for_pending(client: TestClient, *, run_id: str, approval_key: str, timeout_sec: float = 1.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        resp = client.get(f"/api/v1/runs/{run_id}/approvals/pending")
        assert resp.status_code == 200, resp.text
        approvals = resp.json().get("approvals") or []
        if any(isinstance(a, dict) and a.get("approval_key") == approval_key for a in approvals):
            return
        time.sleep(0.01)
    raise AssertionError("pending approval not observed within timeout")


def test_pending_approvals_empty_list(tmp_path) -> None:
    mod = _load_app_mod(tmp_path)
    client = TestClient(mod.app)

    run_id = "run_empty"
    resp = client.get(f"/api/v1/runs/{run_id}/approvals/pending")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == {"run_id": run_id, "approvals": []}


def test_pending_approvals_includes_required_fields(tmp_path) -> None:
    mod = _load_app_mod(tmp_path)
    client = TestClient(mod.app)

    run_id = "run_with_pending"
    approval_key = "k_pending_1"
    t, got = _start_pending_approval(mod=mod, run_id=run_id, approval_key=approval_key)
    _wait_for_pending(client, run_id=run_id, approval_key=approval_key)

    resp = client.get(f"/api/v1/runs/{run_id}/approvals/pending")
    assert resp.status_code == 200, resp.text
    approvals = resp.json().get("approvals") or []
    assert isinstance(approvals, list)
    assert len(approvals) >= 1

    item = next(a for a in approvals if isinstance(a, dict) and a.get("approval_key") == approval_key)
    assert item.get("approval_key") == approval_key
    assert item.get("tool") == "shell_exec"
    assert item.get("summary") == "echo hi"
    assert isinstance(item.get("request"), dict)
    assert isinstance(item.get("age_ms"), int)
    assert item.get("age_ms") >= 0

    decided = client.post(f"/api/v1/runs/{run_id}/approvals/{approval_key}", json={"decision": "approved"})
    assert decided.status_code == 200, decided.text
    assert decided.json().get("ok") is True

    t.join(timeout=2.0)
    assert got.get("decision") == ApprovalDecision.APPROVED

    after = client.get(f"/api/v1/runs/{run_id}/approvals/pending")
    assert after.status_code == 200, after.text
    assert after.json().get("approvals") == []


def test_decide_invalid_decision_returns_400_and_does_not_resolve(tmp_path) -> None:
    mod = _load_app_mod(tmp_path)
    client = TestClient(mod.app)

    run_id = "run_invalid_decision"
    approval_key = "k_invalid_1"
    t, got = _start_pending_approval(mod=mod, run_id=run_id, approval_key=approval_key)
    _wait_for_pending(client, run_id=run_id, approval_key=approval_key)

    bad = client.post(f"/api/v1/runs/{run_id}/approvals/{approval_key}", json={"decision": "nope"})
    assert bad.status_code == 400, bad.text

    # Still pending
    pending = client.get(f"/api/v1/runs/{run_id}/approvals/pending")
    assert pending.status_code == 200, pending.text
    approvals = pending.json().get("approvals") or []
    assert any(isinstance(a, dict) and a.get("approval_key") == approval_key for a in approvals)

    # Clean up: resolve with a valid decision so the worker can exit.
    ok = client.post(f"/api/v1/runs/{run_id}/approvals/{approval_key}", json={"decision": "denied"})
    assert ok.status_code == 200, ok.text
    t.join(timeout=2.0)
    assert got.get("decision") == ApprovalDecision.DENIED


def test_decide_nonexistent_returns_404(tmp_path) -> None:
    mod = _load_app_mod(tmp_path)
    client = TestClient(mod.app)

    run_id = "run_404"
    approval_key = "no_such_key"
    resp = client.post(f"/api/v1/runs/{run_id}/approvals/{approval_key}", json={"decision": "approved"})
    assert resp.status_code == 404, resp.text

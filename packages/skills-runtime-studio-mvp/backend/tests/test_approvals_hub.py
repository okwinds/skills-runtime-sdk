from __future__ import annotations

import asyncio
import threading
import time
from typing import Dict

from agent_sdk.safety.approvals import ApprovalDecision, ApprovalRequest

from studio_api.approvals import ApprovalHub


def test_approval_hub_can_resolve_decision_from_other_thread() -> None:
    hub = ApprovalHub()
    run_id = "run_test"
    approval_key = "k1"

    got: Dict[str, object] = {}

    async def _await_decision() -> ApprovalDecision:
        provider = hub.provider_for_run(run_id=run_id)
        req = ApprovalRequest(
            approval_key=approval_key,
            tool="shell_exec",
            summary="run echo",
            details={"argv": ["/bin/echo", "hi"]},
        )
        return await provider.request_approval(request=req, timeout_ms=2_000)

    def _worker() -> None:
        got["decision"] = asyncio.run(_await_decision())

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    # 等待 pending 出现（最多 1s）
    deadline = time.time() + 1.0
    while time.time() < deadline:
        pending = hub.list_pending(run_id=run_id)
        if pending and pending[0].get("approval_key") == approval_key:
            break
        time.sleep(0.01)

    assert hub.decide(run_id=run_id, approval_key=approval_key, decision="approved") is True
    t.join(timeout=2.0)
    assert got.get("decision") == ApprovalDecision.APPROVED


def test_approval_hub_invalid_decision_raises() -> None:
    hub = ApprovalHub()
    run_id = "run_invalid"
    approval_key = "k2"

    async def _await_decision() -> ApprovalDecision:
        provider = hub.provider_for_run(run_id=run_id)
        req = ApprovalRequest(
            approval_key=approval_key,
            tool="shell_exec",
            summary="run echo",
            details={"argv": ["/bin/echo", "hi"]},
        )
        return await provider.request_approval(request=req, timeout_ms=2_000)

    t = threading.Thread(target=lambda: asyncio.run(_await_decision()), daemon=True)
    t.start()

    deadline = time.time() + 1.0
    while time.time() < deadline:
        if hub.list_pending(run_id=run_id):
            break
        time.sleep(0.01)

    try:
        hub.decide(run_id=run_id, approval_key=approval_key, decision="nope")
        assert False, "expected ValueError"
    except ValueError:
        pass

    assert hub.decide(run_id=run_id, approval_key=approval_key, decision="denied") is True
    t.join(timeout=2.0)

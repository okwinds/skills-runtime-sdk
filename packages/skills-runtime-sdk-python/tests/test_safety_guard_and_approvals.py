from __future__ import annotations

from skills_runtime.safety.approvals import compute_approval_key
from skills_runtime.safety.guard import evaluate_command_risk


def test_guard_detects_sudo_high_risk() -> None:
    r = evaluate_command_risk(["sudo", "ls", "/"])
    assert r.risk_level == "high"


def test_guard_detects_rm_rf_root_high_risk() -> None:
    r = evaluate_command_risk(["rm", "-rf", "/"])
    assert r.risk_level == "high"


def test_compute_approval_key_stable() -> None:
    k1 = compute_approval_key(tool="shell_exec", request={"argv": ["echo", "hi"], "cwd": "/tmp"})
    k2 = compute_approval_key(tool="shell_exec", request={"cwd": "/tmp", "argv": ["echo", "hi"]})
    assert k1 == k2


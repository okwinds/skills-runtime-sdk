"""
Safety（Guard + Approvals）模块。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/safety.md`
"""

from __future__ import annotations

from skills_runtime.safety.approvals import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
    compute_approval_key,
)
from skills_runtime.safety.gate import GateDecision, SafetyGate
from skills_runtime.safety.rule_approvals import ApprovalRule, RuleBasedApprovalProvider
from skills_runtime.safety.guard import CommandRisk, evaluate_command_risk
from skills_runtime.safety.policy import (
    PolicyDecision,
    evaluate_policy_for_custom_tool,
    evaluate_policy_for_shell_exec,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalRule",
    "ApprovalProvider",
    "ApprovalRequest",
    "CommandRisk",
    "GateDecision",
    "PolicyDecision",
    "RuleBasedApprovalProvider",
    "SafetyGate",
    "compute_approval_key",
    "evaluate_command_risk",
    "evaluate_policy_for_custom_tool",
    "evaluate_policy_for_shell_exec",
]

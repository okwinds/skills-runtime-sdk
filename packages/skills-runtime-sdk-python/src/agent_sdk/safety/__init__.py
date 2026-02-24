"""
Safety（Guard + Approvals）模块。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/safety.md`
"""

from __future__ import annotations

from agent_sdk.safety.approvals import (
    ApprovalDecision,
    ApprovalProvider,
    ApprovalRequest,
    compute_approval_key,
)
from agent_sdk.safety.rule_approvals import ApprovalRule, RuleBasedApprovalProvider
from agent_sdk.safety.guard import CommandRisk, evaluate_command_risk
from agent_sdk.safety.policy import PolicyDecision, evaluate_policy_for_shell_exec

__all__ = [
    "ApprovalDecision",
    "ApprovalRule",
    "ApprovalProvider",
    "ApprovalRequest",
    "CommandRisk",
    "PolicyDecision",
    "RuleBasedApprovalProvider",
    "compute_approval_key",
    "evaluate_command_risk",
    "evaluate_policy_for_shell_exec",
]

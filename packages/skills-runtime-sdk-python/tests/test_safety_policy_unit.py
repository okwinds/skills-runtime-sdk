"""
Unit tests for skills_runtime.safety.policy.

Covers evaluate_policy_for_shell_exec and evaluate_policy_for_custom_tool
with all decision branches: denylist, mode=deny, require_escalated,
allowlist, mode=allow, risk=high, and mode=ask default.
"""

from __future__ import annotations

import pytest

from skills_runtime.config.loader import AgentSdkSafetyConfig
from skills_runtime.safety.guard import CommandRisk
from skills_runtime.safety.policy import PolicyDecision, evaluate_policy_for_shell_exec, evaluate_policy_for_custom_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safety(
    *,
    mode: str = "ask",
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    tool_allowlist: list[str] | None = None,
    tool_denylist: list[str] | None = None,
) -> AgentSdkSafetyConfig:
    return AgentSdkSafetyConfig(
        mode=mode,  # type: ignore[arg-type]
        allowlist=allowlist or [],
        denylist=denylist or [],
        tool_allowlist=tool_allowlist or [],
        tool_denylist=tool_denylist or [],
    )


def _risk(level: str = "low") -> CommandRisk:
    return CommandRisk(risk_level=level, reason="test")


# ---------------------------------------------------------------------------
# evaluate_policy_for_shell_exec
# ---------------------------------------------------------------------------

class TestShellExecDenylist:
    def test_exact_command_hit_returns_deny(self):
        safety = _safety(denylist=["rm"])
        result = evaluate_policy_for_shell_exec(argv=["rm", "-rf", "/tmp/x"], risk=_risk(), safety=safety)
        assert result.action == "deny"
        assert result.matched_rule == "rm"

    def test_prefix_with_space_hit_returns_deny(self):
        safety = _safety(denylist=["git push --force"])
        result = evaluate_policy_for_shell_exec(argv=["git", "push", "--force"], risk=_risk(), safety=safety)
        assert result.action == "deny"

    def test_denylist_miss_does_not_deny(self):
        safety = _safety(mode="allow", denylist=["shutdown"])
        result = evaluate_policy_for_shell_exec(argv=["ls", "-la"], risk=_risk(), safety=safety)
        assert result.action == "allow"

    def test_denylist_takes_priority_over_mode_allow(self):
        safety = _safety(mode="allow", denylist=["rm"])
        result = evaluate_policy_for_shell_exec(argv=["rm", "file.txt"], risk=_risk(), safety=safety)
        assert result.action == "deny"


class TestShellExecModeDeny:
    def test_mode_deny_returns_deny(self):
        safety = _safety(mode="deny")
        result = evaluate_policy_for_shell_exec(argv=["ls"], risk=_risk(), safety=safety)
        assert result.action == "deny"
        assert result.matched_rule == "mode=deny"

    def test_mode_deny_overrides_allowlist(self):
        # denylist is checked first; mode=deny fires after denylist miss
        safety = _safety(mode="deny", allowlist=["ls"])
        result = evaluate_policy_for_shell_exec(argv=["ls"], risk=_risk(), safety=safety)
        assert result.action == "deny"


class TestShellExecRequireEscalated:
    def test_require_escalated_returns_ask(self):
        safety = _safety(mode="allow")
        result = evaluate_policy_for_shell_exec(
            argv=["some-tool"], risk=_risk(), safety=safety, sandbox_permissions="require_escalated"
        )
        assert result.action == "ask"
        assert result.matched_rule == "sandbox"

    def test_require_escalated_overrides_allowlist(self):
        safety = _safety(mode="allow", allowlist=["some-tool"])
        result = evaluate_policy_for_shell_exec(
            argv=["some-tool"], risk=_risk(), safety=safety, sandbox_permissions="require_escalated"
        )
        assert result.action == "ask"

    def test_no_sandbox_permissions_does_not_ask(self):
        safety = _safety(mode="allow")
        result = evaluate_policy_for_shell_exec(argv=["ls"], risk=_risk(), safety=safety)
        assert result.action == "allow"


class TestShellExecAllowlist:
    def test_allowlist_hit_returns_allow(self):
        safety = _safety(allowlist=["git"])
        result = evaluate_policy_for_shell_exec(argv=["git", "status"], risk=_risk("high"), safety=safety)
        assert result.action == "allow"
        assert result.matched_rule == "git"

    def test_allowlist_prefix_with_space(self):
        safety = _safety(allowlist=["git status"])
        result = evaluate_policy_for_shell_exec(argv=["git", "status"], risk=_risk(), safety=safety)
        assert result.action == "allow"

    def test_allowlist_miss_falls_through(self):
        safety = _safety(mode="ask", allowlist=["git"])
        result = evaluate_policy_for_shell_exec(argv=["curl", "http://example.com"], risk=_risk("low"), safety=safety)
        assert result.action == "ask"


class TestShellExecModeAllow:
    def test_mode_allow_returns_allow(self):
        safety = _safety(mode="allow")
        result = evaluate_policy_for_shell_exec(argv=["ls"], risk=_risk(), safety=safety)
        assert result.action == "allow"
        assert result.matched_rule == "mode=allow"


class TestShellExecModeAsk:
    def test_risk_high_returns_ask(self):
        safety = _safety(mode="ask")
        result = evaluate_policy_for_shell_exec(argv=["sudo", "rm", "-rf", "/"], risk=_risk("high"), safety=safety)
        assert result.action == "ask"
        assert result.matched_rule == "risk=high"

    def test_risk_low_mode_ask_returns_ask(self):
        safety = _safety(mode="ask")
        result = evaluate_policy_for_shell_exec(argv=["ls"], risk=_risk("low"), safety=safety)
        assert result.action == "ask"
        assert result.matched_rule == "mode=ask"

    def test_risk_medium_mode_ask_returns_ask(self):
        safety = _safety(mode="ask")
        result = evaluate_policy_for_shell_exec(argv=["cat", "/etc/passwd"], risk=_risk("medium"), safety=safety)
        assert result.action == "ask"


# ---------------------------------------------------------------------------
# evaluate_policy_for_custom_tool
# ---------------------------------------------------------------------------

class TestCustomToolDenylist:
    def test_tool_denylist_hit_returns_deny(self):
        safety = _safety(tool_denylist=["dangerous_tool"])
        result = evaluate_policy_for_custom_tool(tool="dangerous_tool", safety=safety)
        assert result.action == "deny"
        assert result.matched_rule == "tool_denylist"

    def test_tool_denylist_miss_does_not_deny(self):
        safety = _safety(mode="allow", tool_denylist=["other_tool"])
        result = evaluate_policy_for_custom_tool(tool="safe_tool", safety=safety)
        assert result.action == "allow"

    def test_tool_denylist_takes_priority_over_mode_allow(self):
        safety = _safety(mode="allow", tool_denylist=["bad_tool"])
        result = evaluate_policy_for_custom_tool(tool="bad_tool", safety=safety)
        assert result.action == "deny"


class TestCustomToolModeDeny:
    def test_mode_deny_returns_deny(self):
        safety = _safety(mode="deny")
        result = evaluate_policy_for_custom_tool(tool="any_tool", safety=safety)
        assert result.action == "deny"
        assert result.matched_rule == "mode=deny"

    def test_mode_deny_overrides_tool_allowlist(self):
        safety = _safety(mode="deny", tool_allowlist=["any_tool"])
        result = evaluate_policy_for_custom_tool(tool="any_tool", safety=safety)
        assert result.action == "deny"


class TestCustomToolModeAllow:
    def test_mode_allow_returns_allow(self):
        safety = _safety(mode="allow")
        result = evaluate_policy_for_custom_tool(tool="my_tool", safety=safety)
        assert result.action == "allow"
        assert result.matched_rule == "mode=allow"


class TestCustomToolAllowlist:
    def test_tool_allowlist_hit_returns_allow(self):
        safety = _safety(mode="ask", tool_allowlist=["trusted_tool"])
        result = evaluate_policy_for_custom_tool(tool="trusted_tool", safety=safety)
        assert result.action == "allow"
        assert result.matched_rule == "tool_allowlist"

    def test_tool_allowlist_miss_falls_through_to_ask(self):
        safety = _safety(mode="ask", tool_allowlist=["other_tool"])
        result = evaluate_policy_for_custom_tool(tool="unknown_tool", safety=safety)
        assert result.action == "ask"


class TestCustomToolModeAskDefault:
    def test_mode_ask_default_returns_ask(self):
        safety = _safety(mode="ask")
        result = evaluate_policy_for_custom_tool(tool="some_tool", safety=safety)
        assert result.action == "ask"
        assert result.matched_rule == "mode=ask"

    def test_unknown_tool_no_lists_returns_ask(self):
        safety = _safety()  # defaults: mode=ask, empty lists
        result = evaluate_policy_for_custom_tool(tool="new_tool", safety=safety)
        assert result.action == "ask"


# ---------------------------------------------------------------------------
# PolicyDecision dataclass
# ---------------------------------------------------------------------------

class TestPolicyDecision:
    def test_matched_rule_optional_defaults_none(self):
        d = PolicyDecision(action="allow", reason="ok")
        assert d.matched_rule is None

    def test_frozen_immutable(self):
        d = PolicyDecision(action="deny", reason="blocked", matched_rule="denylist")
        with pytest.raises((AttributeError, TypeError)):
            d.action = "allow"  # type: ignore[misc]

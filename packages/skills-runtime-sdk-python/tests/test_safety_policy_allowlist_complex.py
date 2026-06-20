from __future__ import annotations

import pytest

from skills_runtime.config.loader import AgentSdkSafetyConfig
from skills_runtime.core import approval_sanitizers
from skills_runtime.safety.descriptors import ShellCommandDescriptor, parse_shellish_command_to_argv
from skills_runtime.safety.gate import SafetyGate
from skills_runtime.safety.guard import CommandRisk
from skills_runtime.safety.policy import evaluate_policy_for_shell_exec
from skills_runtime.tools.protocol import ToolCall


def _safety(
    *,
    mode: str = "ask",
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
) -> AgentSdkSafetyConfig:
    return AgentSdkSafetyConfig(
        mode=mode,  # type: ignore[arg-type]
        allowlist=list(allowlist or []),
        denylist=list(denylist or []),
    )


def _decision_for_shell_command(
    command: str,
    *,
    safety: AgentSdkSafetyConfig,
    sandbox_permissions: str | None = None,
):
    risk_raw = ShellCommandDescriptor().extract_risk({"command": command})
    return evaluate_policy_for_shell_exec(
        argv=list(risk_raw["argv"]),
        risk=CommandRisk(risk_level=str(risk_raw["risk_level"]), reason=str(risk_raw["reason"])),
        safety=safety,
        sandbox_permissions=sandbox_permissions,
    )


@pytest.mark.parametrize(
    "command",
    [
        "cat /etc/passwd;rm -rf /",
        "a&&b",
        "x|y",
        "a>b",
        "a<b",
    ],
)
def test_parse_shellish_detects_raw_metacharacters_without_spaces(command: str) -> None:
    argv, is_complex, reason = parse_shellish_command_to_argv(command)

    assert argv == []
    assert is_complex is True
    assert reason == "shell metacharacters detected"


def test_parse_shellish_empty_command_is_complex() -> None:
    argv, is_complex, reason = parse_shellish_command_to_argv("")

    assert argv == []
    assert is_complex is True
    assert reason == "empty command"


@pytest.mark.parametrize(
    ("command", "expected_argv"),
    [
        ("ls -la", ["ls", "-la"]),
        ("git push", ["git", "push"]),
    ],
)
def test_parse_shellish_simple_commands_preserve_argv(command: str, expected_argv: list[str]) -> None:
    argv, is_complex, reason = parse_shellish_command_to_argv(command)

    assert argv == expected_argv
    assert is_complex is False
    assert reason == "parsed"


@pytest.mark.parametrize(
    ("command", "allowlist"),
    [
        ("ls ; rm -rf /", ["ls"]),
        ("cat /etc/passwd;rm -rf /", ["cat"]),
    ],
)
def test_policy_ask_mode_does_not_allowlist_complex_shell_strings(
    command: str,
    allowlist: list[str],
) -> None:
    decision = _decision_for_shell_command(command, safety=_safety(mode="ask", allowlist=allowlist))

    assert decision.action == "ask"
    assert decision.matched_rule == "risk=high"


@pytest.mark.parametrize(
    ("command", "allowlist", "matched_rule"),
    [
        ("ls -la", ["ls"], "ls"),
        ("git push", ["git"], "git"),
    ],
)
def test_policy_allowlist_preserves_simple_shell_commands(
    command: str,
    allowlist: list[str],
    matched_rule: str,
) -> None:
    decision = _decision_for_shell_command(command, safety=_safety(mode="ask", allowlist=allowlist))

    assert decision.action == "allow"
    assert decision.matched_rule == matched_rule


@pytest.mark.parametrize(
    "command",
    [
        "ls ; rm -rf /",
        "cat /etc/passwd;rm -rf /",
    ],
)
def test_policy_complex_shell_strings_do_not_match_denylist_as_rm(command: str) -> None:
    decision = _decision_for_shell_command(command, safety=_safety(mode="ask", denylist=["rm"]))

    assert decision.action == "ask"
    assert decision.matched_rule == "risk=high"


def test_policy_mode_allow_still_allows_complex_shell_strings() -> None:
    decision = _decision_for_shell_command(
        "ls ; rm -rf /",
        safety=_safety(mode="allow", allowlist=["ls"]),
    )

    assert decision.action == "allow"
    assert decision.matched_rule == "mode=allow"


def test_policy_require_escalated_still_asks_before_allowlist() -> None:
    decision = _decision_for_shell_command(
        "ls -la",
        safety=_safety(mode="ask", allowlist=["ls"]),
        sandbox_permissions="require_escalated",
    )

    assert decision.action == "ask"
    assert decision.matched_rule == "sandbox"


def test_policy_high_risk_non_complex_allowlist_behavior_is_unchanged() -> None:
    decision = evaluate_policy_for_shell_exec(
        argv=["rm", "-rf", "/"],
        risk=CommandRisk(risk_level="high", reason="test high risk"),
        safety=_safety(mode="ask", allowlist=["rm"]),
    )

    assert decision.action == "allow"
    assert decision.matched_rule == "rm"


def test_gate_asks_for_allowlisted_shell_command_with_raw_metacharacter() -> None:
    gate = SafetyGate(
        safety_config=_safety(mode="ask", allowlist=["cat"]),
        get_descriptor=lambda tool: ShellCommandDescriptor(),
    )
    decision = gate.evaluate(
        ToolCall(
            call_id="c1",
            name="shell_command",
            args={"command": "cat /etc/passwd;rm -rf /"},
            raw_arguments=None,
        )
    )

    assert decision.action == "ask"
    assert decision.matched_rule == "risk=high"
    assert decision.sanitized_request["intent"] == {
        "argv": [],
        "is_complex": True,
        "reason": "shell metacharacters detected",
    }


def test_approval_sanitizer_shellish_parser_matches_descriptor_parser() -> None:
    command = "cat /etc/passwd;rm -rf /"

    assert approval_sanitizers._parse_shellish_command_to_argv(command) == parse_shellish_command_to_argv(command)
    argv, is_complex, reason = approval_sanitizers._parse_shellish_command_to_argv(command)
    assert argv == []
    assert is_complex is True
    assert reason == "shell metacharacters detected"

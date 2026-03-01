from __future__ import annotations

import json
from typing import Any, Dict

from skills_runtime.config.loader import AgentSdkSafetyConfig
from skills_runtime.safety.gate import GateDecision, SafetyGate
from skills_runtime.safety.guard import CommandRisk, evaluate_command_risk
from skills_runtime.tools.protocol import ToolCall


class _StubDescriptor:
    """最小 descriptor stub：用于验证 SafetyGate 决策流程。"""

    def __init__(
        self,
        *,
        policy_category: str,
        argv: list[str] | None = None,
        risk: CommandRisk | None = None,
        approval_summary: str = "summary",
        approval_request: Dict[str, Any] | None = None,
        event_request: Dict[str, Any] | None = None,
    ) -> None:
        self.policy_category = policy_category
        self._argv = list(argv or [])
        self._risk = risk or evaluate_command_risk(self._argv)
        self._approval_summary = approval_summary
        self._approval_request = dict(approval_request or {})
        self._event_request = dict(event_request or {})
        self.approval_called = 0
        self.event_called = 0

    def extract_risk(self, args: Dict[str, Any]) -> tuple[list[str], CommandRisk]:
        _ = args
        return list(self._argv), self._risk

    def sanitize_for_approval(
        self,
        args: Dict[str, Any],
        *,
        skills_manager: Any = None,
    ) -> tuple[str, Dict[str, Any]]:
        _ = args
        _ = skills_manager
        self.approval_called += 1
        return self._approval_summary, dict(self._approval_request)

    def sanitize_for_event(
        self,
        args: Dict[str, Any],
        *,
        skills_manager: Any = None,
    ) -> Dict[str, Any]:
        _ = args
        _ = skills_manager
        self.event_called += 1
        return dict(self._event_request)


def _mk_call(name: str, args: Dict[str, Any] | None = None) -> ToolCall:
    return ToolCall(call_id="c1", name=name, args=args or {}, raw_arguments=None)


def _mk_safety(
    *,
    mode: str = "ask",
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    tool_allowlist: list[str] | None = None,
    tool_denylist: list[str] | None = None,
) -> AgentSdkSafetyConfig:
    return AgentSdkSafetyConfig(
        mode=mode,
        allowlist=list(allowlist or []),
        denylist=list(denylist or []),
        tool_allowlist=list(tool_allowlist or []),
        tool_denylist=list(tool_denylist or []),
    )


def _mk_gate(
    *,
    safety: AgentSdkSafetyConfig,
    descriptor_by_tool: Dict[str, Any],
) -> SafetyGate:
    return SafetyGate(
        safety_config=safety,
        get_descriptor=lambda tool: descriptor_by_tool[tool],
    )


def test_shell_exec_mode_deny_returns_deny() -> None:
    safety = _mk_safety(mode="deny")
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"shell_exec": _StubDescriptor(policy_category="shell", argv=["echo", "hi"])},
    )
    decision = gate.evaluate(_mk_call("shell_exec", {"argv": ["echo", "hi"]}))
    assert decision.action == "deny"


def test_shell_exec_mode_ask_returns_ask() -> None:
    safety = _mk_safety(mode="ask")
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"shell_exec": _StubDescriptor(policy_category="shell", argv=["echo", "hi"])},
    )
    decision = gate.evaluate(_mk_call("shell_exec", {"argv": ["echo", "hi"]}))
    assert decision.action == "ask"


def test_shell_exec_allowlist_hit_returns_allow() -> None:
    safety = _mk_safety(mode="ask", allowlist=["echo"])
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"shell_exec": _StubDescriptor(policy_category="shell", argv=["echo", "hi"])},
    )
    decision = gate.evaluate(_mk_call("shell_exec", {"argv": ["echo", "hi"]}))
    assert decision.action == "allow"


def test_shell_exec_denylist_hit_returns_deny() -> None:
    safety = _mk_safety(mode="ask", denylist=["echo"])
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"shell_exec": _StubDescriptor(policy_category="shell", argv=["echo", "hi"])},
    )
    decision = gate.evaluate(_mk_call("shell_exec", {"argv": ["echo", "hi"]}))
    assert decision.action == "deny"


def test_custom_tool_mode_ask_returns_ask() -> None:
    safety = _mk_safety(mode="ask")
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"my_tool": _StubDescriptor(policy_category="custom")},
    )
    decision = gate.evaluate(_mk_call("my_tool", {"x": 1}))
    assert decision.action == "ask"


def test_custom_tool_allowlist_hit_returns_allow() -> None:
    safety = _mk_safety(mode="ask", tool_allowlist=["my_tool"])
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"my_tool": _StubDescriptor(policy_category="custom")},
    )
    decision = gate.evaluate(_mk_call("my_tool", {"x": 1}))
    assert decision.action == "allow"


def test_custom_tool_denylist_hit_returns_deny() -> None:
    safety = _mk_safety(mode="ask", tool_denylist=["my_tool"])
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"my_tool": _StubDescriptor(policy_category="custom")},
    )
    decision = gate.evaluate(_mk_call("my_tool", {"x": 1}))
    assert decision.action == "deny"


def test_file_write_mode_deny_returns_deny() -> None:
    safety = _mk_safety(mode="deny")
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"file_write": _StubDescriptor(policy_category="file")},
    )
    decision = gate.evaluate(_mk_call("file_write", {"path": "a.txt", "content": "hello"}))
    assert decision.action == "deny"


def test_file_write_mode_ask_returns_ask() -> None:
    safety = _mk_safety(mode="ask")
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"file_write": _StubDescriptor(policy_category="file")},
    )
    decision = gate.evaluate(_mk_call("file_write", {"path": "a.txt", "content": "hello"}))
    assert decision.action == "ask"


def test_file_read_policy_none_returns_allow() -> None:
    safety = _mk_safety(mode="deny")
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"file_read": _StubDescriptor(policy_category="none")},
    )
    decision = gate.evaluate(_mk_call("file_read", {"path": "a.txt"}))
    assert decision.action == "allow"


def test_build_denied_result_returns_permission_tool_result() -> None:
    safety = _mk_safety(mode="ask")
    gate = _mk_gate(
        safety=safety,
        descriptor_by_tool={"shell_exec": _StubDescriptor(policy_category="shell", argv=["echo", "hi"])},
    )
    call = _mk_call("shell_exec", {"argv": ["echo", "hi"]})
    decision = GateDecision(
        action="deny",
        reason="Tool is denied by safety.mode=deny.",
        summary="summary",
        sanitized_request={"argv": ["echo", "hi"]},
        matched_rule="mode=deny",
    )

    result = gate.build_denied_result(call, decision)
    content = json.loads(result.content)

    assert result.ok is False
    assert result.error_kind == "permission"
    assert content["ok"] is False
    assert content["data"]["tool"] == "shell_exec"
    assert content["data"]["reason"] == "mode=deny"


def test_sanitize_for_approval_delegates_to_descriptor() -> None:
    descriptor = _StubDescriptor(
        policy_category="shell",
        argv=["echo", "hi"],
        approval_summary="审批摘要",
        approval_request={"argv": ["echo", "hi"]},
    )
    gate = _mk_gate(
        safety=_mk_safety(mode="ask"),
        descriptor_by_tool={"shell_exec": descriptor},
    )

    summary, request = gate.sanitize_for_approval(_mk_call("shell_exec", {"argv": ["echo", "hi"]}))
    assert summary == "审批摘要"
    assert request == {"argv": ["echo", "hi"]}
    assert descriptor.approval_called == 1


def test_sanitize_for_event_delegates_to_descriptor() -> None:
    descriptor = _StubDescriptor(
        policy_category="shell",
        argv=["echo", "hi"],
        event_request={"argv": ["echo", "hi"], "env_keys": ["OPENAI_API_KEY"]},
    )
    gate = _mk_gate(
        safety=_mk_safety(mode="ask"),
        descriptor_by_tool={"shell_exec": descriptor},
    )

    event_args = gate.sanitize_for_event(_mk_call("shell_exec", {"argv": ["echo", "hi"]}))
    assert event_args == {"argv": ["echo", "hi"], "env_keys": ["OPENAI_API_KEY"]}
    assert descriptor.event_called == 1


def test_descriptor_lookup_exception_is_fail_closed_deny() -> None:
    safety = _mk_safety(mode="allow")

    def _boom(_: str):
        raise RuntimeError("boom")

    gate = SafetyGate(safety_config=safety, get_descriptor=_boom)
    decision = gate.evaluate(_mk_call("shell_exec", {"argv": ["echo", "hi"]}))
    assert decision.action == "deny"
    assert decision.matched_rule == "descriptor=deny"

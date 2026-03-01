from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Optional

from skills_runtime.core.agent import Agent
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.tools.protocol import ToolCall


class _Backend:
    """最小 fake backend：第一次触发 tool_call，第二次输出文本并结束。"""

    def __init__(self, tool_call: ToolCall) -> None:
        self._call = tool_call
        self._count = 0

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:  # type: ignore[override]
        _ = request
        if self._count == 0:
            self._count += 1
            yield ChatStreamEvent(type="tool_calls", tool_calls=[self._call], finish_reason="tool_calls")
            yield ChatStreamEvent(type="completed", finish_reason="tool_calls")
            return
        yield ChatStreamEvent(type="text_delta", text="ok")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


class _ApproveAll(ApprovalProvider):
    async def request_approval(  # type: ignore[override]
        self,
        *,
        request: ApprovalRequest,
        timeout_ms: Optional[int] = None,
    ) -> ApprovalDecision:
        _ = (request, timeout_ms)
        return ApprovalDecision.APPROVED


def _event_text(events: list[Any]) -> str:
    return "\n".join(e.to_json() for e in events)


def _write_overlay(tmp_path: Path, *, safety_lines: list[str]) -> Path:
    overlay = tmp_path / "runtime.yaml"
    overlay.write_text("\n".join(["config_version: 1", *safety_lines, ""]), encoding="utf-8")
    return overlay


def test_custom_tool_args_redacted_in_tool_call_requested_payload_arguments(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.1）：
    自定义/未知 tool 的 tool_call_requested.payload.arguments 默认不得包含已知 secret value 明文。
    """

    secret = "CUSTOM_TOOL_SECRET_SHOULD_NOT_LEAK"
    overlay = _write_overlay(tmp_path, safety_lines=["safety:", "  mode: deny"])

    call = ToolCall(call_id="c1", name="echo_custom", args={"token": secret, "env": {"OPENAI_API_KEY": secret}}, raw_arguments=None)
    agent = Agent(
        backend=_Backend(call),
        workspace_root=tmp_path,
        config_paths=[overlay],
        env_vars={"OPENAI_API_KEY": secret},
    )

    @agent.tool
    def echo_custom(token: str, env: dict) -> str:  # type: ignore[type-arg]
        return token

    events = list(agent.run_stream("run"))
    requested = next(e for e in events if e.type == "tool_call_requested")
    args = requested.payload.get("arguments") or {}
    assert isinstance(args, dict)

    assert args.get("token") == "<redacted>"
    assert args.get("env_keys") == ["OPENAI_API_KEY"]
    assert secret not in _event_text(events)


def test_custom_tool_args_redacted_in_llm_response_delta_tool_calls(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.2）：
    llm_response_delta(delta_type=\"tool_calls\") 必须使用与 tool_call_requested 一致的脱敏 args 表示。
    """

    secret = "CUSTOM_TOOL_SECRET_SHOULD_NOT_LEAK"
    overlay = _write_overlay(tmp_path, safety_lines=["safety:", "  mode: deny"])

    call = ToolCall(call_id="c1", name="echo_custom", args={"token": secret, "env": {"OPENAI_API_KEY": secret}}, raw_arguments=None)
    agent = Agent(
        backend=_Backend(call),
        workspace_root=tmp_path,
        config_paths=[overlay],
        env_vars={"OPENAI_API_KEY": secret},
    )

    @agent.tool
    def echo_custom(token: str, env: dict) -> str:  # type: ignore[type-arg]
        return token

    events = list(agent.run_stream("run"))
    requested = next(e for e in events if e.type == "tool_call_requested")
    req_args = requested.payload.get("arguments") or {}

    delta = next(e for e in events if e.type == "llm_response_delta" and (e.payload or {}).get("delta_type") == "tool_calls")
    calls = (delta.payload or {}).get("tool_calls") or []
    assert isinstance(calls, list) and calls
    delta_args = (calls[0] or {}).get("arguments") if isinstance(calls[0], dict) else None

    assert isinstance(req_args, dict)
    assert isinstance(delta_args, dict)
    assert delta_args == req_args
    assert secret not in _event_text(events)


def test_custom_tool_approval_requested_payload_request_is_sanitized(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.3）：
    自定义/未知 tools 的 approval_requested.payload.request 默认不得包含 secrets 明文（env values 也不允许）。
    """

    secret = "CUSTOM_TOOL_SECRET_SHOULD_NOT_LEAK"
    overlay = _write_overlay(tmp_path, safety_lines=["safety:", "  mode: ask"])

    call = ToolCall(call_id="c1", name="echo_custom", args={"token": secret, "env": {"OPENAI_API_KEY": secret}}, raw_arguments=None)
    agent = Agent(
        backend=_Backend(call),
        workspace_root=tmp_path,
        config_paths=[overlay],
        approval_provider=_ApproveAll(),
        env_vars={"OPENAI_API_KEY": secret},
    )

    @agent.tool
    def echo_custom(token: str, env: dict) -> str:  # type: ignore[type-arg]
        return token

    events = list(agent.run_stream("run"))
    approval = next(e for e in events if e.type == "approval_requested")
    req = (approval.payload or {}).get("request") or {}
    assert isinstance(req, dict)

    assert req.get("token") == "<redacted>"
    assert req.get("env_keys") == ["OPENAI_API_KEY"]
    assert "env" not in req
    assert secret not in _event_text(events)


def test_custom_tool_approval_key_differs_when_non_secret_args_change(tmp_path: Path) -> None:
    """
    回归（close-harden-safety-test-gaps / regression-test-guardrails）：
    同一 custom tool 名称下，若非敏感参数变化导致动作语义变化，
    则 approval_key 必须随之变化（避免错误复用“已审批”缓存）。
    """

    overlay = _write_overlay(tmp_path, safety_lines=["safety:", "  mode: ask"])

    call1 = ToolCall(call_id="c1", name="echo_custom", args={"x": 1}, raw_arguments=None)
    agent1 = Agent(
        backend=_Backend(call1),
        workspace_root=tmp_path,
        config_paths=[overlay],
        approval_provider=_ApproveAll(),
    )

    @agent1.tool
    def echo_custom(x: int) -> int:
        return x

    events1 = list(agent1.run_stream("run"))
    approval1 = next(e for e in events1 if e.type == "approval_requested")
    k1 = str((approval1.payload or {}).get("approval_key") or "")
    assert k1

    call2 = ToolCall(call_id="c1", name="echo_custom", args={"x": 2}, raw_arguments=None)
    agent2 = Agent(
        backend=_Backend(call2),
        workspace_root=tmp_path,
        config_paths=[overlay],
        approval_provider=_ApproveAll(),
    )

    @agent2.tool
    def echo_custom(x: int) -> int:
        return x

    events2 = list(agent2.run_stream("run"))
    approval2 = next(e for e in events2 if e.type == "approval_requested")
    k2 = str((approval2.payload or {}).get("approval_key") or "")
    assert k2

    assert k1 != k2

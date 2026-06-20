from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import pytest

from skills_runtime.agent import Agent
from skills_runtime.config.loader import load_config_dicts
from skills_runtime.core.loop_controller import LoopController
from skills_runtime.llm.chat_sse import ChatStreamEvent
from skills_runtime.llm.fake import FakeChatBackend, FakeChatCall
from skills_runtime.llm.protocol import ChatRequest
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload, ToolSpec


class _AlwaysDeny(ApprovalProvider):
    """测试用 ApprovalProvider：所有审批请求都拒绝。"""

    async def request_approval(
        self,
        *,
        request: ApprovalRequest,
        timeout_ms: Optional[int] = None,
    ) -> ApprovalDecision:
        _ = request
        _ = timeout_ms
        return ApprovalDecision.DENIED


class _SleepingBackend:
    """测试用 backend：让 wall time 预算在 streaming 期间耗尽。"""

    def __init__(self, *, sleep_sec: float) -> None:
        self.calls = 0
        self._sleep_sec = float(sleep_sec)

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        _ = request
        self.calls += 1
        await asyncio.sleep(self._sleep_sec)
        yield ChatStreamEvent(type="text_delta", text="late")
        yield ChatStreamEvent(type="completed", finish_reason="stop")


def _make_controller(*, max_turns: int | None = None) -> LoopController:
    return LoopController(
        max_steps=10,
        max_wall_time_sec=None,
        started_monotonic=time.monotonic(),
        max_turns=max_turns,
    )


def _base_cfg(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_version": 1,
        "run": run,
        "llm": {"base_url": "http://example.invalid/v1", "api_key_env": "TEST_API_KEY"},
        "models": {"planner": "planner-test", "executor": "executor-test"},
    }


def _write_overlay(
    tmp_path: Path,
    *,
    run_lines: list[str] | None = None,
    safety_lines: list[str] | None = None,
) -> Path:
    lines = ["config_version: 1"]
    if run_lines:
        lines.extend(["run:", *run_lines])
    if safety_lines:
        lines.extend(["safety:", *safety_lines])
    path = tmp_path / "runtime.overlay.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tool_call(*, call_id: str, tool: str, args: dict[str, Any] | None = None) -> ToolCall:
    payload = dict(args or {})
    return ToolCall(
        call_id=call_id,
        name=tool,
        args=payload,
        raw_arguments=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def _tool_call_backend(tool: str, args_by_turn: list[dict[str, Any]]) -> FakeChatBackend:
    calls = _tool_call_fake_calls(tool, args_by_turn)
    return FakeChatBackend(calls=calls)


def _tool_call_backend_with_final(
    tool: str,
    args_by_turn: list[dict[str, Any]],
    *,
    final_text: str,
) -> FakeChatBackend:
    calls = _tool_call_fake_calls(tool, args_by_turn)
    calls.append(
        FakeChatCall(
            events=[
                ChatStreamEvent(type="text_delta", text=final_text),
                ChatStreamEvent(type="completed", finish_reason="stop"),
            ]
        )
    )
    return FakeChatBackend(calls=calls)


def _tool_call_fake_calls(tool: str, args_by_turn: list[dict[str, Any]]) -> list[FakeChatCall]:
    calls: list[FakeChatCall] = []
    for idx, args in enumerate(args_by_turn, start=1):
        calls.append(
            FakeChatCall(
                events=[
                    ChatStreamEvent(
                        type="tool_calls",
                        tool_calls=[_tool_call(call_id=f"c{idx}", tool=tool, args=args)],
                        finish_reason="tool_calls",
                    ),
                    ChatStreamEvent(type="completed", finish_reason="tool_calls"),
                ]
            )
        )
    return calls


def _register_counting_tool(agent: Agent, *, name: str = "loop_tool") -> dict[str, int]:
    spec = ToolSpec(
        name=name,
        description="count calls",
        parameters={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "required": ["n"],
            "additionalProperties": False,
        },
    )
    called = {"n": 0}

    def handler(call: ToolCall, _ctx: Any) -> ToolResult:
        called["n"] += 1
        payload = ToolResultPayload(
            ok=True,
            stdout=str(call.args.get("n", "")),
            stderr="",
            exit_code=0,
            duration_ms=0,
            truncated=False,
            data={"n": call.args.get("n")},
        )
        return ToolResult.from_payload(payload)

    agent.register_tool(spec, handler)
    return called


def _events_for(result) -> list[Any]:  # type: ignore[no-untyped-def]
    return list(JsonlWal(Path(result.wal_locator)).iter_events())


def _failed_events(result) -> list[Any]:  # type: ignore[no-untyped-def]
    return [event for event in _events_for(result) if event.type == "run_failed"]


def _llm_request_count(result) -> int:  # type: ignore[no-untyped-def]
    return sum(1 for event in _events_for(result) if event.type == "llm_request_started")


def test_loop_controller_turn_budget_edges() -> None:
    c = _make_controller(max_turns=3)
    assert c.is_turn_budget_exceeded() is False
    c.next_turn_id()
    assert c.is_turn_budget_exceeded() is False
    c.next_turn_id()
    assert c.is_turn_budget_exceeded() is False
    c.next_turn_id()
    assert c.is_turn_budget_exceeded() is True

    assert _make_controller(max_turns=0).is_turn_budget_exceeded() is True

    unlimited = _make_controller(max_turns=None)
    for _ in range(5):
        assert unlimited.is_turn_budget_exceeded() is False
        unlimited.next_turn_id()
    assert unlimited.is_turn_budget_exceeded() is False


def test_loop_controller_tool_denial_threshold_counts_per_tool() -> None:
    c = _make_controller()

    for _ in range(4):
        assert c.record_denied_approval(tool="tool_a") is None
    assert c.denied_approvals_by_tool["tool_a"] == 4
    assert c.should_abort_due_to_repeated_denial(tool="tool_a") is False

    c.record_denied_approval(tool="tool_b")
    assert c.denied_approvals_by_tool["tool_b"] == 1
    assert c.should_abort_due_to_repeated_denial(tool="tool_b") is False

    c.record_denied_approval(tool="tool_a")
    assert c.should_abort_due_to_repeated_denial(tool="tool_a") is True


def test_loop_controller_key_denial_threshold_still_works() -> None:
    c = _make_controller()

    c.record_denied_approval(approval_key="key_a", tool="tool_a")
    assert c.should_abort_due_to_repeated_denial(approval_key="key_a", tool="tool_a") is False

    c.record_denied_approval(approval_key="key_a", tool="tool_b")
    assert c.denied_approvals_by_key["key_a"] == 2
    assert c.should_abort_due_to_repeated_denial(approval_key="key_a", tool="tool_b") is True


def test_loop_controller_ignores_none_and_empty_denial_buckets() -> None:
    c = _make_controller()

    c.record_denied_approval(approval_key=None, tool=None)
    c.record_denied_approval(approval_key="", tool="")

    assert c.denied_approvals_by_key == {}
    assert c.denied_approvals_by_tool == {}
    assert c.should_abort_due_to_repeated_denial(approval_key=None, tool=None) is False
    assert c.should_abort_due_to_repeated_denial(approval_key="", tool="") is False


def test_config_accepts_zero_max_turns_and_rejects_negative() -> None:
    cfg = load_config_dicts([_base_cfg({"max_turns": 0})])
    assert cfg.run.max_turns == 0

    with pytest.raises(Exception):
        load_config_dicts([_base_cfg({"max_turns": -1})])


@pytest.mark.parametrize("max_turns", [3, 1])
def test_agent_turn_budget_stops_before_next_turn(tmp_path: Path, max_turns: int) -> None:
    overlay = _write_overlay(
        tmp_path,
        run_lines=[f"  max_turns: {max_turns}", "  max_steps: 40"],
        safety_lines=["  mode: ask", "  tool_allowlist:", "    - loop_tool"],
    )
    backend = _tool_call_backend("loop_tool", [{"n": idx} for idx in range(1, max_turns + 1)])
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    called = _register_counting_tool(agent)

    result = agent.run("keep calling tool")

    assert result.status == "failed"
    assert called["n"] == max_turns
    assert _llm_request_count(result) == max_turns
    failed = _failed_events(result)
    assert failed
    assert failed[-1].payload["error_kind"] == "budget_exceeded"
    assert f"max_turns={max_turns}" in failed[-1].payload["message"]


def test_agent_zero_max_turns_terminates_before_first_llm_request(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        run_lines=["  max_turns: 0", "  max_steps: 40"],
        safety_lines=["  mode: ask", "  tool_allowlist:", "    - loop_tool"],
    )
    backend = _tool_call_backend("loop_tool", [{"n": 1}])
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    called = _register_counting_tool(agent)

    result = agent.run("should stop immediately")

    assert result.status == "failed"
    assert called["n"] == 0
    events = _events_for(result)
    assert not any(event.type == "llm_request_started" for event in events)
    failed = [event for event in events if event.type == "run_failed"]
    assert failed[-1].payload["error_kind"] == "budget_exceeded"
    assert "max_turns=0" in failed[-1].payload["message"]


def test_agent_none_max_turns_does_not_limit_normal_completion(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        run_lines=["  max_turns: null", "  max_steps: 40"],
        safety_lines=["  mode: ask", "  tool_allowlist:", "    - loop_tool"],
    )
    backend = _tool_call_backend_with_final("loop_tool", [{"n": 1}, {"n": 2}], final_text="done")
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    called = _register_counting_tool(agent)

    result = agent.run("complete after two tools")

    assert result.status == "completed"
    assert result.final_output == "done"
    assert called["n"] == 2
    assert _llm_request_count(result) == 3
    assert any(event.type == "run_completed" for event in _events_for(result))


def test_policy_deny_repeated_tool_aborts_run(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        run_lines=["  max_steps: 40"],
        safety_lines=["  mode: ask", "  tool_denylist:", "    - denied_tool"],
    )
    backend = _tool_call_backend_with_final(
        "denied_tool",
        [{"n": idx} for idx in range(1, 6)],
        final_text="should-not-complete",
    )
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    called = _register_counting_tool(agent, name="denied_tool")

    result = agent.run("keep requesting denied tool")

    assert result.status == "failed"
    assert called["n"] == 0
    assert _llm_request_count(result) == 5
    events = _events_for(result)
    assert not any(event.type == "approval_requested" for event in events)
    failed = [event for event in events if event.type == "run_failed"]
    assert failed
    payload = failed[-1].payload
    assert payload["error_kind"] == "policy_denied"
    assert payload["retryable"] is False
    assert payload["wal_locator"]
    assert payload["details"]["tool"] == "denied_tool"


def test_varying_approval_denials_abort_by_tool_threshold(tmp_path: Path) -> None:
    overlay = _write_overlay(tmp_path, run_lines=["  max_steps: 40"], safety_lines=["  mode: ask"])
    backend = _tool_call_backend_with_final(
        "approval_tool",
        [{"n": idx} for idx in range(1, 6)],
        final_text="should-not-complete",
    )
    agent = Agent(
        model="fake-model",
        backend=backend,
        workspace_root=tmp_path,
        config_paths=[overlay],
        approval_provider=_AlwaysDeny(),
    )
    called = _register_counting_tool(agent, name="approval_tool")

    result = agent.run("keep requesting denied approvals")

    assert result.status == "failed"
    assert called["n"] == 0
    assert _llm_request_count(result) == 5
    events = _events_for(result)
    approval_requests = [event for event in events if event.type == "approval_requested"]
    assert len(approval_requests) == 5
    assert len({event.payload["approval_key"] for event in approval_requests}) == 5
    failed = [event for event in events if event.type == "run_failed"]
    assert failed
    assert failed[-1].payload["error_kind"] == "approval_denied"
    assert failed[-1].payload["details"]["tool"] == "approval_tool"


def test_normal_run_completes_within_max_turns(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        run_lines=["  max_turns: 3", "  max_steps: 40"],
        safety_lines=["  mode: ask", "  tool_allowlist:", "    - loop_tool"],
    )
    backend = _tool_call_backend_with_final("loop_tool", [{"n": 1}, {"n": 2}], final_text="ok")
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    called = _register_counting_tool(agent)

    result = agent.run("finish before turn budget")

    assert result.status == "completed"
    assert result.final_output == "ok"
    assert called["n"] == 2


def test_max_steps_still_limits_actual_tool_execution(tmp_path: Path) -> None:
    overlay = _write_overlay(
        tmp_path,
        run_lines=["  max_turns: 10", "  max_steps: 1"],
        safety_lines=["  mode: ask", "  tool_allowlist:", "    - loop_tool"],
    )
    backend = _tool_call_backend_with_final("loop_tool", [{"n": 1}, {"n": 2}], final_text="should-not-reach")
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])
    called = _register_counting_tool(agent)

    result = agent.run("hit step budget")

    assert result.status == "failed"
    assert called["n"] == 1
    failed = _failed_events(result)
    assert failed[-1].payload["error_kind"] == "budget_exceeded"
    assert "max_steps=1" in failed[-1].payload["message"]


def test_max_wall_time_still_limits_run(tmp_path: Path) -> None:
    overlay = _write_overlay(tmp_path, run_lines=["  max_turns: 10", "  max_wall_time_sec: 1"])
    backend = _SleepingBackend(sleep_sec=1.2)
    agent = Agent(model="fake-model", backend=backend, workspace_root=tmp_path, config_paths=[overlay])

    result = agent.run("slow request")

    assert result.status == "failed"
    assert backend.calls == 1
    failed = _failed_events(result)
    assert failed[-1].payload["error_kind"] == "budget_exceeded"
    assert "max_wall_time_sec=1" in failed[-1].payload["message"]

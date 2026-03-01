"""
工具调用编排（从 core.agent_loop 拆出）。

包含：
- tool_call_requested/tool_call_finished 事件产出
- arguments JSON 解析校验（fail-closed）
- safety gate policy（allow/deny/ask）
- approvals flow（含 session cache 与 loop guard）
- tool dispatch 与 history 回注
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Set

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest, compute_approval_key
from skills_runtime.safety.gate import SafetyGate
from skills_runtime.tools.dispatcher import ToolDispatchInputs, ToolDispatcher
from skills_runtime.tools.protocol import ToolCall, ToolResult, ToolResultPayload


async def process_pending_tool_calls(
    *,
    ctx: RunContext,
    turn_id: str,
    pending_tool_calls: Sequence[ToolCall],
    loop,
    max_steps: int,
    max_wall_time_sec: Optional[float],
    env_store: Dict[str, str],
    safety_gate: SafetyGate,
    dispatcher: ToolDispatcher,
    pending_tool_events: List[AgentEvent],
    approval_provider: Optional[ApprovalProvider],
    safety_config: Any,
    approved_for_session_keys: Set[str],
) -> bool:
    """执行 pending tool calls（含 approvals/safety），并把结果回注到 ctx.history。"""
    if not pending_tool_calls:
        return True

    tool_calls_wire = []
    for c in pending_tool_calls:
        raw_args = c.raw_arguments
        if raw_args is None:
            raw_args = json.dumps(c.args, ensure_ascii=False, separators=(",", ":"))
        tool_calls_wire.append({"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": raw_args}})
    ctx.history.append({"role": "assistant", "content": None, "tool_calls": tool_calls_wire})

    for call in pending_tool_calls:
        if loop.is_cancelled():
            ctx.emit_cancelled()
            return False
        if loop.wall_time_exceeded():
            ctx.emit_budget_exceeded(message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}")
            return False

        step_id = loop.next_step_id()

        redaction_values = list((env_store or {}).values())
        raw_arguments = (call.raw_arguments or "").strip()
        raw_arguments_len = len(raw_arguments)
        raw_arguments_sha256: Optional[str] = None
        raw_arguments_validation_error: Optional[str] = None
        if raw_arguments:
            raw_arguments_sha256 = hashlib.sha256(raw_arguments.encode("utf-8")).hexdigest()
            try:
                parsed = json.loads(raw_arguments)
                if not isinstance(parsed, dict):
                    raw_arguments_validation_error = "tool arguments must be a JSON object"
            except json.JSONDecodeError as e:
                raw_arguments_validation_error = str(e)

        ctx.emit_event(
            AgentEvent(
                type="tool_call_requested",
                timestamp=now_rfc3339(),
                run_id=ctx.run_id,
                turn_id=turn_id,
                step_id=step_id,
                payload={
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": safety_gate.sanitize_for_event(call, redaction_values=redaction_values),
                    **(
                        {
                            "arguments_valid": False,
                            "raw_arguments_len": raw_arguments_len,
                            "raw_arguments_sha256": raw_arguments_sha256,
                            "raw_arguments_error": raw_arguments_validation_error,
                        }
                        if raw_arguments_validation_error is not None
                        else {}
                    ),
                },
            )
        )

        if raw_arguments_validation_error is not None:
            validation_result = ToolResult.error_payload(
                error_kind="validation",
                stderr=f"invalid tool arguments JSON: {raw_arguments_validation_error}",
                data={"raw_arguments_len": raw_arguments_len, "raw_arguments_sha256": raw_arguments_sha256},
            )
            ctx.emit_event(
                AgentEvent(
                    type="tool_call_finished",
                    timestamp=now_rfc3339(),
                    run_id=ctx.run_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"call_id": call.call_id, "tool": call.name, "result": validation_result.details or {}},
                )
            )
            ctx.history.append({"role": "tool", "tool_call_id": call.call_id, "content": validation_result.content})
            continue

        approval_reason: Optional[str] = None
        policy_decision = safety_gate.evaluate(call)
        if policy_decision.action == "deny":
            denied_result = safety_gate.build_denied_result(call, policy_decision)
            ctx.emit_event(
                AgentEvent(
                    type="tool_call_finished",
                    timestamp=now_rfc3339(),
                    run_id=ctx.run_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"call_id": call.call_id, "tool": call.name, "result": denied_result.details or {}},
                )
            )
            ctx.history.append({"role": "tool", "tool_call_id": call.call_id, "content": denied_result.content})
            continue
        requires_approval = policy_decision.action == "ask"

        approval_key: Optional[str] = None
        if requires_approval:
            summary, request_obj = safety_gate.sanitize_for_approval(call)
            approval_key = compute_approval_key(tool=call.name, request=request_obj)

            if approval_key in approved_for_session_keys:
                decision = ApprovalDecision.APPROVED_FOR_SESSION
                approval_reason = "cached"
            else:
                ctx.emit_event(
                    AgentEvent(
                        type="approval_requested",
                        timestamp=now_rfc3339(),
                        run_id=ctx.run_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        payload={"approval_key": approval_key, "tool": call.name, "summary": summary, "request": request_obj},
                    )
                )

                if approval_provider is None:
                    decision = ApprovalDecision.DENIED
                    approval_reason = "no_provider"
                else:
                    try:
                        timeout_ms = int(getattr(safety_config, "approval_timeout_ms", 60_000) or 60_000)
                    except (TypeError, ValueError):
                        timeout_ms = 60_000
                    try:
                        decision = await asyncio.wait_for(
                            approval_provider.request_approval(
                                request=ApprovalRequest(
                                    approval_key=approval_key,
                                    tool=call.name,
                                    summary=summary,
                                    details=request_obj,
                                ),
                                timeout_ms=timeout_ms,
                            ),
                            timeout=timeout_ms / 1000.0,
                        )
                        approval_reason = "provider"
                    except asyncio.TimeoutError:
                        decision = ApprovalDecision.DENIED
                        approval_reason = "timeout"

            ctx.emit_event(
                AgentEvent(
                    type="approval_decided",
                    timestamp=now_rfc3339(),
                    run_id=ctx.run_id,
                    turn_id=turn_id,
                    step_id=step_id,
                    payload={"approval_key": approval_key, "decision": decision.value, "reason": approval_reason},
                )
            )

            if decision == ApprovalDecision.APPROVED_FOR_SESSION:
                approved_for_session_keys.add(approval_key)

            if decision == ApprovalDecision.ABORT:
                ctx.emit_cancelled()
                return False

            if decision == ApprovalDecision.DENIED:
                loop.record_denied_approval(approval_key)
                denied_payload = ToolResultPayload(
                    ok=False,
                    stdout="",
                    stderr="approval denied",
                    exit_code=None,
                    duration_ms=0,
                    truncated=False,
                    data={"tool": call.name},
                    error_kind="permission",
                    retryable=False,
                    retry_after_ms=None,
                )
                denied_result = ToolResult.from_payload(denied_payload, message="approval denied")
                ctx.emit_event(
                    AgentEvent(
                        type="tool_call_finished",
                        timestamp=now_rfc3339(),
                        run_id=ctx.run_id,
                        turn_id=turn_id,
                        step_id=step_id,
                        payload={"call_id": call.call_id, "tool": call.name, "result": denied_result.details or {}},
                    )
                )
                ctx.history.append({"role": "tool", "tool_call_id": call.call_id, "content": denied_result.content})

                if approval_reason == "no_provider":
                    ctx.emit_event(
                        AgentEvent(
                            type="run_failed",
                            timestamp=now_rfc3339(),
                            run_id=ctx.run_id,
                            payload={
                                "error_kind": "config_error",
                                "message": f"ApprovalProvider is required for tool '{call.name}' but none is configured.",
                                "retryable": False,
                                "wal_locator": ctx.wal_locator,
                                "details": {"tool": call.name, "approval_key": approval_key, "reason": approval_reason},
                            },
                        )
                    )
                    return False

                if loop.should_abort_due_to_repeated_denial(approval_key=approval_key):
                    ctx.emit_event(
                        AgentEvent(
                            type="run_failed",
                            timestamp=now_rfc3339(),
                            run_id=ctx.run_id,
                            payload={
                                "error_kind": "approval_denied",
                                "message": "Approval was denied repeatedly for the same action; aborting to prevent an infinite loop.",
                                "retryable": False,
                                "wal_locator": ctx.wal_locator,
                                "details": {"tool": call.name, "approval_key": approval_key, "reason": approval_reason},
                            },
                        )
                    )
                    return False
                continue

        if loop.is_cancelled():
            ctx.emit_cancelled()
            return False

        if not loop.try_consume_tool_step():
            ctx.emit_budget_exceeded(message=f"budget exceeded: max_steps={max_steps}")
            return False

        result = dispatcher.dispatch_one(
            inputs=ToolDispatchInputs(call=call, run_id=ctx.run_id, turn_id=turn_id, step_id=step_id),
            pending_tool_events=pending_tool_events,
            emit_event=ctx.emit_event,
            emit_stream=ctx.wal_emitter.stream_only,
        )

        ctx.history.append({"role": "tool", "tool_call_id": call.call_id, "content": result.content})

    return True


__all__ = ["process_pending_tool_calls"]

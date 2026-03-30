"""工具调用编排（从 core.agent_loop 拆出）。

包含：
- tool_call_requested/tool_call_finished 事件产出
- arguments JSON 解析校验（fail-closed）
- safety gate policy（allow/deny/ask）
- approvals flow（含 session cache 与 loop guard）
- tool dispatch 与 history 回注（Fix 5：两阶段 approval 串行 + dispatch asyncio.gather）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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
    approval_provider: Optional[ApprovalProvider],
    safety_config: Any,
    approved_for_session_keys: Set[str],
) -> bool:
    """
    执行 pending tool calls（含 approvals/safety），并把结果回注到 ctx.history。

    Fix 5：两阶段实现：
    - Phase 1（串行）：validation + safety gate + approvals → 收集 approved_batch
      denied/invalid 结果直接写入 history（顺序保持）。
    - Phase 2（asyncio.gather）：批量派发 approved_batch。
      同步 handler 在单线程 event loop 中仍为顺序执行；
      当 handler 变为 async 时，gather 自动获得并发语义。
    - Phase 3：把 approved 结果按原始 call 顺序写入 history。
    """
    if not pending_tool_calls:
        return True

    tool_calls_wire = []
    for c in pending_tool_calls:
        raw_args = c.raw_arguments
        if raw_args is None:
            raw_args = json.dumps(c.args, ensure_ascii=False, separators=(",", ":"))
        tool_calls_wire.append({"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": raw_args}})
    ctx.history.append({"role": "assistant", "content": None, "tool_calls": tool_calls_wire})

    # ── Phase 1：串行 validation + safety + approvals ────────────────────────
    # approved_batch: calls that passed all gates, to be dispatched in Phase 2
    # denied_results: call_id → ToolResult for calls already handled in Phase 1
    approved_batch: List[Tuple[ToolCall, str]] = []  # (call, step_id)
    denied_results: Dict[str, ToolResult] = {}

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

        extra_payload: Dict[str, Any] = {}
        if raw_arguments_validation_error is not None:
            extra_payload = {
                "arguments_valid": False,
                "raw_arguments_len": raw_arguments_len,
                "raw_arguments_sha256": raw_arguments_sha256,
                "raw_arguments_error": raw_arguments_validation_error,
            }

        ctx.emit_event(
            AgentEvent(
                type="tool_call_requested",
                timestamp=now_rfc3339(),
                run_id=ctx.run_id,
                turn_id=turn_id,
                step_id=step_id,
                payload={
                    "call_id": call.call_id,
                    "tool": call.name,
                    "name": call.name,
                    "arguments": safety_gate.sanitize_for_event(call, redaction_values=redaction_values),
                    **extra_payload,
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
            denied_results[call.call_id] = validation_result
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
            denied_results[call.call_id] = denied_result
            continue

        requires_approval = policy_decision.action == "ask"
        approval_key: Optional[str] = None

        if requires_approval:
            summary, request_obj = safety_gate.sanitize_for_approval(call, redaction_values=redaction_values)
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
                denied_results[call.call_id] = denied_result

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

        # 通过所有检查：加入 approved_batch 等待 Phase 2 派发
        approved_batch.append((call, step_id))

    # ── Phase 2：asyncio.gather 并发派发 approved_batch ──────────────────────
    # 同步 handler 在单线程 event loop 中顺序执行（无 await 点不切换）；
    # 当 handler 升级为 async 时，gather 自动提供并发语义。
    if not approved_batch:
        return True

    async def _dispatch_one_async(call: ToolCall, step_id: str) -> ToolResult:
        """
        单个 tool call 的异步派发包装（为未来 async handler 铺路）。

        关键约束：
        - 每个 dispatch 必须拥有独立的 pending_tool_events 容器；
        - 且该容器必须同时作为本次 registry.dispatch 的 event_sink；
        - 否则一个 dispatch 的 clear/flush 会污染同批次其它 dispatch，或导致工具旁路事件丢失。
        """
        local_pending_tool_events: List[AgentEvent] = []
        return dispatcher.dispatch_one(
            inputs=ToolDispatchInputs(call=call, run_id=ctx.run_id, turn_id=turn_id, step_id=step_id),
            pending_tool_events=local_pending_tool_events,
            emit_event=ctx.emit_event,
            emit_stream=ctx.wal_emitter.stream_only,
        )

    dispatch_results: List[ToolResult] = list(
        await asyncio.gather(*[_dispatch_one_async(call, step_id) for call, step_id in approved_batch])
    )

    # ── Phase 3：按原始 call 顺序写入 history ────────────────────────────────
    # denied/invalid 已在 Phase 1 写入，此处只写 approved 结果。
    approved_result_map: Dict[str, ToolResult] = {
        call.call_id: result
        for (call, _step_id), result in zip(approved_batch, dispatch_results)
    }
    approved_step_id_map: Dict[str, str] = {call.call_id: step_id for call, step_id in approved_batch}
    for call in pending_tool_calls:
        if call.call_id in denied_results:
            continue  # Phase 1 已写入
        result = approved_result_map.get(call.call_id)
        if result is not None:
            ctx.history.append({"role": "tool", "tool_call_id": call.call_id, "content": result.content})

    for call in pending_tool_calls:
        result = denied_results.get(call.call_id) or approved_result_map.get(call.call_id)
        if result is None or result.error_kind != "human_required":
            continue
        details = result.details if isinstance(result.details, dict) else None
        message = ""
        if isinstance(result.message, str) and result.message.strip():
            message = result.message.strip()
        elif isinstance(details, dict):
            stderr = details.get("stderr")
            if isinstance(stderr, str) and stderr.strip():
                message = stderr.strip()
        if not message:
            message = f"tool '{call.name}' requires human input before the run can continue"
        ctx.emit_waiting_human(
            tool=call.name,
            call_id=call.call_id,
            message=message,
            details=details,
            step_id=approved_step_id_map.get(call.call_id),
        )
        return False

    return True


__all__ = ["process_pending_tool_calls"]

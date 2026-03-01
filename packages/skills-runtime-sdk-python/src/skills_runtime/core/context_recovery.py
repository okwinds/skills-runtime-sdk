"""
上下文溢出恢复与 compaction（从 core.agent_loop 拆出）。

覆盖：
- ask-first 人类决策
- compact-first 自动压缩
- handoff 生成可复制摘要并终止本次 run
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Optional

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.llm.protocol import ChatBackend, ChatRequest
from skills_runtime.prompts.compaction import (
    SUMMARY_PREFIX_TEMPLATE_ZH,
    build_compaction_messages,
    format_history_for_compaction,
)
from skills_runtime.tools.protocol import HumanIOProvider


async def ask_human_context_recovery_choice(
    *,
    run_id: str,
    turn_id: str,
    human_io: Optional[HumanIOProvider],
    human_timeout_ms: int,
    emit_event,
) -> Optional[str]:
    """向 human_io 询问 context recovery 决策（无 human_io 时返回 None）。"""
    if human_io is None:
        return None

    call_id = f"context_recovery_{run_id}_{turn_id}"
    question = (
        "检测到 context_length_exceeded。\n\n"
        "请选择下一步：\n"
        "- compact_continue：执行一次上下文压缩并继续\n"
        "- handoff_new_run：生成可复制的 handoff 摘要，建议开新 run\n"
        "- increase_budget_continue：提高本次 run 预算后再压缩继续\n"
        "- terminate：终止本次 run\n"
    )
    choices = ["compact_continue", "handoff_new_run", "increase_budget_continue", "terminate"]
    emit_event(
        AgentEvent(
            type="human_request",
            timestamp=now_rfc3339(),
            run_id=run_id,
            turn_id=turn_id,
            payload={
                "call_id": call_id,
                "question": question,
                "choices": choices,
                "context": {"kind": "context_recovery", "mode": "ask_first"},
            },
        )
    )

    answer = await asyncio.to_thread(
        human_io.request_human_input,
        call_id=call_id,
        question=question,
        choices=choices,
        context={"kind": "context_recovery", "mode": "ask_first"},
        timeout_ms=human_timeout_ms,
    )
    ans = str(answer or "").strip()
    emit_event(
        AgentEvent(
            type="human_response",
            timestamp=now_rfc3339(),
            run_id=run_id,
            turn_id=turn_id,
            payload={"call_id": call_id, "answer": ans},
        )
    )
    return ans


async def perform_compaction_turn_and_rebuild_history(
    *,
    backend: ChatBackend,
    executor_model: str,
    ctx: RunContext,
    task: str,
    reason: str,
    turn_id: str,
) -> str:
    """执行一次 compaction turn，并用摘要重建 history（返回 artifact_path）。"""
    if ctx.max_compactions_per_run > 0 and ctx.compactions_performed >= ctx.max_compactions_per_run:
        raise ValueError("max compactions per run exceeded")

    transcript = format_history_for_compaction(
        ctx.history,
        max_chars=ctx.compaction_history_max_chars,
        keep_last_messages=ctx.compaction_keep_last_messages,
    )
    compaction_messages = build_compaction_messages(task=task, transcript=transcript)

    ctx.emit_event(
        AgentEvent(
            type="compaction_started",
            timestamp=now_rfc3339(),
            run_id=ctx.run_id,
            turn_id=turn_id,
            payload={"reason": str(reason or "unknown"), "mode": ctx.context_recovery_mode},
        )
    )

    summary_text = ""
    try:
        agen = backend.stream_chat(
            ChatRequest(
                model=executor_model,
                messages=compaction_messages,
                tools=None,
                temperature=0.2,
                run_id=ctx.run_id,
                turn_id=turn_id,
                extra={"purpose": "compaction"},
            )
        )
        async for ev in agen:
            t = getattr(ev, "type", None)
            if t == "text_delta":
                summary_text += str(getattr(ev, "text", "") or "")
            elif t == "completed":
                break
    except BaseException as e:
        ctx.emit_event(
            AgentEvent(
                type="compaction_failed",
                timestamp=now_rfc3339(),
                run_id=ctx.run_id,
                turn_id=turn_id,
                payload={"reason": str(reason or "unknown"), "error": str(e)},
            )
        )
        summary_text = f"(compaction failed; fallback transcript excerpt)\n\n{transcript}"

    summary_text = str(summary_text or "").strip()
    summary_full = (SUMMARY_PREFIX_TEMPLATE_ZH + "\n" + summary_text).strip() + "\n"
    artifact_path = ctx.write_text_artifact(kind="handoff_summary", content=summary_full)
    ctx.compaction_artifacts.append(artifact_path)

    ctx.compactions_performed += 1
    ctx.refresh_terminal_notices()

    sha256 = hashlib.sha256(summary_full.encode("utf-8")).hexdigest()
    ctx.emit_event(
        AgentEvent(
            type="context_compacted",
            timestamp=now_rfc3339(),
            run_id=ctx.run_id,
            turn_id=turn_id,
            payload={
                "reason": str(reason or "unknown"),
                "count": int(ctx.compactions_performed),
                "artifact_path": artifact_path,
                "summary_len": len(summary_full),
                "summary_sha256": sha256,
            },
        )
    )

    kept_tail = []
    for m in ctx.history:
        if not isinstance(m, dict):
            continue
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        kept_tail.append({"role": m.get("role"), "content": content})
    kept_tail = (
        kept_tail[-max(0, int(ctx.compaction_keep_last_messages)) :]
        if ctx.compaction_keep_last_messages
        else []
    )

    ctx.history.clear()
    ctx.history.append({"role": "assistant", "content": summary_full})
    ctx.history.extend(kept_tail)

    ctx.emit_event(
        AgentEvent(
            type="compaction_finished",
            timestamp=now_rfc3339(),
            run_id=ctx.run_id,
            turn_id=turn_id,
            payload={"reason": str(reason or "unknown"), "count": int(ctx.compactions_performed), "artifact_path": artifact_path},
        )
    )

    return artifact_path


async def handle_context_length_exceeded(
    *,
    exc: BaseException,
    backend: ChatBackend,
    executor_model: str,
    ctx: RunContext,
    loop,
    task: str,
    turn_id: str,
    human_io: Optional[HumanIOProvider],
    human_timeout_ms: int,
) -> bool:
    """处理 ContextLengthExceeded：按模式 ask/compact/handoff 决策并决定是否继续。"""
    try:
        from skills_runtime.llm.errors import ContextLengthExceededError

        is_ctx = isinstance(exc, ContextLengthExceededError)
    except ImportError:
        is_ctx = False

    if not is_ctx:
        raise exc

    ctx.emit_event(
        AgentEvent(
            type="context_length_exceeded",
            timestamp=now_rfc3339(),
            run_id=ctx.run_id,
            turn_id=turn_id,
            payload={"mode": ctx.context_recovery_mode, "compactions": int(ctx.compactions_performed)},
        )
    )

    if ctx.context_recovery_mode == "fail_fast":
        raise exc

    effective_mode = ctx.context_recovery_mode
    decision: Optional[str] = None

    if ctx.context_recovery_mode == "ask_first":
        decision = await ask_human_context_recovery_choice(
            run_id=ctx.run_id,
            turn_id=turn_id,
            human_io=human_io,
            human_timeout_ms=human_timeout_ms,
            emit_event=ctx.emit_event,
        )
        if decision is None:
            effective_mode = ctx.ask_first_fallback_mode
            ctx.emit_event(
                AgentEvent(
                    type="context_recovery_decided",
                    timestamp=now_rfc3339(),
                    run_id=ctx.run_id,
                    turn_id=turn_id,
                    payload={"mode": "ask_first", "decision": "no_human_provider", "fallback_mode": effective_mode},
                )
            )
        else:
            ctx.emit_event(
                AgentEvent(
                    type="context_recovery_decided",
                    timestamp=now_rfc3339(),
                    run_id=ctx.run_id,
                    turn_id=turn_id,
                    payload={"mode": "ask_first", "decision": decision},
                )
            )

            if decision == "terminate":
                ctx.emit_event(
                    AgentEvent(
                        type="run_failed",
                        timestamp=now_rfc3339(),
                        run_id=ctx.run_id,
                        payload={
                            "error_kind": "terminated",
                            "message": "terminated by user decision (ask_first)",
                            "retryable": False,
                            "wal_locator": ctx.wal_locator,
                        },
                    )
                )
                return False

            if decision == "handoff_new_run":
                try:
                    handoff_artifact_path = await perform_compaction_turn_and_rebuild_history(
                        backend=backend,
                        executor_model=executor_model,
                        ctx=ctx,
                        task=task,
                        reason="context_length_exceeded",
                        turn_id=turn_id,
                    )
                except Exception as ce:
                    ctx.emit_event(
                        AgentEvent(
                            type="run_failed",
                            timestamp=now_rfc3339(),
                            run_id=ctx.run_id,
                            payload={
                                "error_kind": "context_length_exceeded",
                                "message": f"context recovery failed: {ce}",
                                "retryable": False,
                                "wal_locator": ctx.wal_locator,
                            },
                        )
                    )
                    return False

                ctx.emit_event(
                    AgentEvent(
                        type="run_completed",
                        timestamp=now_rfc3339(),
                        run_id=ctx.run_id,
                        payload={
                            "final_output": "",
                            "artifacts": list(ctx.compaction_artifacts),
                            "wal_locator": ctx.wal_locator,
                            "metadata": {
                                "notices": list(ctx.terminal_notices),
                                "handoff": {"artifact_path": handoff_artifact_path},
                            },
                        },
                    )
                )
                return False

            if decision == "increase_budget_continue":
                old_steps = int(loop.max_steps)
                loop.max_steps = int(loop.max_steps) + int(max(0, ctx.increase_budget_extra_steps))
                old_wall = loop.max_wall_time_sec
                if old_wall is not None:
                    loop.max_wall_time_sec = float(old_wall) + float(max(0, ctx.increase_budget_extra_wall_time_sec))
                ctx.emit_event(
                    AgentEvent(
                        type="budget_increased",
                        timestamp=now_rfc3339(),
                        run_id=ctx.run_id,
                        turn_id=turn_id,
                        payload={
                            "reason": "context_recovery",
                            "old": {"max_steps": old_steps, "max_wall_time_sec": old_wall},
                            "new": {"max_steps": int(loop.max_steps), "max_wall_time_sec": loop.max_wall_time_sec},
                        },
                    )
                )
                effective_mode = "compact_first"

            if decision == "compact_continue":
                effective_mode = "compact_first"

    if effective_mode == "compact_first":
        try:
            await perform_compaction_turn_and_rebuild_history(
                backend=backend,
                executor_model=executor_model,
                ctx=ctx,
                task=task,
                reason="context_length_exceeded",
                turn_id=turn_id,
            )
        except Exception as ce:
            ctx.emit_event(
                AgentEvent(
                    type="run_failed",
                    timestamp=now_rfc3339(),
                    run_id=ctx.run_id,
                    payload={
                        "error_kind": "context_length_exceeded",
                        "message": f"context recovery failed: {ce}",
                        "retryable": False,
                        "wal_locator": ctx.wal_locator,
                    },
                )
            )
            return False
        return True

    raise exc


__all__ = ["ask_human_context_recovery_choice", "perform_compaction_turn_and_rebuild_history", "handle_context_length_exceeded"]

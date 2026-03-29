"""
StreamingBridge：承接 AgentLoop 中单轮 LLM streaming 的并发/清理逻辑。

目标：
- 把 backend stream 消费、取消/超时 watcher、queue 竞争与 delta 事件归一化
  从 `agent_loop.AgentLoop._run_stream_async` 中拆出；
- 对主 loop 输出显式 `StreamOutcome`，避免再靠多层 `try/finally` 与隐式 return
  传播终态语义。
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.loop_controller import LoopController
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.llm.protocol import ChatBackend, ChatRequest
from skills_runtime.safety.gate import SafetyGate
from skills_runtime.tools.protocol import ToolCall


@dataclass(frozen=True)
class StreamOutcome:
    """
    单轮 LLM streaming 的显式输出。

    字段：
    - `assistant_text`：收集到的 assistant 文本增量
    - `pending_tool_calls`：本轮累积的 tool_calls
    - `usage_payload`：已标准化的 usage 载荷；若 provider 未提供则为 None
    - `terminal_state`：streaming 层观察到的终态（completed/cancelled/budget_exceeded/error）
    - `terminal_error`：当 `terminal_state=error` 时承载原始异常
    """

    assistant_text: str
    pending_tool_calls: List[ToolCall]
    usage_payload: Optional[Dict[str, Any]]
    terminal_state: Literal["completed", "cancelled", "budget_exceeded", "error"]
    terminal_error: Optional[BaseException]


class StreamingBridge:
    """
    单轮 LLM streaming 的内部桥接器。

    说明：
    - 负责消费 backend async iterator，并把底层 provider 事件归一化成 SDK 事件；
    - 负责处理 cancel/budget watcher 与 backend task 生命周期；
    - 不直接发出 terminal event，由上层主 loop 依据 `StreamOutcome` 统一决定。
    """

    def __init__(
        self,
        *,
        ctx: RunContext,
        loop: LoopController,
        turn_id: str,
        executor_model: str,
        safety_gate: SafetyGate,
        env_store: Dict[str, str],
    ) -> None:
        """
        创建 StreamingBridge。

        参数：
        - `ctx`：当前 run 共享上下文，用于发出 delta/usage 事件
        - `loop`：当前 run 的预算/取消控制器
        - `turn_id`：当前 turn 标识
        - `executor_model`：默认模型名（provider 未返回 model 时回退）
        - `safety_gate`：用于 tool_calls 事件参数脱敏
        - `env_store`：run-local env，用于事件脱敏
        """

        self._ctx = ctx
        self._loop = loop
        self._turn_id = turn_id
        self._executor_model = str(executor_model)
        self._safety_gate = safety_gate
        self._env_store = env_store

    async def run(self, *, backend: ChatBackend, request: ChatRequest) -> StreamOutcome:
        """
        消费一轮 backend streaming，并返回显式 `StreamOutcome`。

        行为：
        - 正常 delta / tool_calls / usage 事件会通过 `ctx.emit_event()` 发出；
        - cancel / budget / backend exception 不在此处发 terminal event，而是通过返回值上报给上层。
        """

        assistant_text = ""
        pending_tool_calls: List[ToolCall] = []
        usage_payload: Optional[Dict[str, Any]] = None

        agen = backend.stream_chat(request)
        q_backend: "asyncio.Queue[Any]" = asyncio.Queue()
        stop_event: asyncio.Event = asyncio.Event()

        async def _stop_watcher() -> None:
            """后台 watcher：每 10ms 轮询取消/超时，触发后 set stop_event。"""

            try:
                while not stop_event.is_set():
                    if self._loop.is_cancelled() or self._loop.wall_time_exceeded():
                        stop_event.set()
                        return
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                pass

        async def _consume_backend() -> None:
            """消费 provider stream，并通过队列把事件/异常传给主协程。"""

            try:
                async for item in agen:
                    await q_backend.put(item)
            except asyncio.CancelledError:
                with contextlib.suppress(Exception):
                    await agen.aclose()
                raise
            except BaseException as exc:
                await q_backend.put(exc)
            finally:
                await q_backend.put(None)

        backend_task = asyncio.create_task(_consume_backend())
        watcher_task = asyncio.create_task(_stop_watcher())
        try:
            while True:
                get_future = asyncio.create_task(q_backend.get())
                stop_future = asyncio.create_task(stop_event.wait())
                done, pending = await asyncio.wait(
                    {get_future, stop_future},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for future in pending:
                    future.cancel()
                    with contextlib.suppress(BaseException):
                        await future

                if stop_future in done:
                    if not get_future.done():
                        get_future.cancel()
                        with contextlib.suppress(BaseException):
                            await get_future
                    backend_task.cancel()
                    with contextlib.suppress(BaseException):
                        await asyncio.gather(backend_task, return_exceptions=True)
                    terminal_state: Literal["cancelled", "budget_exceeded"] = (
                        "cancelled" if self._loop.is_cancelled() else "budget_exceeded"
                    )
                    return StreamOutcome(
                        assistant_text=assistant_text,
                        pending_tool_calls=list(pending_tool_calls),
                        usage_payload=usage_payload,
                        terminal_state=terminal_state,
                        terminal_error=None,
                    )

                item = get_future.result()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    return StreamOutcome(
                        assistant_text=assistant_text,
                        pending_tool_calls=list(pending_tool_calls),
                        usage_payload=usage_payload,
                        terminal_state="error",
                        terminal_error=item,
                    )

                ev = item
                event_type = getattr(ev, "type", None)
                if event_type == "text_delta":
                    text = getattr(ev, "text", "") or ""
                    assistant_text += text
                    self._ctx.emit_event(
                        AgentEvent(
                            type="llm_response_delta",
                            timestamp=now_rfc3339(),
                            run_id=self._ctx.run_id,
                            turn_id=self._turn_id,
                            payload={"delta_type": "text", "text": text},
                        )
                    )
                    continue

                if event_type == "tool_calls":
                    calls = getattr(ev, "tool_calls", None) or []
                    pending_tool_calls.extend(calls)
                    redaction_values = list((self._env_store or {}).values())
                    self._ctx.emit_event(
                        AgentEvent(
                            type="llm_response_delta",
                            timestamp=now_rfc3339(),
                            run_id=self._ctx.run_id,
                            turn_id=self._turn_id,
                            payload={
                                "delta_type": "tool_calls",
                                "tool_calls": [
                                    {
                                        "call_id": call.call_id,
                                        "tool": call.name,
                                        "name": call.name,
                                        "arguments": self._safety_gate.sanitize_for_event(
                                            call,
                                            redaction_values=redaction_values,
                                        ),
                                    }
                                    for call in calls
                                ],
                            },
                        )
                    )
                    continue

                if event_type == "completed":
                    usage_payload = self._normalize_usage_payload(ev)
                    if usage_payload is not None:
                        self._ctx.emit_event(
                            AgentEvent(
                                type="llm_usage",
                                timestamp=now_rfc3339(),
                                run_id=self._ctx.run_id,
                                turn_id=self._turn_id,
                                payload=usage_payload,
                            )
                        )
                    break

            return StreamOutcome(
                assistant_text=assistant_text,
                pending_tool_calls=list(pending_tool_calls),
                usage_payload=usage_payload,
                terminal_state="completed",
                terminal_error=None,
            )
        finally:
            stop_event.set()
            await self._cancel_task(watcher_task)
            await self._cancel_task(backend_task)

    async def _cancel_task(self, task: "asyncio.Task[Any]") -> None:
        """best-effort 结束后台任务，避免 run 退出后残留 watcher/backend task。"""

        if task.done():
            return
        task.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(task, return_exceptions=True)

    def _normalize_usage_payload(self, ev: Any) -> Optional[Dict[str, Any]]:
        """
        把 completed 事件上的 usage 标准化为 `llm_usage` payload。

        约束：
        - 仅接受 `dict` usage；
        - 任一 token 字段非法时返回 `None`，保持 fail-closed（不发 usage）。
        """

        usage = getattr(ev, "usage", None)
        if not isinstance(usage, dict):
            return None
        try:
            input_tokens = max(int(usage.get("input_tokens") or 0), 0)
            output_tokens = max(int(usage.get("output_tokens") or 0), 0)
            total_tokens = usage.get("total_tokens")
            total_tokens = (
                max(int(total_tokens), 0)
                if total_tokens is not None
                else input_tokens + output_tokens
            )
        except (TypeError, ValueError):
            return None

        payload: Dict[str, Any] = {
            "model": str(getattr(ev, "model", None) or self._executor_model),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }
        provider = getattr(ev, "provider", None)
        if isinstance(provider, str) and provider:
            payload["provider"] = provider
        request_id = getattr(ev, "request_id", None)
        if isinstance(request_id, str) and request_id:
            payload["request_id"] = request_id
        return payload


__all__ = ["StreamOutcome", "StreamingBridge"]

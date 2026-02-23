"""
ToolDispatcher：工具派发封装（Agent Loop 使用）。

背景：
- `ToolRegistry.dispatch(...)` 负责“按 name 路由到 handler 并执行”；
- 但 Agent Loop 需要严格控制 approvals 顺序与 tool_call_* 事件的发出时机：
  - `tool_call_requested` 必须在 approvals 之前
  - `tool_call_started/finished` 必须在实际 dispatch 前后
  - 工具执行期产生的其它事件（例如某些工具内部 emit 的 debug/metrics）需要延后统一 emit，
    避免插入到 approvals 事件之间造成审计序列歧义

因此本模块提供一个薄封装，把“派发 + 事件 flush”收敛到单一入口，便于后续内核化重构。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List

from agent_sdk.core.contracts import AgentEvent
from agent_sdk.tools.protocol import ToolCall, ToolResult
from agent_sdk.tools.registry import ToolRegistry


EventEmitter = Callable[[AgentEvent], None]


@dataclass(frozen=True)
class ToolDispatchInputs:
    """
    ToolDispatcher 的一次调用输入。

    字段：
    - call：工具调用
    - run_id/turn_id/step_id：用于事件关联
    """

    call: ToolCall
    run_id: str
    turn_id: str
    step_id: str


class ToolDispatcher:
    """
    工具派发器（执行 + 控制事件序列）。

    说明：
    - `emit_event`：用于发出必须落盘的事件（通常会 append WAL 再 stream）
    - `emit_stream`：仅用于把“工具执行期旁路事件”推送到调用方（这些事件通常已由 tool ctx 写入 WAL）
    """

    def __init__(self, *, registry: ToolRegistry, now_rfc3339: Callable[[], str]) -> None:
        """创建派发器。参数见类注释。"""

        self._registry = registry
        self._now_rfc3339 = now_rfc3339

    def dispatch_one(
        self,
        *,
        inputs: ToolDispatchInputs,
        pending_tool_events: List[AgentEvent],
        emit_event: EventEmitter,
        emit_stream: EventEmitter,
    ) -> ToolResult:
        """
        执行一次 tool call，并保证事件序列稳定。

        参数：
        - inputs：ToolDispatchInputs
        - pending_tool_events：工具执行期产生的旁路事件缓冲（由 ToolExecutionContext.event_sink 写入）
        - emit_event：落盘事件出口（tool_call_started/finished）
        - emit_stream：旁路事件出口（flush pending_tool_events）

        返回：
        - ToolResult：工具执行结果
        """

        call = inputs.call

        emit_event(
            AgentEvent(
                type="tool_call_started",
                ts=self._now_rfc3339(),
                run_id=inputs.run_id,
                turn_id=inputs.turn_id,
                step_id=inputs.step_id,
                payload={"call_id": call.call_id, "tool": call.name},
            )
        )

        pending_tool_events.clear()
        result: ToolResult = self._registry.dispatch(call, turn_id=inputs.turn_id, step_id=inputs.step_id)

        # 注意：pending_tool_events 中的事件通常已经被 tool ctx 写入 WAL（ctx.emit_event），这里只负责 stream。
        for te in pending_tool_events:
            emit_stream(te)

        emit_event(
            AgentEvent(
                type="tool_call_finished",
                ts=self._now_rfc3339(),
                run_id=inputs.run_id,
                turn_id=inputs.turn_id,
                step_id=inputs.step_id,
                payload={"call_id": call.call_id, "tool": call.name, "result": result.details or {}},
            )
        )

        return result


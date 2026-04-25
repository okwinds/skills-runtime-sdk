"""
TurnOrchestrator：承接 AgentLoop 中“单轮 turn”的编排逻辑。

目标：
- 把 skill 注入、prompt 组装、LLM request emit、StreamingBridge 调用与 turn 级分流
  从 `agent_loop.AgentLoop._run_stream_async` 中拆出；
- 让主 loop 只消费显式 `TurnResult`，避免继续直接操作大量 turn-local 状态。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from skills_runtime.core.context_recovery import handle_context_length_exceeded
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.errors import FrameworkError, UserError
from skills_runtime.core.loop_controller import LoopController
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.streaming_bridge import StreamingBridge
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.llm.protocol import ChatBackend, ChatRequest
from skills_runtime.safety.gate import SafetyGate
from skills_runtime.tools.protocol import ToolCall


@dataclass(frozen=True)
class TurnResult:
    """
    单轮 turn 的显式结果。

    字段：
    - `kind`：主 loop 唯一关心的 turn 级分流信号
    - `assistant_text`：当前轮汇总出的 assistant 文本
    - `pending_tool_calls`：当前轮待执行的 tool calls
    - `terminal_state`：当 `kind="terminated"` 时承载终止原因
    """

    kind: Literal["continue_with_tools", "completed", "retry_turn", "terminated"]
    assistant_text: str = ""
    pending_tool_calls: List[ToolCall] = field(default_factory=list)
    terminal_state: Optional[str] = None


class TurnOrchestrator:
    """
    单轮 Agent turn 的内部编排器。

    说明：
    - 本类不直接发 terminal event；终态仍由外层主 loop 统一决策；
    - 本类负责把“本轮是否继续 tool / 完成 / retry / 终止”收敛为 `TurnResult`。
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        run_id: str,
        task: str,
        executor_model: str,
        human_io: Any,
        human_timeout_ms: int,
        skills_manager: Any,
        prompt_manager: Any,
        registry: Any,
        ensure_skill_env_vars: Callable[..., Any],
        bridge_factory: Callable[..., Any] = StreamingBridge,
        handle_context_length_exceeded_fn: Optional[Callable[..., Any]] = handle_context_length_exceeded,
    ) -> None:
        """
        创建 TurnOrchestrator。

        参数：
        - `workspace_root/run_id/task/executor_model`：当前 run 的固定上下文
        - `human_io/human_timeout_ms`：context recovery 所需的人类交互依赖
        - `skills_manager/prompt_manager/registry`：turn 期间注入与 prompt 组装依赖
        - `ensure_skill_env_vars`：skill env 准备函数（允许 sync/async）
        - `bridge_factory`：streaming bridge 工厂（测试可替换）
        - `handle_context_length_exceeded_fn`：context recovery 处理器（测试可替换）
        """

        self._workspace_root = Path(workspace_root).resolve()
        self._run_id = str(run_id)
        self._task = str(task)
        self._executor_model = str(executor_model)
        self._human_io = human_io
        self._human_timeout_ms = int(human_timeout_ms or 0)
        self._skills_manager = skills_manager
        self._prompt_manager = prompt_manager
        self._registry = registry
        self._ensure_skill_env_vars = ensure_skill_env_vars
        self._bridge_factory = bridge_factory
        self._handle_context_length_exceeded = handle_context_length_exceeded_fn

    async def run_turn(
        self,
        *,
        ctx: RunContext,
        loop: LoopController,
        backend: ChatBackend,
        turn_id: str,
        run_env_store: Dict[str, str],
        safety_gate: SafetyGate,
    ) -> TurnResult:
        """
        执行单轮 turn，并返回显式 `TurnResult`。

        职责：
        - resolve/inject skills
        - build prompt messages
        - emit `llm_request_started`
        - 调用 `StreamingBridge`
        - 处理 context recovery 与 turn 级分流
        """

        injected: List[tuple[Any, str, Optional[str]]] = []
        try:
            resolved = self._skills_manager.resolve_mentions(self._task)
        except (FrameworkError, UserError):
            resolved = []

        for skill, mention in resolved:
            should_inject = getattr(self._prompt_manager, "should_inject_skill", None)
            if callable(should_inject) and not should_inject(
                skill,
                mention.mention_text,
                task=self._task,
                user_input=None,
            ):
                continue
            ok_to_inject = await self._maybe_await(
                self._ensure_skill_env_vars(
                    skill,
                    env_store=run_env_store,
                    run_id=self._run_id,
                    turn_id=turn_id,
                    emit=ctx.emit_event,
                )
            )
            if not ok_to_inject:
                continue
            injected.append((skill, "mention", mention.mention_text))
            payload = {
                "skill_name": skill.skill_name,
                "skill_path": str(skill.path or skill.locator),
                "namespace": str(mention.namespace),
                "skill_locator": str(skill.locator),
                "source": "mention",
                "mention_text": mention.mention_text,
            }
            if getattr(skill, "space_id", ""):
                payload["space_id"] = str(skill.space_id)
            if getattr(skill, "source_id", ""):
                payload["source_id"] = str(skill.source_id)
            ctx.emit_event(
                AgentEvent(
                    type="skill_injected",
                    timestamp=now_rfc3339(),
                    run_id=self._run_id,
                    turn_id=turn_id,
                    payload=payload,
                )
            )

        tools = self._registry.list_specs()
        filter_tools = getattr(self._prompt_manager, "filter_tools_for_task", None)
        if callable(filter_tools):
            provider_tools = filter_tools(
                tools,
                task=self._task,
                user_input=None,
            )
        else:
            provider_tools = list(tools)
        messages, _prompt_debug = self._prompt_manager.build_messages(
            task=self._task,
            cwd=str(self._workspace_root),
            tools=provider_tools,
            skills_manager=self._skills_manager,
            injected_skills=injected,
            history=ctx.history,
            user_input=None,
        )
        ctx.emit_event(
            AgentEvent(
                type="llm_request_started",
                timestamp=now_rfc3339(),
                run_id=self._run_id,
                turn_id=turn_id,
                payload={
                    "model": self._executor_model,
                    "wire_api": "chat.completions",
                    "messages_count": len(messages),
                    "tools_count": len(provider_tools),
                },
            )
        )

        def _on_retry(info: Dict[str, Any]) -> None:
            """将 backend 重试调度信息转成可观测事件（best-effort）。"""

            try:
                ctx.emit_event(
                    AgentEvent(
                        type="llm_retry_scheduled",
                        timestamp=now_rfc3339(),
                        run_id=self._run_id,
                        turn_id=turn_id,
                        payload=dict(info or {}),
                    )
                )
            except Exception:
                pass

        bridge = self._bridge_factory(
            ctx=ctx,
            loop=loop,
            turn_id=turn_id,
            executor_model=self._executor_model,
            safety_gate=safety_gate,
            env_store=run_env_store,
        )
        try:
            outcome = await bridge.run(
                backend=backend,
                request=ChatRequest(
                    model=self._executor_model,
                    messages=messages,
                    tools=provider_tools,
                    run_id=self._run_id,
                    turn_id=turn_id,
                    extra={"on_retry": _on_retry},
                ),
            )
            if outcome.terminal_state == "error":
                raise outcome.terminal_error or RuntimeError("turn orchestrator bridge failed")
        except BaseException as exc:
            if self._handle_context_length_exceeded is None:
                raise
            retry = await self._handle_context_length_exceeded(
                exc=exc,
                backend=backend,
                executor_model=self._executor_model,
                ctx=ctx,
                loop=loop,
                task=self._task,
                turn_id=turn_id,
                human_io=self._human_io,
                human_timeout_ms=self._human_timeout_ms,
            )
            if retry:
                return TurnResult(kind="retry_turn")
            return TurnResult(kind="terminated", terminal_state="context_recovery_terminated")

        if outcome.terminal_state in ("cancelled", "budget_exceeded"):
            return TurnResult(kind="terminated", terminal_state=str(outcome.terminal_state))
        if outcome.pending_tool_calls:
            return TurnResult(
                kind="continue_with_tools",
                assistant_text=str(outcome.assistant_text or ""),
                pending_tool_calls=list(outcome.pending_tool_calls),
            )
        return TurnResult(
            kind="completed",
            assistant_text=str(outcome.assistant_text or ""),
            pending_tool_calls=[],
        )

    async def _maybe_await(self, value: Any) -> Any:
        """兼容 sync/async 回调，必要时等待结果。"""

        if inspect.isawaitable(value):
            return await value
        return value


__all__ = ["TurnResult", "TurnOrchestrator"]

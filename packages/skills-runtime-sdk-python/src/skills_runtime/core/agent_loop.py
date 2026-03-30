"""AgentLoop（Phase 2）：对外入口与最小 run loop。"""
from __future__ import annotations
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from pydantic import BaseModel, create_model
from skills_runtime.config.loader import AgentSdkConfig
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.errors import UserError
from skills_runtime.core.exec_sessions import ExecSessionsProvider
from skills_runtime.core.executor import Executor
from skills_runtime.core.run_lifecycle import RunBootstrap
from skills_runtime.core.run_errors import _classify_run_exception
from skills_runtime.core.skill_env import ensure_skill_env_vars
from skills_runtime.core.stream_adapters import run_stream_async_iter, run_stream_sync, run_sync
from skills_runtime.core.tool_orchestration import process_pending_tool_calls
from skills_runtime.llm.protocol import ChatBackend, _validate_chat_backend_protocol
from skills_runtime.prompts.manager import PromptManager
from skills_runtime.safety.approvals import ApprovalProvider
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.models import Skill
from skills_runtime.state.wal_protocol import WalBackend
from skills_runtime.tools.protocol import HumanIOProvider, ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext, ToolRegistry
@dataclass(frozen=True)
class RunResult:
    """Agent.run 的返回结构（Phase 2 最小）。"""
    status: str  # completed|failed|cancelled|waiting_human
    final_output: str
    artifacts: List[str]
    wal_locator: str
class AgentLoop:
    """Skills Runtime SDK 对外入口（Phase 2 最小实现）。"""
    def __init__(
        self,
        *,
        workspace_root: Path, config: AgentSdkConfig, config_overlay_paths: List[str],
        profile_id: Optional[str],
        child_profile_map: Optional[Dict[str, str]] = None,
        planner_model: str, executor_model: str, backend: Optional[ChatBackend], executor: Executor,
        human_io: Optional[HumanIOProvider], approval_provider: Optional[ApprovalProvider], cancel_checker: Optional[Callable[[], bool]],
        safety: Any, approved_for_session_keys: set[str], exec_sessions: Optional[ExecSessionsProvider], collab_manager: Optional[object],
        wal_backend: Optional[WalBackend], event_hooks: Sequence[Callable[[AgentEvent], None]],
        env_store: Dict[str, str], skills_manager: SkillsManager, prompt_manager: PromptManager, extra_tools: List[Tuple[ToolSpec, Any, bool]],
    ) -> None:
        """
        初始化 AgentLoop（由 Agent 负责配置装配后注入运行态依赖）。

        说明：
        - `_approved_for_session_keys` 为 session 级缓存：用于同一 AgentLoop 实例内跨 run 复用 approvals（in-memory，进程内有效）。
        - `_env_store` 为注入基底：每次 `run()`/`run_stream()` 会复制一份 run-local env_store，避免跨 run 状态泄漏。
        """
        self._workspace_root = Path(workspace_root).resolve()
        self._config = config
        self._config_overlay_paths = list(config_overlay_paths)
        self._profile_id = str(profile_id) if profile_id is not None else None
        self._child_profile_map = dict(child_profile_map) if child_profile_map is not None else None
        self._planner_model = str(planner_model)
        self._executor_model = str(executor_model)
        self._backend = backend
        self._executor = executor
        self._human_io = human_io
        self._approval_provider = approval_provider
        self._cancel_checker = cancel_checker
        self._safety = safety
        self._approved_for_session_keys = approved_for_session_keys
        self._exec_sessions = exec_sessions
        self._collab_manager = collab_manager
        self._wal_backend = wal_backend
        self._event_hooks = [h for h in event_hooks if callable(h)]
        self._env_store = env_store
        self._skills_manager = skills_manager
        self._prompt_manager = prompt_manager
        self._extra_tools = extra_tools
        # Fix 4：缓存 builtin tool names frozenset，避免每次 run 重新从 registry.list_specs() 遍历。
        # register_tool() 调用时会置 None 使缓存失效。
        self._builtin_tool_names_cache: Optional[frozenset] = None
    def tool(self, func=None, *, name: Optional[str] = None, description: Optional[str] = None):  # type: ignore[no-untyped-def]
        """注册自定义 tool（decorator）。"""
        def _register(f):  # type: ignore[no-untyped-def]
            """把一个 Python 函数注册为 tool：生成 schema 并封装为统一的 ToolResult。"""
            tool_name = name or f.__name__
            tool_desc = description or (f.__doc__ or "").strip() or f"custom tool: {tool_name}"
            fields: Dict[str, Any] = {}
            for param_name, param in inspect.signature(f).parameters.items():
                ann = param.annotation
                if ann is inspect._empty:
                    # 未显式注解时保持宽类型，避免隐式收窄到 str 导致运行时语义偏差。
                    ann = Any
                default = param.default if param.default is not inspect._empty else ...
                fields[param_name] = (ann, default)
            Model: type[BaseModel] = create_model(f"_{tool_name}_Args", **fields)  # type: ignore[call-overload]
            schema = Model.model_json_schema()
            parameters = {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
                "additionalProperties": False,
            }
            spec = ToolSpec(name=tool_name, description=tool_desc, parameters=parameters)
            def handler(call: ToolCall, _ctx: ToolExecutionContext) -> ToolResult:
                """tool handler：校验参数 → 调用函数 → 将返回值封装为 `ToolResultPayload.stdout`。"""
                try:
                    args_obj = Model.model_validate(call.args)
                except Exception as e:
                    return ToolResult.error_payload(error_kind="validation", stderr=str(e))
                try:
                    out = f(**args_obj.model_dump())
                except Exception as e:  # pragma: no cover
                    return ToolResult.error_payload(error_kind="unknown", stderr=str(e))
                payload = ToolResultPayload(
                    ok=True,
                    stdout=str(out),
                    stderr="",
                    exit_code=0,
                    duration_ms=0,
                    truncated=False,
                    data={"result": str(out)},
                    error_kind=None,
                    retryable=False,
                    retry_after_ms=None,
                )
                return ToolResult.from_payload(payload)
            self.register_tool(spec, handler, override=False)
            return f
        if func is None:
            return _register
        return _register(func)
    def register_tool(self, spec: ToolSpec, handler: Any, *, override: bool = False) -> None:
        """注册一个预构造的 `ToolSpec + handler` 到 Agent（BL-031 公开扩展点）。"""
        if not isinstance(spec, ToolSpec):
            raise UserError("spec must be a ToolSpec")
        if not isinstance(spec.name, str) or not spec.name.strip():
            raise UserError("tool spec.name must be a non-empty string")
        if not callable(handler):
            raise UserError("handler must be callable")
        tool_name = spec.name
        idx: Optional[int] = None
        for i, (s, _h, _o) in enumerate(self._extra_tools):
            if s.name == tool_name:
                idx = i
                break
        if idx is not None and not override:
            raise UserError(f"重复注册 tool：{tool_name}")
        entry = (spec, handler, bool(override))
        if idx is None:
            self._extra_tools.append(entry)
        else:
            self._extra_tools[idx] = entry
        # 新工具注册后，builtin_tool_names 集合不变，但为保持语义一致性强制失效
        self._builtin_tool_names_cache = None
    async def _ensure_skill_env_vars(  # type: ignore[no-untyped-def]
        self,
        skill: Skill,
        *,
        env_store: Dict[str, str],
        run_id: str,
        turn_id: str,
        emit,
    ) -> bool:
        """确保某个 skill 所需的 env vars 已就绪（可能触发 human_io）。"""
        return await ensure_skill_env_vars(
            skill,
            config=self._config,
            env_store=env_store,
            human_io=self._human_io,
            run_id=run_id,
            turn_id=turn_id,
            emit=emit,
        )
    def run(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> RunResult:
        """同步运行任务并返回汇总结果（Phase 2：通过消费 `run_stream(...)` 得到最终输出）。"""
        summary = run_sync(self, task, run_id=run_id, initial_history=initial_history)
        return RunResult(status=summary.status, final_output=summary.final_output, artifacts=[], wal_locator=summary.wal_locator)
    def run_stream(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[AgentEvent]:
        """同步事件流接口（Iterator[AgentEvent]）。"""
        yield from run_stream_sync(self, task, run_id=run_id, initial_history=initial_history)
    async def run_stream_async(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[AgentEvent]:
        """异步事件流接口（给 Web/SSE 适配层使用）。"""
        async for item in run_stream_async_iter(self, task, run_id=run_id, initial_history=initial_history):
            yield item
    async def _run_stream_async(  # type: ignore[no-untyped-def]
        self,
        task: str,
        *,
        run_id: Optional[str],
        initial_history: Optional[List[Dict[str, Any]]],
        emit,
    ) -> None:
        """核心 run loop（async）。"""
        session = RunBootstrap(
            workspace_root=self._workspace_root,
            config=self._config,
            config_overlay_paths=self._config_overlay_paths,
            profile_id=self._profile_id,
            child_profile_map=self._child_profile_map,
            planner_model=self._planner_model,
            executor_model=self._executor_model,
            backend=self._backend,
            executor=self._executor,
            human_io=self._human_io,
            cancel_checker=self._cancel_checker,
            safety=self._safety,
            approved_for_session_keys=self._approved_for_session_keys,
            exec_sessions=self._exec_sessions,
            collab_manager=self._collab_manager,
            wal_backend=self._wal_backend,
            event_hooks=self._event_hooks,
            env_store=self._env_store,
            skills_manager=self._skills_manager,
            prompt_manager=self._prompt_manager,
            extra_tools=self._extra_tools,
            builtin_tool_names_cache=self._builtin_tool_names_cache,
            ensure_skill_env_vars=self._ensure_skill_env_vars,
            classify_run_exception=_classify_run_exception,
        ).build(task=task, run_id=run_id, initial_history=initial_history, emit=emit)
        self._builtin_tool_names_cache = session.builtin_tool_names

        backend = session.backend
        try:
            if backend is None:
                session.finalizer.emit_failed(ValueError("未配置 LLM backend（backend=None）"))
                return
            try:
                _validate_chat_backend_protocol(backend)
            except BaseException as e:
                session.finalizer.emit_failed(e)
                return
            while True:
                if session.loop.is_cancelled():
                    session.finalizer.emit_cancelled()
                    return
                if session.loop.wall_time_exceeded():
                    session.finalizer.emit_budget_exceeded(
                        message=f"budget exceeded: max_wall_time_sec={session.loop.max_wall_time_sec}"
                    )
                    return
                turn_id = session.loop.next_turn_id()
                result = await session.turn_orchestrator.run_turn(
                    ctx=session.ctx,
                    loop=session.loop,
                    backend=backend,
                    turn_id=turn_id,
                    run_env_store=session.run_env_store,
                    safety_gate=session.safety_gate,
                )
                if result.kind == "retry_turn":
                    continue
                if result.kind == "terminated":
                    if result.terminal_state == "cancelled":
                        session.finalizer.emit_cancelled()
                    elif result.terminal_state == "budget_exceeded":
                        session.finalizer.emit_budget_exceeded(
                            message=f"budget exceeded: max_wall_time_sec={session.loop.max_wall_time_sec}"
                        )
                    return
                assistant_text = result.assistant_text
                pending_tool_calls = list(result.pending_tool_calls)
                if pending_tool_calls:
                    ok = await process_pending_tool_calls(
                        ctx=session.ctx,
                        turn_id=turn_id,
                        pending_tool_calls=pending_tool_calls,
                        loop=session.loop,
                        max_steps=session.loop.max_steps,
                        max_wall_time_sec=session.loop.max_wall_time_sec,
                        env_store=session.run_env_store,
                        safety_gate=session.safety_gate,
                        dispatcher=session.dispatcher,
                        approval_provider=self._approval_provider,
                        safety_config=self._safety,
                        approved_for_session_keys=self._approved_for_session_keys,
                    )
                    if not ok:
                        return
                    continue
                if assistant_text:
                    session.ctx.history.append({"role": "assistant", "content": assistant_text})
                session.finalizer.emit_completed(final_output=assistant_text)
                return
        except BaseException as e:
            session.finalizer.emit_failed(e)
            return
        finally:
            session.finalizer.merge_new_env_vars()

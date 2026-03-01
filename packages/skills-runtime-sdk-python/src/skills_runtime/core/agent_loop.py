"""AgentLoop（Phase 2）：对外入口与最小 run loop。"""
from __future__ import annotations
import asyncio
import contextlib
import inspect
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from pydantic import BaseModel, create_model
from skills_runtime.config.loader import AgentSdkConfig
from skills_runtime.core.context_recovery import handle_context_length_exceeded
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.errors import FrameworkError, UserError
from skills_runtime.core.exec_sessions import ExecSessionsProvider
from skills_runtime.core.executor import Executor
from skills_runtime.core.loop_controller import LoopController
from skills_runtime.core.resume_builder import prepare_resume
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.run_errors import _classify_run_exception
from skills_runtime.core.skill_env import ensure_skill_env_vars
from skills_runtime.core.stream_adapters import run_stream_async_iter, run_stream_sync, run_sync
from skills_runtime.core.tool_orchestration import process_pending_tool_calls
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.llm.protocol import ChatBackend, ChatRequest, _validate_chat_backend_protocol
from skills_runtime.prompts.manager import PromptManager
from skills_runtime.safety.approvals import ApprovalProvider
from skills_runtime.safety.descriptors import get_builtin_tool_safety_descriptor
from skills_runtime.safety.gate import SafetyGate
from skills_runtime.sandbox import create_default_os_sandbox_adapter
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.skills.models import Skill
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.state.wal_emitter import WalEmitter
from skills_runtime.state.wal_protocol import WalBackend
from skills_runtime.tools.builtin import register_builtin_tools
from skills_runtime.tools.dispatcher import ToolDispatcher
from skills_runtime.tools.protocol import HumanIOProvider, ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext, ToolRegistry
@dataclass(frozen=True)
class RunResult:
    """Agent.run 的返回结构（Phase 2 最小）。"""
    status: str  # completed|failed|cancelled
    final_output: str
    artifacts: List[str]
    wal_locator: str
class AgentLoop:
    """Skills Runtime SDK 对外入口（Phase 2 最小实现）。"""
    def __init__(
        self,
        *,
        workspace_root: Path,
        config: AgentSdkConfig,
        config_overlay_paths: List[str],
        planner_model: str,
        executor_model: str,
        backend: Optional[ChatBackend],
        executor: Executor,
        human_io: Optional[HumanIOProvider],
        approval_provider: Optional[ApprovalProvider],
        cancel_checker: Optional[Callable[[], bool]],
        safety: Any,
        approved_for_session_keys: set[str],
        exec_sessions: Optional[ExecSessionsProvider],
        collab_manager: Optional[object],
        wal_backend: Optional[WalBackend],
        event_hooks: Sequence[Callable[[AgentEvent], None]],
        env_store: Dict[str, str],
        skills_manager: SkillsManager,
        prompt_manager: PromptManager,
        extra_tools: List[Tuple[ToolSpec, Any, bool]],
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
                    ann = str
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
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        run_dir = (self._workspace_root / ".skills_runtime_sdk" / "runs" / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        wal_jsonl_path = run_dir / "events.jsonl"
        injected_wal = self._wal_backend
        if injected_wal is not None:
            wal = injected_wal
            wal_locator = f"{wal.locator()}#run_id={run_id}"
        else:
            wal = JsonlWal(wal_jsonl_path)
            wal_locator = str(wal_jsonl_path)
        wal_emitter = WalEmitter(wal=wal, stream=emit, hooks=list(self._event_hooks))
        max_steps = int(self._config.run.max_steps)
        max_wall_time_sec = self._config.run.max_wall_time_sec
        cr = self._config.run.context_recovery
        ctx = RunContext(
            run_id=run_id,
            run_dir=run_dir,
            wal=wal,
            wal_locator=wal_locator,
            wal_emitter=wal_emitter,
            history=[],
            artifacts_dir=(run_dir / "artifacts").resolve(),
            max_steps=max_steps,
            max_wall_time_sec=float(max_wall_time_sec) if max_wall_time_sec is not None else None,
            context_recovery_mode=str(cr.mode),
            max_compactions_per_run=int(cr.max_compactions_per_run),
            ask_first_fallback_mode=str(cr.ask_first_fallback_mode),
            compaction_history_max_chars=int(cr.compaction_history_max_chars),
            compaction_keep_last_messages=int(cr.compaction_keep_last_messages),
            increase_budget_extra_steps=int(cr.increase_budget_extra_steps),
            increase_budget_extra_wall_time_sec=int(cr.increase_budget_extra_wall_time_sec),
        )
        resume_strategy = str(self._config.run.resume_strategy)
        resume = prepare_resume(wal=wal, run_id=run_id, initial_history=initial_history, resume_strategy=resume_strategy)
        if resume.resume_replay_history:
            ctx.history.extend(resume.resume_replay_history)
            self._approved_for_session_keys.update(set(resume.resume_replay_approved))
        elif resume.resume_summary:
            ctx.history.append({"role": "assistant", "content": resume.resume_summary})
        if initial_history:
            for item in initial_history:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                if role not in ("user", "assistant"):
                    continue
                if not isinstance(content, str):
                    continue
                ctx.history.append({"role": role, "content": content})
        ctx.emit_event(
            AgentEvent(
                type="run_started",
                timestamp=now_rfc3339(),
                run_id=run_id,
                payload={
                    "task": task,
                    "config_summary": {
                        "models": {"planner": self._planner_model, "executor": self._executor_model},
                        "llm": {"base_url": self._config.llm.base_url, "api_key_env": self._config.llm.api_key_env},
                        "config_overlays": list(self._config_overlay_paths),
                    },
                    "workspace_root": str(self._workspace_root),
                    "resume": {
                        "enabled": bool(resume.resume_summary) or bool(resume.resume_replay_history),
                        "strategy": resume_strategy,
                        "previous_events": resume.existing_events_count,
                    },
                },
            )
        )
        started_monotonic = time.monotonic()
        loop = LoopController(
            max_steps=max_steps,
            max_wall_time_sec=float(max_wall_time_sec) if max_wall_time_sec is not None else None,
            started_monotonic=started_monotonic,
            cancel_checker=self._cancel_checker,
            denied_approvals_by_key=dict(resume.resume_replay_denied or {}),
        )
        run_env_store: Dict[str, str] = dict(self._env_store or {})
        if self._backend is None:
            ctx.emit_event(
                AgentEvent(
                    type="run_failed",
                    timestamp=now_rfc3339(),
                    run_id=run_id,
                    payload={
                        "error_kind": "config_error",
                        "message": "未配置 LLM backend（backend=None）",
                        "retryable": False,
                        "wal_locator": wal_locator,
                    },
                )
            )
            return
        backend = self._backend
        try:
            _validate_chat_backend_protocol(backend)
        except BaseException as e:
            failed = _classify_run_exception(e).to_payload()
            failed["wal_locator"] = wal_locator
            ctx.emit_event(AgentEvent(type="run_failed", timestamp=now_rfc3339(), run_id=run_id, payload=failed))
            return
        pending_tool_events: List[AgentEvent] = []
        def _tool_event_sink(e: AgentEvent) -> None:
            """收集 tool 执行期间产生的事件（供 WAL flush / 回放）。"""
            pending_tool_events.append(e)
        tool_ctx = ToolExecutionContext(
            workspace_root=self._workspace_root,
            run_id=run_id,
            wal=None,
            event_emitter=wal_emitter,
            executor=self._executor,
            human_io=self._human_io,
            env=run_env_store,
            cancel_checker=self._cancel_checker,
            redaction_values=lambda: list(run_env_store.values()),
            sandbox_policy_default=str(self._config.sandbox.default_policy or "none").strip().lower(),
            sandbox_adapter=create_default_os_sandbox_adapter(
                mode=str(self._config.sandbox.os.mode or "auto").strip().lower(),
                seatbelt_profile=str(self._config.sandbox.os.seatbelt.profile or "").strip(),
                bubblewrap_bwrap_path=str(self._config.sandbox.os.bubblewrap.bwrap_path or "bwrap").strip(),
                bubblewrap_unshare_net=bool(self._config.sandbox.os.bubblewrap.unshare_net),
            ),
            emit_tool_events=False,
            event_sink=_tool_event_sink,
            skills_manager=self._skills_manager,
            exec_sessions=self._exec_sessions,
            collab_manager=self._collab_manager,
        )
        registry = ToolRegistry(ctx=tool_ctx)
        register_builtin_tools(registry)
        builtin_tool_names = set(s.name for s in registry.list_specs())
        for spec, handler, override in self._extra_tools:
            registry.register(spec, handler, override=bool(override))
        custom_tool_names = set(s.name for s, _h, _o in self._extra_tools)
        registered_tool_names = set(s.name for s in registry.list_specs())
        dispatcher = ToolDispatcher(registry=registry, now_rfc3339=now_rfc3339)
        safety_gate = SafetyGate(
            safety_config=self._safety,
            get_descriptor=get_builtin_tool_safety_descriptor,
            skills_manager=self._skills_manager,
            is_custom_tool=lambda tool_name: (tool_name in custom_tool_names)
            or ((tool_name in registered_tool_names) and (tool_name not in builtin_tool_names)),
        )
        try:
            while True:
                if loop.is_cancelled():
                    ctx.emit_cancelled()
                    return
                if loop.wall_time_exceeded():
                    ctx.emit_budget_exceeded(message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}")
                    return
                turn_id = loop.next_turn_id()
                injected: List[Tuple[Any, str, Optional[str]]] = []
                try:
                    resolved = self._skills_manager.resolve_mentions(task)
                except (FrameworkError, UserError):
                    resolved = []
                for skill, mention in resolved:
                    ok_to_inject = await self._ensure_skill_env_vars(
                        skill, env_store=run_env_store, run_id=run_id, turn_id=turn_id, emit=ctx.emit_event
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
                            run_id=run_id,
                            turn_id=turn_id,
                            payload=payload,
                        )
                    )
                tools = registry.list_specs()
                messages, _prompt_debug = self._prompt_manager.build_messages(
                    task=task,
                    cwd=str(self._workspace_root),
                    tools=tools,
                    skills_manager=self._skills_manager,
                    injected_skills=injected,
                    history=ctx.history,
                    user_input=None,
                )
                ctx.emit_event(
                    AgentEvent(
                        type="llm_request_started",
                        timestamp=now_rfc3339(),
                        run_id=run_id,
                        turn_id=turn_id,
                        payload={
                            "model": self._executor_model,
                            "wire_api": "chat.completions",
                            "messages_count": len(messages),
                            "tools_count": len(tools),
                        },
                    )
                )
                assistant_text = ""
                pending_tool_calls: List[ToolCall] = []
                try:
                    def _on_retry(info: Dict[str, Any]) -> None:
                        """将 backend 重试调度信息转成可观测事件（best-effort）。"""
                        try:
                            ctx.emit_event(
                                AgentEvent(
                                    type="llm_retry_scheduled",
                                    timestamp=now_rfc3339(),
                                    run_id=run_id,
                                    turn_id=turn_id,
                                    payload=dict(info or {}),
                                )
                            )
                        except Exception:
                            pass
                    agen = backend.stream_chat(
                        ChatRequest(
                            model=self._executor_model,
                            messages=messages,
                            tools=tools,
                            run_id=run_id,
                            turn_id=turn_id,
                            extra={"on_retry": _on_retry},
                        )
                    )
                    q_backend: "asyncio.Queue[Any]" = asyncio.Queue()
                    async def _consume_backend() -> None:
                        """消费 backend streaming 并写入队列（异常与 EOF 通过哨兵传递）。"""
                        try:
                            async for item in agen:
                                await q_backend.put(item)
                        except asyncio.CancelledError:
                            with contextlib.suppress(Exception):
                                await agen.aclose()
                            raise
                        except BaseException as e:
                            await q_backend.put(e)
                        finally:
                            await q_backend.put(None)
                    backend_task = asyncio.create_task(_consume_backend())
                    try:
                        while True:
                            if loop.is_cancelled():
                                backend_task.cancel()
                                with contextlib.suppress(BaseException):
                                    await asyncio.gather(backend_task, return_exceptions=True)
                                ctx.emit_cancelled()
                                return
                            if loop.wall_time_exceeded():
                                backend_task.cancel()
                                with contextlib.suppress(BaseException):
                                    await asyncio.gather(backend_task, return_exceptions=True)
                                ctx.emit_budget_exceeded(message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}")
                                return
                            try:
                                item = await asyncio.wait_for(q_backend.get(), timeout=0.05)
                            except asyncio.TimeoutError:
                                continue
                            if item is None:
                                break
                            if isinstance(item, BaseException):
                                raise item
                            ev = item
                            t = getattr(ev, "type", None)
                            if t == "text_delta":
                                text = getattr(ev, "text", "") or ""
                                assistant_text += text
                                ctx.emit_event(
                                    AgentEvent(
                                        type="llm_response_delta",
                                        timestamp=now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        payload={"delta_type": "text", "text": text},
                                    )
                                )
                            elif t == "tool_calls":
                                calls = getattr(ev, "tool_calls", None) or []
                                pending_tool_calls.extend(calls)
                                redaction_values = list((run_env_store or {}).values())
                                ctx.emit_event(
                                    AgentEvent(
                                        type="llm_response_delta",
                                        timestamp=now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        payload={
                                            "delta_type": "tool_calls",
                                            "tool_calls": [
                                                {
                                                    "call_id": c.call_id,
                                                    "name": c.name,
                                                    "arguments": safety_gate.sanitize_for_event(c, redaction_values=redaction_values),
                                                }
                                                for c in calls
                                            ],
                                        },
                                    )
                                )
                            elif t == "completed":
                                break
                    finally:
                        if not backend_task.done():
                            backend_task.cancel()
                            with contextlib.suppress(BaseException):
                                await asyncio.gather(backend_task, return_exceptions=True)
                except BaseException as e:
                    retry = await handle_context_length_exceeded(
                        exc=e,
                        backend=backend,
                        executor_model=self._executor_model,
                        ctx=ctx,
                        loop=loop,
                        task=task,
                        turn_id=turn_id,
                        human_io=self._human_io,
                        human_timeout_ms=self._config.run.human_timeout_ms,
                    )
                    if retry:
                        continue
                    return
                if pending_tool_calls:
                    ok = await process_pending_tool_calls(
                        ctx=ctx,
                        turn_id=turn_id,
                        pending_tool_calls=pending_tool_calls,
                        loop=loop,
                        max_steps=max_steps,
                        max_wall_time_sec=max_wall_time_sec,
                        env_store=run_env_store,
                        safety_gate=safety_gate,
                        dispatcher=dispatcher,
                        pending_tool_events=pending_tool_events,
                        approval_provider=self._approval_provider,
                        safety_config=self._safety,
                        approved_for_session_keys=self._approved_for_session_keys,
                    )
                    if not ok:
                        return
                    continue
                if assistant_text:
                    ctx.history.append({"role": "assistant", "content": assistant_text})
                ctx.emit_event(
                    AgentEvent(
                        type="run_completed",
                        timestamp=now_rfc3339(),
                        run_id=run_id,
                        payload={
                            "final_output": assistant_text,
                            "artifacts": list(ctx.compaction_artifacts),
                            "wal_locator": wal_locator,
                            "metadata": {"notices": list(ctx.terminal_notices)},
                        },
                    )
                )
                return
        except BaseException as e:
            failed = _classify_run_exception(e).to_payload()
            failed["wal_locator"] = wal_locator
            ctx.emit_event(
                AgentEvent(
                    type="run_failed",
                    timestamp=now_rfc3339(),
                    run_id=run_id,
                    payload=failed,
                )
            )
            return
        finally:
            self._env_store.update(run_env_store)

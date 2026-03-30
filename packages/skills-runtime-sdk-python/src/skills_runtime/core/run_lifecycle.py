"""
Run lifecycle 内部对象：RunBootstrap / RunSession / RunFinalizer。

目标：
- 从 `agent_loop.AgentLoop._run_stream_async` 中抽离 run 级装配与终态收尾；
- 让主 loop 只保留 turn 调度与 tool/final 分流；
- 不改变对外事件协议、WAL 顺序与 env-store 增量 merge 语义。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from skills_runtime.config.loader import AgentSdkConfig
from skills_runtime.core.context_recovery import handle_context_length_exceeded
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.exec_sessions import ExecSessionsProvider
from skills_runtime.core.executor import Executor
from skills_runtime.core.loop_controller import LoopController
from skills_runtime.core.resume_builder import prepare_resume
from skills_runtime.core.run_context import RunContext
from skills_runtime.core.streaming_bridge import StreamingBridge
from skills_runtime.core.turn_orchestrator import TurnOrchestrator
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.prompts.manager import PromptManager
from skills_runtime.safety.gate import SafetyGate
from skills_runtime.sandbox import create_default_os_sandbox_adapter
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.state.wal_emitter import WalEmitter
from skills_runtime.state.wal_protocol import WalBackend
from skills_runtime.tools.builtin import register_builtin_tools
from skills_runtime.tools.dispatcher import ToolDispatcher
from skills_runtime.tools.protocol import HumanIOProvider, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext, ToolRegistry


@dataclass(frozen=True)
class RunSession:
    """单次 run 的装配结果，供主 loop 消费。"""

    run_id: str
    wal_locator: str
    ctx: RunContext
    loop: LoopController
    backend: Any
    registry: ToolRegistry
    dispatcher: ToolDispatcher
    safety_gate: SafetyGate
    turn_orchestrator: TurnOrchestrator
    finalizer: "RunFinalizer"
    run_env_store: Dict[str, str]
    builtin_tool_names: frozenset[str]


class RunBootstrap:
    """负责单次 run 的装配与 `run_started` 发射。"""

    def __init__(
        self,
        *,
        workspace_root: Path,
        config: AgentSdkConfig,
        config_overlay_paths: List[str],
        profile_id: Optional[str],
        child_profile_map: Optional[Dict[str, str]] = None,
        planner_model: str,
        executor_model: str,
        backend: Any,
        executor: Executor,
        human_io: Optional[HumanIOProvider],
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
        builtin_tool_names_cache: Optional[frozenset[str]],
        ensure_skill_env_vars: Callable[..., Any],
        classify_run_exception: Optional[Callable[[BaseException], Any]] = None,
    ) -> None:
        """缓存单次 run 装配所需依赖，供 `build()` 创建完整运行会话。"""

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
        self._extra_tools = list(extra_tools)
        self._builtin_tool_names_cache = builtin_tool_names_cache
        self._ensure_skill_env_vars = ensure_skill_env_vars
        self._classify_run_exception = classify_run_exception

    def build(
        self,
        *,
        task: str,
        run_id: Optional[str],
        initial_history: Optional[List[Dict[str, Any]]],
        emit: Callable[[AgentEvent], None],
    ) -> RunSession:
        """装配单次 run 所需状态，并发出 `run_started`。"""

        resolved_run_id = str(run_id or f"run_{uuid.uuid4().hex}")
        run_dir = (self._workspace_root / ".skills_runtime_sdk" / "runs" / resolved_run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        wal_jsonl_path = run_dir / "events.jsonl"
        injected_wal = self._wal_backend
        if injected_wal is not None:
            wal = injected_wal
            wal_locator = f"{wal.locator()}#run_id={resolved_run_id}"
        else:
            wal = JsonlWal(wal_jsonl_path)
            wal_locator = str(wal_jsonl_path)

        wal_emitter = WalEmitter(wal=wal, stream=emit, hooks=list(self._event_hooks))
        max_steps = int(self._config.run.max_steps)
        max_wall_time_sec = self._config.run.max_wall_time_sec
        cr = self._config.run.context_recovery
        ctx = RunContext(
            run_id=resolved_run_id,
            run_dir=run_dir,
            wal=wal,
            wal_locator=wal_locator,
            wal_emitter=wal_emitter,
            history=[],
            artifacts_dir=(run_dir / "artifacts").resolve(),
            profile_id=self._profile_id,
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
        resume = prepare_resume(
            wal=wal,
            run_id=resolved_run_id,
            initial_history=initial_history,
            resume_strategy=resume_strategy,
        )
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
                run_id=resolved_run_id,
                payload={
                    "task": task,
                    "config_summary": {
                        "profile_id": self._profile_id,
                        "models": {
                            "planner": self._planner_model,
                            "executor": self._executor_model,
                        },
                        "llm": {
                            "base_url": self._config.llm.base_url,
                            "api_key_env": self._config.llm.api_key_env,
                        },
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

        loop = LoopController(
            max_steps=max_steps,
            max_wall_time_sec=float(max_wall_time_sec) if max_wall_time_sec is not None else None,
            started_monotonic=time.monotonic(),
            cancel_checker=self._cancel_checker,
            denied_approvals_by_key=dict(resume.resume_replay_denied or {}),
        )
        run_env_store: Dict[str, str] = dict(self._env_store or {})
        initial_env_keys = set(run_env_store.keys())

        tool_ctx = ToolExecutionContext(
            workspace_root=self._workspace_root,
            run_id=resolved_run_id,
            profile_id=self._profile_id,
            child_profile_map=self._child_profile_map,
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
            skills_manager=self._skills_manager,
            exec_sessions=self._exec_sessions,
            collab_manager=self._collab_manager,
        )
        registry = ToolRegistry(ctx=tool_ctx)
        register_builtin_tools(registry)
        builtin_tool_names = self._builtin_tool_names_cache
        if builtin_tool_names is None:
            builtin_tool_names = frozenset(spec.name for spec in registry.list_specs())
        for spec, handler, override in self._extra_tools:
            registry.register(spec, handler, override=bool(override))
        custom_tool_names = set(spec.name for spec, _handler, _override in self._extra_tools)
        registered_tool_names = set(spec.name for spec in registry.list_specs())

        dispatcher = ToolDispatcher(registry=registry, now_rfc3339=now_rfc3339)
        safety_gate = SafetyGate(
            safety_config=self._safety,
            get_descriptor=registry.get_descriptor,
            skills_manager=self._skills_manager,
            is_custom_tool=lambda tool_name: (tool_name in custom_tool_names)
            or ((tool_name in registered_tool_names) and (tool_name not in builtin_tool_names)),
        )
        turn_orchestrator = TurnOrchestrator(
            workspace_root=self._workspace_root,
            run_id=resolved_run_id,
            task=task,
            executor_model=self._executor_model,
            human_io=self._human_io,
            human_timeout_ms=self._config.run.human_timeout_ms,
            skills_manager=self._skills_manager,
            prompt_manager=self._prompt_manager,
            registry=registry,
            ensure_skill_env_vars=self._ensure_skill_env_vars,
            bridge_factory=StreamingBridge,
            handle_context_length_exceeded_fn=handle_context_length_exceeded,
        )
        finalizer = RunFinalizer(
            ctx=ctx,
            session_env_store=self._env_store,
            run_env_store=run_env_store,
            initial_env_keys=initial_env_keys,
            classify_run_exception=self._classify_run_exception,
        )
        return RunSession(
            run_id=resolved_run_id,
            wal_locator=wal_locator,
            ctx=ctx,
            loop=loop,
            backend=self._backend,
            registry=registry,
            dispatcher=dispatcher,
            safety_gate=safety_gate,
            turn_orchestrator=turn_orchestrator,
            finalizer=finalizer,
            run_env_store=run_env_store,
            builtin_tool_names=builtin_tool_names,
        )


class RunFinalizer:
    """负责 run 终态事件发射与 env-store 增量回写。"""

    def __init__(
        self,
        *,
        ctx: RunContext,
        session_env_store: Dict[str, str],
        run_env_store: Dict[str, str],
        initial_env_keys: set[str],
        classify_run_exception: Optional[Callable[[BaseException], Any]] = None,
    ) -> None:
        """绑定终态发射与 env-store merge 所需状态。"""

        self._ctx = ctx
        self._session_env_store = session_env_store
        self._run_env_store = run_env_store
        self._initial_env_keys = set(initial_env_keys)
        self._classify_run_exception = classify_run_exception

    def emit_completed(self, *, final_output: str) -> None:
        """发出 `run_completed`，并保持原有 payload 形状。"""

        self._ctx.emit_event(
            AgentEvent(
                type="run_completed",
                timestamp=now_rfc3339(),
                run_id=self._ctx.run_id,
                payload={
                    "final_output": final_output,
                    "artifacts": list(self._ctx.compaction_artifacts),
                    "wal_locator": self._ctx.wal_locator,
                    "metadata": {"notices": list(self._ctx.terminal_notices)},
                },
            )
        )

    def emit_failed(self, exc: BaseException) -> None:
        """按既有错误分类逻辑发出 `run_failed`。"""

        if self._classify_run_exception is None:
            raise RuntimeError("classify_run_exception is required for emit_failed")
        failed = self._classify_run_exception(exc).to_payload()
        failed["wal_locator"] = self._ctx.wal_locator
        self._ctx.emit_event(
            AgentEvent(
                type="run_failed",
                timestamp=now_rfc3339(),
                run_id=self._ctx.run_id,
                payload=failed,
            )
        )

    def emit_cancelled(self) -> None:
        """转发取消终态。"""

        self._ctx.emit_cancelled()

    def emit_budget_exceeded(self, *, message: str) -> None:
        """转发预算耗尽终态。"""

        self._ctx.emit_budget_exceeded(message=message)

    def merge_new_env_vars(self) -> None:
        """只把本次 run 新增的 env var 合并回 session 级缓存。"""

        for key, value in self._run_env_store.items():
            if key not in self._initial_env_keys:
                self._session_env_store[key] = value


__all__ = ["RunBootstrap", "RunFinalizer", "RunSession"]

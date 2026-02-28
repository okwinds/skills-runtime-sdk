"""Agent 对外门面：负责配置装配与 API 委托。"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from pydantic import BaseModel, create_model

from skills_runtime.config.defaults import load_default_config_dict
from skills_runtime.config.loader import AgentSdkConfig, load_config_dicts
from skills_runtime.core.agent_loop import AgentLoop, ChatBackend, RunResult, _sanitize_approval_request
from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.errors import UserError
from skills_runtime.core.exec_sessions import ExecSessionsProvider
from skills_runtime.core.executor import Executor
from skills_runtime.prompts.manager import PromptManager, PromptTemplates
from skills_runtime.safety.approvals import ApprovalProvider
from skills_runtime.skills.manager import SkillsManager
from skills_runtime.state.wal_protocol import WalBackend
from skills_runtime.tools.protocol import HumanIOProvider, ToolCall, ToolResult, ToolResultPayload, ToolSpec
from skills_runtime.tools.registry import ToolExecutionContext


class Agent:
    """Skills Runtime SDK 的对外入口（薄门面）。"""

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        planner_model: Optional[str] = None,
        executor_model: Optional[str] = None,
        workspace_root: Optional[Path] = None,
        skills_disabled_paths: Optional[List[Path]] = None,
        skills_manager: Optional[SkillsManager] = None,
        env_vars: Optional[Dict[str, str]] = None,
        backend: Optional[ChatBackend] = None,
        config_paths: Optional[List[Path]] = None,
        prompt_templates: Optional[PromptTemplates] = None,
        human_io: Optional[HumanIOProvider] = None,
        approval_provider: Optional[ApprovalProvider] = None,
        cancel_checker: Optional[Callable[[], bool]] = None,
        exec_sessions: Optional[ExecSessionsProvider] = None,
        collab_manager: Optional[object] = None,
        wal_backend: Optional[WalBackend] = None,
        event_hooks: Optional[Sequence[Callable[[AgentEvent], None]]] = None,
    ) -> None:
        """构造 Agent，并把运行态依赖注入 AgentLoop。"""

        self._workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self._config_overlay_paths: List[str] = []

        # 默认配置作为 base，调用方 overlays 作为增量覆盖。
        default_overlay: Dict[str, Any] = load_default_config_dict()
        overlays: List[Dict[str, Any]] = [default_overlay]
        if config_paths:
            for p in config_paths:
                pp = Path(p)
                try:
                    pp = pp.resolve()
                except OSError:
                    pp = Path(p)
                self._config_overlay_paths.append(str(pp))
                import yaml

                obj = yaml.safe_load(pp.read_text(encoding="utf-8")) or {}
                if isinstance(obj, dict):
                    overlays.append(obj)

        self._config: AgentSdkConfig = load_config_dicts(overlays)

        chosen_model = model or executor_model or self._config.models.executor
        self._planner_model = planner_model or self._config.models.planner
        self._executor_model = executor_model or chosen_model

        self._backend = backend
        self._executor = Executor()
        self._human_io = human_io
        self._approval_provider = approval_provider
        self._cancel_checker = cancel_checker
        self._safety = self._config.safety
        self._approved_for_session_keys: set[str] = set()
        self._exec_sessions = exec_sessions
        self._collab_manager = collab_manager
        self._wal_backend = wal_backend
        self._event_hooks = [h for h in (event_hooks or []) if callable(h)]
        self._env_store: Dict[str, str] = env_vars if env_vars is not None else {}

        def _resolve_under_workspace(p: Path) -> Path:
            """把相对路径解析到 workspace_root 下，保持路径语义稳定。"""

            if p.is_absolute():
                return p.resolve()
            return (self._workspace_root / p).resolve()

        self._skills_manager = skills_manager or SkillsManager(
            workspace_root=self._workspace_root,
            skills_config=self._config.skills,
        )
        self._skills_manager.scan()
        if skills_disabled_paths:
            for p in skills_disabled_paths:
                try:
                    self._skills_manager.set_enabled(_resolve_under_workspace(Path(p)), False)
                except (KeyError, ValueError, OSError):
                    continue

        def _load_builtin_prompt_template(template_name: str) -> Tuple[str, str]:
            """读取内置 prompt 模板（system/developer）。"""

            from importlib.resources import files

            base = files("skills_runtime.assets").joinpath("prompts").joinpath(template_name)
            system_text = base.joinpath("system.md").read_text(encoding="utf-8")
            developer_text = base.joinpath("developer.md").read_text(encoding="utf-8")
            return system_text, developer_text

        if prompt_templates is None:
            pcfg = self._config.prompt
            system_text: Optional[str] = pcfg.system_text
            developer_text: Optional[str] = pcfg.developer_text
            system_path: Optional[Path] = _resolve_under_workspace(Path(pcfg.system_path)) if pcfg.system_path else None
            developer_path: Optional[Path] = (
                _resolve_under_workspace(Path(pcfg.developer_path)) if pcfg.developer_path else None
            )

            if system_text is None and developer_text is None and system_path is None and developer_path is None:
                sys_t, dev_t = _load_builtin_prompt_template(pcfg.template or "default")
                prompt_templates = PromptTemplates(
                    name=str(pcfg.template or "default"),
                    version=f"builtin:{pcfg.template or 'default'}",
                    system_text=sys_t,
                    developer_text=dev_t,
                )
            else:
                prompt_templates = PromptTemplates(
                    name=str(pcfg.template or "default"),
                    version="configured",
                    system_text=system_text,
                    developer_text=developer_text,
                    system_path=system_path,
                    developer_path=developer_path,
                )

        self._prompt_manager = PromptManager(
            templates=prompt_templates,
            include_skills_list=bool(self._config.prompt.include_skills_list),
            history_max_messages=int(self._config.prompt.history.max_messages),
            history_max_chars=int(self._config.prompt.history.max_chars),
        )
        self._extra_tools: List[Tuple[ToolSpec, Any, bool]] = []

        self._loop = AgentLoop(
            workspace_root=self._workspace_root,
            config=self._config,
            config_overlay_paths=self._config_overlay_paths,
            planner_model=self._planner_model,
            executor_model=self._executor_model,
            backend=self._backend,
            executor=self._executor,
            human_io=self._human_io,
            approval_provider=self._approval_provider,
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
        )

    def tool(self, func=None, *, name: Optional[str] = None, description: Optional[str] = None):  # type: ignore[no-untyped-def]
        """注册自定义 tool（decorator）。"""

        def _register(f):  # type: ignore[no-untyped-def]
            """把函数签名转换成 ToolSpec 并完成注册。"""

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
                """执行已注册函数，并将结果封装为 ToolResult。"""

                try:
                    args_obj = Model.model_validate(call.args)
                except Exception as e:
                    # 防御性兜底：pydantic 验证失败（ValidationError 或其他）。
                    return ToolResult.error_payload(error_kind="validation", stderr=str(e))
                try:
                    out = f(**args_obj.model_dump())
                except Exception as e:  # pragma: no cover
                    # 防御性兜底：用户注册函数可能抛出任意异常。
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
        """注册一个预构造的 ToolSpec + handler 到 Agent。"""

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

    def run(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> RunResult:
        """同步运行任务并返回汇总结果。"""

        return self._loop.run(task, run_id=run_id, initial_history=initial_history)

    def run_stream(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[AgentEvent]:
        """同步事件流接口。"""

        return self._loop.run_stream(task, run_id=run_id, initial_history=initial_history)

    async def run_stream_async(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[AgentEvent]:
        """异步事件流接口。"""

        async for ev in self._loop.run_stream_async(task, run_id=run_id, initial_history=initial_history):
            yield ev

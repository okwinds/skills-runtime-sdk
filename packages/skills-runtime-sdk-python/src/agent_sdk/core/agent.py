"""
Agent（对外入口）与最小 Agent Loop（Phase 2）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/api-reference.md`
- `docs/specs/skills-runtime-sdk/docs/agent-loop.md`
- `docs/specs/skills-runtime-sdk/docs/core-contracts.md`
- `docs/specs/skills-runtime-sdk/docs/state.md`
"""

from __future__ import annotations

import asyncio
import json
import os
import inspect
import threading
import uuid
import contextlib
import hashlib
import shlex
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterator, List, Optional, Protocol, Sequence, Tuple

from pydantic import BaseModel, create_model

from agent_sdk.config.loader import AgentSdkConfig, load_config_dicts
from agent_sdk.config.defaults import load_default_config_dict
from agent_sdk.core.contracts import AgentEvent
from agent_sdk.core.errors import FrameworkError
from agent_sdk.core.exec_sessions import ExecSessionsProvider
from agent_sdk.core.executor import Executor
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.tools.builtin import register_builtin_tools
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry

from agent_sdk.prompts.manager import PromptManager, PromptTemplates
from agent_sdk.skills.manager import SkillsManager
from agent_sdk.skills.models import Skill
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest, compute_approval_key
from agent_sdk.safety.guard import evaluate_command_risk
from agent_sdk.safety.policy import evaluate_policy_for_shell_exec
from agent_sdk.tools.protocol import HumanIOProvider, ToolResultPayload
from agent_sdk.sandbox import create_default_os_sandbox_adapter


def _now_rfc3339() -> str:
    """返回当前 UTC 时间的 RFC3339 字符串（以 `Z` 结尾）。"""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _format_argv(argv: list[str]) -> str:
    """
    将 argv 格式化为可读命令串（用于 approval summary）。

    说明：
    - 仅用于展示，不用于执行
    - 使用 shlex.quote，尽量让空格/特殊字符可读
    """

    return " ".join(shlex.quote(x) for x in argv)


def _sanitize_approval_request(
    tool: str,
    *,
    args: Dict[str, Any],
    skills_manager: Optional[SkillsManager] = None,
) -> tuple[str, Dict[str, Any]]:
    """
    将 tool args 转成“可审计但不泄露 secrets”的 approval 请求表示。

    Gate：
    - 不得包含 env 值 / API key / file content 明文
    - 对 file_write：允许 content 的不可逆摘要（sha256）与 bytes
    """

    if tool == "shell_exec":
        argv_raw = args.get("argv")
        argv: list[str] = argv_raw if isinstance(argv_raw, list) and all(isinstance(x, str) for x in argv_raw) else []
        risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])

        env_keys: list[str] = []
        env_raw = args.get("env")
        if isinstance(env_raw, dict):
            env_keys = sorted(str(k) for k in env_raw.keys())

        sandbox_permissions = args.get("sandbox_permissions")
        sandbox_perm = None
        if isinstance(sandbox_permissions, str) and sandbox_permissions.strip():
            sandbox_perm = sandbox_permissions.strip()

        sandbox = args.get("sandbox")
        sandbox_policy = None
        if isinstance(sandbox, str) and sandbox.strip():
            sandbox_policy = sandbox.strip()

        req: Dict[str, Any] = {
            "argv": argv,
            "cwd": args.get("cwd"),
            "timeout_ms": args.get("timeout_ms"),
            "tty": bool(args.get("tty") or False),
            "env_keys": env_keys,
            "sandbox": sandbox_policy,
            "sandbox_permissions": sandbox_perm,
            "risk": {"risk_level": risk.risk_level, "reason": risk.reason},
        }
        justification = args.get("justification")
        if isinstance(justification, str) and justification.strip():
            req["justification"] = justification.strip()

        cmd = _format_argv(argv) if argv else "<invalid argv>"
        summary = f"授权：shell_exec 执行命令：{cmd}（risk={risk.risk_level}）"
        return summary, req

    if tool == "file_write":
        path = args.get("path")
        content = args.get("content")
        create_dirs = args.get("create_dirs")
        if create_dirs is None:
            create_dirs = args.get("mkdirs")
        create_dirs = True if create_dirs is None else bool(create_dirs)

        sandbox_permissions = args.get("sandbox_permissions")
        sandbox_perm = None
        if isinstance(sandbox_permissions, str) and sandbox_permissions.strip():
            sandbox_perm = sandbox_permissions.strip()

        bytes_count: Optional[int] = None
        sha256: Optional[str] = None
        if isinstance(content, str):
            b = content.encode("utf-8")
            bytes_count = len(b)
            sha256 = hashlib.sha256(b).hexdigest()

        req2: Dict[str, Any] = {
            "path": path,
            "create_dirs": create_dirs,
            "sandbox_permissions": sandbox_perm,
            "bytes": bytes_count,
            "content_sha256": sha256,
        }
        justification = args.get("justification")
        if isinstance(justification, str) and justification.strip():
            req2["justification"] = justification.strip()
        summary2 = f"授权：file_write 写入文件：{path}（{bytes_count if bytes_count is not None else '?'} bytes）"
        return summary2, req2

    if tool == "apply_patch":
        input_text = args.get("input")

        bytes_count: Optional[int] = None
        sha256: Optional[str] = None
        if isinstance(input_text, str):
            b = input_text.encode("utf-8")
            bytes_count = len(b)
            sha256 = hashlib.sha256(b).hexdigest()

        # best-effort：提取受影响路径（不解析完整语法，不做 I/O）
        file_paths: list[str] = []
        if isinstance(input_text, str):
            for line in input_text.splitlines():
                s = line.strip()
                for prefix in ("*** Add File: ", "*** Update File: ", "*** Delete File: "):
                    if s.startswith(prefix):
                        file_paths.append(s[len(prefix) :].strip())
                if s.startswith("*** Move to: "):
                    file_paths.append(s[len("*** Move to: ") :].strip())

        req_ap: Dict[str, Any] = {
            "file_paths": file_paths,
            "bytes": bytes_count,
            "content_sha256": sha256,
        }
        summary_ap = f"授权：apply_patch 应用补丁（{bytes_count if bytes_count is not None else '?'} bytes）"
        return summary_ap, req_ap

    if tool == "skill_exec":
        mention = args.get("skill_mention")
        action_id = args.get("action_id")
        mention_str = mention.strip() if isinstance(mention, str) else ""
        action_str = action_id.strip() if isinstance(action_id, str) else ""

        req3: Dict[str, Any] = {"skill_mention": mention_str, "action_id": action_str}

        argv: list[str] = []
        timeout_ms: Optional[int] = None
        env_keys: list[str] = []
        bundle_root: Optional[str] = None
        resolve_error: Optional[str] = None

        if skills_manager is not None and mention_str and action_str:
            try:
                resolved = skills_manager.resolve_mentions(mention_str)
                if resolved:
                    skill, _m = resolved[0]
                    if skill.path is not None:
                        bundle_root = str(Path(skill.path).parent.resolve())
                    actions = (skill.metadata or {}).get("actions")
                    if isinstance(actions, dict):
                        adef = actions.get(action_str)
                        if isinstance(adef, dict):
                            argv_raw = adef.get("argv")
                            if isinstance(argv_raw, list) and all(isinstance(x, str) for x in argv_raw):
                                argv = list(argv_raw)
                            tm = adef.get("timeout_ms")
                            if tm is not None:
                                try:
                                    timeout_ms = int(tm)
                                except Exception:
                                    timeout_ms = None
                            env_raw = adef.get("env")
                            if isinstance(env_raw, dict):
                                env_keys = sorted(str(k) for k in env_raw.keys())
            except Exception as e:
                resolve_error = str(e)

        risk = evaluate_command_risk(argv) if argv else evaluate_command_risk([""])
        req3.update(
            {
                "bundle_root": bundle_root,
                "argv": argv,
                "timeout_ms": timeout_ms,
                "env_keys": env_keys,
                "resolve_error": resolve_error,
                "risk": {"risk_level": risk.risk_level, "reason": risk.reason},
            }
        )

        # 生成可复现的 action 指纹，避免“动作内容变化但 approval_key 复用”。
        try:
            import json

            fingerprint_obj = {
                "skill_mention": mention_str,
                "action_id": action_str,
                "bundle_root": bundle_root,
                "argv": argv,
                "timeout_ms": timeout_ms,
                "env_keys": env_keys,
            }
            fingerprint = json.dumps(fingerprint_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
            req3["action_sha256"] = hashlib.sha256(fingerprint).hexdigest()
        except Exception:
            # fail-open：只影响缓存粒度，不应阻塞执行
            pass

        cmd = _format_argv(argv) if argv else "<unresolved action argv>"
        summary3 = f"授权：skill_exec 执行动作：{mention_str}#{action_str} => {cmd}（risk={risk.risk_level}）"
        return summary3, req3

    # fallback：未知 tool 仅记录 keys
    keys: list[str] = []
    if isinstance(args, dict):
        keys = sorted(str(k) for k in args.keys())
    return f"授权：{tool}", {"arguments_keys": keys}


def _sanitize_tool_call_arguments_for_event(
    tool: str,
    *,
    args: Dict[str, Any],
    redaction_values: Sequence[str] = (),
) -> Dict[str, Any]:
    """
    将 tool args 转成“可观测但不泄露 secrets”的事件表示。

    说明：
    - 该函数只用于事件/WAL（`tool_call_requested`、`llm_response_delta(tool_calls)` 等）；
    - 不影响真实执行参数；
    - 必须尽量保持可调试性（保留结构/关键字段），同时满足隐私 Gate。

    最小 Gate（Phase 2 必须）：
    - 不得落盘 env 值（只记录 keys）
    - 不得落盘 file_write 的 content 明文（只记录 bytes + sha256）
    - 其它字符串字段做“已知 secret values”替换（best-effort）
    """

    # 对齐 approvals 的更严格表示（避免同一参数在不同事件里口径漂移）
    if tool in ("shell_exec", "file_write", "skill_exec", "apply_patch"):
        _summary, req = _sanitize_approval_request(tool, args=args, skills_manager=None)
        return req

    def _redact_str(text: str) -> str:
        """把文本中的“已知 secret 值”替换为 `<redacted>`（best-effort）。"""

        if not text:
            return text
        out = text
        for v in redaction_values:
            if not isinstance(v, str):
                continue
            vv = v.strip()
            if len(vv) < 4:
                continue
            out = out.replace(vv, "<redacted>")
        return out

    def _sanitize_obj(obj: Any) -> Any:
        """递归清洗对象结构，避免在事件/WAL 中落盘敏感信息。"""

        if isinstance(obj, str):
            return _redact_str(obj)
        if isinstance(obj, list):
            return [_sanitize_obj(x) for x in obj]
        if isinstance(obj, dict):
            out: Dict[str, Any] = {}
            for k, v in obj.items():
                key = str(k)
                # 通用 Gate：env 只保留 keys，避免意外把 key/value 落盘
                if key == "env" and isinstance(v, dict):
                    out["env_keys"] = sorted(str(kk) for kk in v.keys())
                    continue
                out[key] = _sanitize_obj(v)
            return out
        return obj

    return _sanitize_obj(dict(args))  # copy，避免外部引用被修改


def _classify_run_exception(exc: BaseException) -> Dict[str, Any]:
    """
    将运行时异常映射为 `run_failed` payload（最小集合）。

    设计目标：
    - 给 UI/调用方提供可用的错误信息（可回归）
    - 不泄露 secrets（例如 API key）
    """

    # httpx 相关异常分类（不强依赖 LLM backend 实现）
    try:
        import httpx  # type: ignore

        if isinstance(exc, httpx.TimeoutException):
            return {"error_kind": "network_timeout", "message": str(exc), "retryable": True}

        if isinstance(exc, httpx.RequestError):
            return {"error_kind": "network_error", "message": str(exc), "retryable": True}

        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            kind = "http_error"
            retryable = False
            if code in (401, 403):
                kind = "auth_error"
            elif code == 429:
                kind = "rate_limited"
                retryable = True
            elif 500 <= code <= 599:
                kind = "server_error"
                retryable = True
            msg = f"HTTP {code}"
            try:
                data = exc.response.json()
                # OpenAI 风格：{"error":{"message": "..."}}
                if isinstance(data, dict) and isinstance(data.get("error"), dict):
                    em = data["error"].get("message")
                    if isinstance(em, str) and em.strip():
                        msg = f"HTTP {code}: {em.strip()}"
            except Exception:
                pass
            if len(msg) > 800:
                msg = msg[:800] + "...<truncated>"
            return {"error_kind": kind, "message": msg, "retryable": retryable}
    except Exception:
        pass

    if isinstance(exc, ValueError):
        # 常见：缺少 API key env；或配置加载问题
        return {"error_kind": "config_error", "message": str(exc), "retryable": False}

    # LLM 相关：显式可分类错误
    try:
        from agent_sdk.llm.errors import ContextLengthExceededError

        if isinstance(exc, ContextLengthExceededError):
            return {"error_kind": "context_length_exceeded", "message": str(exc), "retryable": False}
    except Exception:
        pass

    try:
        from agent_sdk.core.errors import LlmError

        if isinstance(exc, LlmError):
            return {"error_kind": "llm_error", "message": str(exc), "retryable": True}
    except Exception:
        pass

    return {"error_kind": "unknown", "message": str(exc), "retryable": False}


class ChatBackend(Protocol):
    """
    LLM backend 抽象（Phase 2：chat.completions streaming）。
    """

    async def stream_chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        temperature: Optional[float] = None,
    ) -> AsyncIterator[Any]:
        """
        以 streaming 方式产出模型输出事件流。

        约束（Phase 2）：
        - 返回的 item 需满足 `agent_sdk.llm.chat_sse` 的事件约定（例如 `type=text_delta/tool_calls/completed`）。
        - 本接口只负责“把 wire 流解析成事件”；重试/超时策略由具体 backend 决定。
        """

        ...


@dataclass(frozen=True)
class RunResult:
    """Agent.run 的返回结构（Phase 2 最小）。"""

    status: str  # completed|failed|cancelled
    final_output: str
    artifacts: List[str]
    events_path: str


class Agent:
    """
    Skills Runtime SDK 对外入口（Phase 2 最小实现）。

    说明：
    - 目前实现目标是“可回归 + 可复刻”：事件流写入 JSONL WAL，并支持流式产出 AgentEvent。
    - approvals/sandbox/多 agent 等在后续阶段补齐；当前仍保留接口注入点。
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        planner_model: Optional[str] = None,
        executor_model: Optional[str] = None,
        workspace_root: Optional[Path] = None,
        skills_roots: Optional[List[Path]] = None,
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
    ) -> None:
        """
        构造一个可运行的 Agent 实例（Phase 2）。

        参数说明（最小集合）：
        - `workspace_root`：工作区根目录；影响默认配置发现、skills filesystem root 的相对路径解析与运行产物落盘位置。
        - `planner_model`/`executor_model`/`model`：模型选择；`model` 作为兼容/快捷入口（默认走 executor）。
        - `skills_roots`：已弃用（框架级不支持 legacy roots 注入；请使用 `skills.spaces/sources` 配置 overlays）。
        - `skills_disabled_paths`：skills 禁用列表（仅对 filesystem path 生效；fail-open）。
        - `skills_manager`：可选；显式注入 SkillsManager（用于产品化自定义缓存/来源策略/禁用策略）。
        - `env_vars`：session-only env_store（仅内存，不落盘值）；用于满足 skill env_var 依赖。
        - `backend`：LLM backend（必须提供，否则运行时抛 `ValueError`）。
        - `config_paths`：额外 YAML overlays（后者覆盖前者）；默认配置由 SDK embedded assets 提供。
        - `prompt_templates`：提示词模板；缺省时使用 config 指定的模板或 builtin 模板。
        - `human_io`：人类输入提供者（用于 approvals 与 env_var 缺失时的收集）。
        - `approval_provider`：审批提供者（用于 shell/file 等危险操作的授权决策）。
        - `cancel_checker`：取消检测回调（用于 Stop/Cancel；异常时 fail-open 为“不取消”）。

        约束：
        - 该初始化过程允许做“启动期 I/O”（读 YAML overlays、扫描 skills）；核心运行事件仍通过 WAL 记录。
        - 不在初始化阶段把 secrets 落盘（env 值只存在于进程 env 或 session env_store）。
        """

        self._workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self._config_overlay_paths: List[str] = []

        # 默认配置来源（必须可作为通用框架独立运行）：
        # - 运行时优先从 package assets 加载（随 wheel 分发）
        # - 开发态允许 repo fallback（由 `load_default_config_dict()` 处理）
        default_overlay: Dict[str, Any] = load_default_config_dict()

        # 配置合并策略：
        # - SDK 默认配置永远作为 base（assets 或 repo fallback）
        # - 传入的 `config_paths` 作为 overlay（后者覆盖前者）
        # 这样可避免调用方“忘了包含 default.yaml”导致缺字段/加载失败，并与 spec 的 overlay 语义一致。
        overlays: List[Dict[str, Any]] = [default_overlay]
        if config_paths:
            for p in config_paths:
                pp = Path(p)
                try:
                    pp = pp.resolve()
                except Exception:
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

        if skills_roots:
            raise FrameworkError(
                code="SKILL_CONFIG_LEGACY_ROOTS_UNSUPPORTED",
                message="Legacy skills_roots is not supported. Use skills.spaces + skills.sources via config overlays.",
                details={"arg": "skills_roots"},
            )
        if self._config.skills.roots:
            raise FrameworkError(
                code="SKILL_CONFIG_LEGACY_ROOTS_UNSUPPORTED",
                message="Legacy skills.roots is not supported. Use skills.spaces + skills.sources.",
                details={"path": "skills.roots"},
            )
        if self._config.skills.mode != "explicit":
            raise FrameworkError(
                code="SKILL_CONFIG_LEGACY_MODE_UNSUPPORTED",
                message="Legacy skills.mode is not supported. Use explicit spaces/sources only.",
                details={"path": "skills.mode", "actual": self._config.skills.mode, "expected": "explicit"},
            )

        self._backend = backend
        self._executor = Executor()
        self._human_io = human_io
        self._approval_provider = approval_provider
        self._cancel_checker = cancel_checker
        self._safety = self._config.safety
        self._approved_for_session_keys: set[str] = set()
        self._exec_sessions = exec_sessions
        self._collab_manager = collab_manager
        # session-only env_store（可由应用层传入共享 dict；不得落盘）
        self._env_store: Dict[str, str] = env_vars if env_vars is not None else {}

        def _resolve_under_workspace(p: Path) -> Path:
            """将相对路径解析到 `workspace_root` 下并 `resolve()`，用于稳定的路径语义。"""

            if p.is_absolute():
                return p.resolve()
            return (self._workspace_root / p).resolve()

        self._skills_manager = skills_manager or SkillsManager(
            workspace_root=self._workspace_root, skills_config=self._config.skills
        )
        self._skills_manager.scan()
        if skills_disabled_paths:
            for p in skills_disabled_paths:
                try:
                    self._skills_manager.set_enabled(_resolve_under_workspace(Path(p)), False)
                except Exception:
                    # fail-open：禁用列表里出现未扫描到的 path 不应阻断 run
                    continue

        def _load_builtin_prompt_template(template_name: str) -> Tuple[str, str]:
            """从 package assets 读取内置 prompt 模板（system/developer）。"""

            from importlib.resources import files

            base = files("agent_sdk.assets").joinpath("prompts").joinpath(template_name)
            system_text = base.joinpath("system.md").read_text(encoding="utf-8")
            developer_text = base.joinpath("developer.md").read_text(encoding="utf-8")
            return system_text, developer_text

        if prompt_templates is None:
            pcfg = self._config.prompt
            system_text: Optional[str] = pcfg.system_text
            developer_text: Optional[str] = pcfg.developer_text
            system_path: Optional[Path] = _resolve_under_workspace(Path(pcfg.system_path)) if pcfg.system_path else None
            developer_path: Optional[Path] = _resolve_under_workspace(Path(pcfg.developer_path)) if pcfg.developer_path else None

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
        self._extra_tools: List[Tuple[ToolSpec, Any]] = []

    def tool(self, func=None, *, name: Optional[str] = None, description: Optional[str] = None):  # type: ignore[no-untyped-def]
        """
        注册自定义 tool（decorator）。

        Phase 2 限制：
        - 仅支持把函数签名的基础类型（str/int/float/bool）转成 JSON schema
        - 返回值会被 `str(...)` 并写入 ToolResultPayload.stdout
        """

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

            self._extra_tools.append((spec, handler))
            return f

        if func is None:
            return _register
        return _register(func)

    async def _ensure_skill_env_vars(  # type: ignore[no-untyped-def]
        self,
        skill: Skill,
        *,
        run_id: str,
        turn_id: str,
        emit,
    ) -> None:
        """
        确保某个 skill 的 env_var 依赖已满足（session-only，不落盘值）。

        行为对齐：
        - `docs/specs/skills-runtime-sdk/docs/env-store.md`
        - `docs/specs/skills-runtime-sdk/docs/skills.md` §6.3

        约束：
        - 不得把真实 value 写入 events/WAL（只能写 env 名字与来源）。
        - Web 场景必须通过 `human_request` 事件驱动 UI 弹窗，再由 /human/answer 回填 queue。
        """

        required = list(getattr(skill, "required_env_vars", []) or [])
        if not required:
            return

        for env_name in required:
            env_name = str(env_name or "").strip()
            if not env_name:
                continue

            # 1) session env_store 优先
            if env_name in self._env_store and str(self._env_store.get(env_name) or "") != "":
                emit(
                    AgentEvent(
                        type="env_var_set",
                        ts=_now_rfc3339(),
                        run_id=run_id,
                        turn_id=turn_id,
                        payload={
                            "env_var": env_name,
                            "skill_name": skill.skill_name,
                            "skill_path": str(skill.path or skill.locator),
                            "value_source": "provided",
                        },
                    )
                )
                continue

            # 2) process env 次之（允许 CLI/CI）
            pv = os.environ.get(env_name, "")
            if pv:
                self._env_store[env_name] = pv
                emit(
                    AgentEvent(
                        type="env_var_set",
                        ts=_now_rfc3339(),
                        run_id=run_id,
                        turn_id=turn_id,
                        payload={
                            "env_var": env_name,
                            "skill_name": skill.skill_name,
                            "skill_path": str(skill.path or skill.locator),
                            "value_source": "process_env",
                        },
                    )
                )
                continue

            # 3) 缺失：需要 human 收集
            emit(
                AgentEvent(
                    type="env_var_required",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={
                        "env_var": env_name,
                        "skill_name": skill.skill_name,
                        "skill_path": str(skill.path or skill.locator),
                        "source": "skill_dependency",
                    },
                )
            )

            if self._human_io is None:
                raise ValueError(f"missing required env var (no HumanIOProvider): {env_name}")

            call_id = f"env_{env_name}_{uuid.uuid4().hex}"
            question = f"请提供环境变量 {env_name} 的值（仅 session 内存使用，不落盘）。"

            # 用 human_request 事件驱动 UI 弹窗；不发送 human_response（避免落盘 value）
            emit(
                AgentEvent(
                    type="human_request",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={
                        "call_id": call_id,
                        "question": question,
                        "choices": None,
                        "context": {
                            "kind": "env_var",
                            "env_var": env_name,
                            "skill": {"name": skill.skill_name, "path": str(skill.path or skill.locator)},
                        },
                    },
                )
            )

            answer = await asyncio.to_thread(
                self._human_io.request_human_input,
                call_id=call_id,
                question=question,
                choices=None,
                context={
                    "kind": "env_var",
                    "env_var": env_name,
                    "skill": {"name": skill.skill_name, "path": str(skill.path or skill.locator)},
                },
                timeout_ms=self._config.run.human_timeout_ms,
            )
            if not isinstance(answer, str) or answer == "":
                raise ValueError(f"missing required env var: {env_name}")

            self._env_store[env_name] = answer
            emit(
                AgentEvent(
                    type="env_var_set",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                        payload={
                            "env_var": env_name,
                            "skill_name": skill.skill_name,
                            "skill_path": str(skill.path or skill.locator),
                            "value_source": "human",
                        },
                    )
                )

    def run(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> RunResult:
        """
        同步运行任务并返回汇总结果。

        Phase 2：通过消费 `run_stream(...)` 得到最终输出。
        """

        final_output = ""
        events_path = ""
        status = "completed"
        for ev in self.run_stream(task, run_id=run_id, initial_history=initial_history):
            if ev.type == "run_completed":
                final_output = str(ev.payload.get("final_output") or "")
                events_path = str(ev.payload.get("events_path") or "")
                status = "completed"
            if ev.type == "run_failed":
                final_output = str(ev.payload.get("message") or "")
                events_path = str(ev.payload.get("events_path") or events_path or "")
                status = "failed"
            if ev.type == "run_cancelled":
                final_output = str(ev.payload.get("message") or "")
                events_path = str(ev.payload.get("events_path") or events_path or "")
                status = "cancelled"
        return RunResult(status=status, final_output=final_output, artifacts=[], events_path=events_path)

    def run_stream(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Iterator[AgentEvent]:
        """
        同步事件流接口（Iterator[AgentEvent]）。

        实现方式：
        - 在后台线程运行 async loop
        - 通过线程安全队列把事件传回当前线程
        """

        import queue

        q: "queue.Queue[Optional[AgentEvent]]" = queue.Queue()
        err_q: "queue.Queue[BaseException]" = queue.Queue()

        def _worker() -> None:
            """后台线程入口：运行 async loop 并把事件写入线程安全队列。"""

            try:
                asyncio.run(
                    self._run_stream_async(
                        task,
                        run_id=run_id,
                        initial_history=initial_history,
                        emit=lambda e: q.put(e),
                    )
                )
            except BaseException as e:  # pragma: no cover（线程内异常兜底）
                err_q.put(e)
            finally:
                q.put(None)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

        while True:
            ev = q.get()
            if ev is None:
                break
            yield ev

        if not err_q.empty():
            raise err_q.get()

    async def run_stream_async(
        self,
        task: str,
        *,
        run_id: Optional[str] = None,
        initial_history: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncIterator[AgentEvent]:
        """
        异步事件流接口（给 Web/SSE 适配层使用）。

        约束（生产化补齐）：
        - 必须是真正的 streaming：事件产生即 yield，不得“缓冲到结束再一次性输出”。
        """

        q: "asyncio.Queue[AgentEvent | None]" = asyncio.Queue()

        def _emit(e: AgentEvent) -> None:
            """把事件写入 asyncio queue（非阻塞）。"""

            try:
                q.put_nowait(e)
            except Exception:
                # fail-open：队列异常不应杀死 run；但可能导致上层丢事件
                pass

        async def _runner() -> None:
            """后台任务：执行核心 loop 并把事件推入 queue。"""

            try:
                await self._run_stream_async(task, run_id=run_id, initial_history=initial_history, emit=_emit)
            finally:
                with contextlib.suppress(Exception):
                    q.put_nowait(None)

        t = asyncio.create_task(_runner())
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            if not t.done():
                t.cancel()
                with contextlib.suppress(BaseException):
                    await asyncio.gather(t, return_exceptions=True)

    async def _run_stream_async(  # type: ignore[no-untyped-def]
        self,
        task: str,
        *,
        run_id: Optional[str],
        initial_history: Optional[List[Dict[str, Any]]],
        emit,
    ) -> None:
        """
        核心 run loop（async）。

        参数：
        - emit：事件输出回调（同步函数），用于把事件推到调用方（run_stream 通过 queue 实现流式）
        """

        if self._backend is None:
            raise ValueError("未配置 LLM backend（backend=None）")

        run_id = run_id or f"run_{uuid.uuid4().hex}"
        run_dir = (self._workspace_root / ".skills_runtime_sdk" / "runs" / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        events_path = run_dir / "events.jsonl"
        wal = JsonlWal(events_path)
        started_monotonic = time.monotonic()

        max_steps = int(self._config.run.max_steps)
        max_wall_time_sec = self._config.run.max_wall_time_sec

        existing_events_count = 0
        existing_events_tail: List[AgentEvent] = []
        existing_events_all: List[AgentEvent] = []
        if events_path.exists():
            tail: "deque[AgentEvent]" = deque(maxlen=200)
            for ev in wal.iter_events():
                existing_events_count += 1
                tail.append(ev)
                existing_events_all.append(ev)
            existing_events_tail = list(tail)

        resume_strategy = str(getattr(self._config.run, "resume_strategy", "summary") or "summary").strip().lower()
        if resume_strategy not in ("summary", "replay"):
            resume_strategy = "summary"

        resume_replay_history: Optional[List[Dict[str, Any]]] = None
        resume_replay_denied: Dict[str, int] = {}
        resume_replay_approved: set[str] = set()

        if existing_events_count > 0 and initial_history is None and resume_strategy == "replay":
            try:
                from agent_sdk.state.replay import rebuild_resume_replay_state

                st = rebuild_resume_replay_state(existing_events_all)
                resume_replay_history = st.history
                resume_replay_denied = st.denied_approvals_by_key
                resume_replay_approved = st.approved_for_session_keys
            except Exception:
                # fail-open：回放失败时回退到 Phase 2 summary-based resume
                resume_replay_history = None
                resume_replay_denied = {}
                resume_replay_approved = set()

        def _build_resume_summary() -> Optional[str]:
            """
            Phase 2 Resume：从已存在 WAL 生成一条摘要型 assistant 消息。

            触发条件：
            - WAL 非空；
            - 调用方未显式传入 initial_history（显式历史以调用方为准，不自动注入 resume 摘要）。
            """

            if existing_events_count <= 0:
                return None
            if initial_history is not None:
                return None
            if resume_strategy == "replay" and resume_replay_history is not None:
                return None

            last_run_started: Optional[AgentEvent] = None
            last_terminal: Optional[AgentEvent] = None
            last_tools: List[AgentEvent] = []

            for ev in reversed(existing_events_tail):
                if last_terminal is None and ev.type in ("run_completed", "run_failed", "run_cancelled"):
                    last_terminal = ev
                if last_run_started is None and ev.type == "run_started":
                    last_run_started = ev
                if ev.type == "tool_call_finished" and len(last_tools) < 5:
                    last_tools.append(ev)
                if last_terminal is not None and last_run_started is not None and len(last_tools) >= 5:
                    break

            prev_task = ""
            if last_run_started is not None:
                prev_task = str(last_run_started.payload.get("task") or "")

            terminal_type = last_terminal.type if last_terminal is not None else "unknown"
            terminal_text = ""
            if last_terminal is not None:
                if last_terminal.type == "run_completed":
                    terminal_text = str(last_terminal.payload.get("final_output") or "")
                else:
                    terminal_text = str(last_terminal.payload.get("message") or "")

            lines: List[str] = ["[Resume Summary]"]
            if prev_task:
                lines.append(f"previous_task: {prev_task}")
            lines.append(f"previous_events: {existing_events_count}")
            lines.append(f"previous_terminal: {terminal_type}")
            if terminal_text:
                lines.append(f"previous_terminal_text: {terminal_text}")
            if last_tools:
                lines.append("recent_tools:")
                for e in reversed(last_tools):
                    tool = str(e.payload.get("tool") or "")
                    result = e.payload.get("result") or {}
                    ok = result.get("ok")
                    error_kind = result.get("error_kind")
                    lines.append(f"- {tool} ok={ok} error_kind={error_kind}")

            out = "\n".join(lines).strip()
            if len(out) > 4096:
                out = out[:4096] + "\n...<truncated>"
            return out

        resume_summary = _build_resume_summary()

        def _emit_event(ev: AgentEvent) -> None:
            """统一事件出口：先追加到 WAL，再回调给调用方（保持顺序一致）。"""

            wal.append(ev)
            emit(ev)

        def _emit_budget_exceeded(*, message: str) -> None:
            """
            发出预算耗尽的 `run_failed` 事件（Phase 2：框架级严格 fail-fast）。

            约束：
            - error_kind 固定为 budget_exceeded
            - retryable 固定为 false
            """

            _emit_event(
                AgentEvent(
                    type="run_failed",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    payload={
                        "error_kind": "budget_exceeded",
                        "message": message,
                        "retryable": False,
                        "events_path": str(events_path),
                    },
                )
            )

        def _wall_time_exceeded() -> bool:
            """检查 wall time 预算是否耗尽（未配置则返回 False）。"""

            if max_wall_time_sec is None:
                return False
            elapsed = time.monotonic() - started_monotonic
            return elapsed > float(max_wall_time_sec)

        _emit_event(
            AgentEvent(
                type="run_started",
                ts=_now_rfc3339(),
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
                        "enabled": bool(resume_summary) or bool(resume_replay_history),
                        "strategy": resume_strategy,
                        "previous_events": existing_events_count,
                    },
                },
            )
        )

        def _is_cancelled() -> bool:
            """检查是否需要取消本次 run（异常时 fail-open：返回 False）。"""

            try:
                return bool(self._cancel_checker and self._cancel_checker())
            except Exception:
                # fail-open：取消检测异常不应杀死 run
                return False

        def _emit_cancelled() -> None:
            """发出 `run_cancelled` 事件并包含 events_path，供调用方定位审计日志。"""

            _emit_event(
                AgentEvent(
                    type="run_cancelled",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    payload={"message": "cancelled by user", "events_path": str(events_path)},
                )
            )

        # tools registry（仅执行，不负责 tool_call_* 事件；由 loop 统一产出，以保证 approvals 顺序可控）
        pending_tool_events: List[AgentEvent] = []

        def _tool_event_sink(e: AgentEvent) -> None:
            """接收 tool 执行期产生的事件，延后由 loop 统一发出（便于控制 approvals 顺序）。"""

            pending_tool_events.append(e)

        tool_ctx = ToolExecutionContext(
            workspace_root=self._workspace_root,
            run_id=run_id,
            wal=wal,
            executor=self._executor,
            human_io=self._human_io,
            env=self._env_store,
            cancel_checker=self._cancel_checker,
            redaction_values=lambda: list(self._env_store.values()),
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
        for spec, handler in self._extra_tools:
            registry.register(spec, handler, override=False)

        history: List[Dict[str, Any]] = []
        if resume_replay_history:
            history.extend(resume_replay_history)
            # replay 模式下尽量恢复 approvals cache，避免进程重启后重复 ask。
            self._approved_for_session_keys.update(set(resume_replay_approved))
        elif resume_summary:
            history.append({"role": "assistant", "content": resume_summary})
        if initial_history:
            # 约束：仅接受最小 message 形态（role/content），避免 tool_calls/tool 复杂态污染会话级历史
            for item in initial_history:
                if not isinstance(item, dict):
                    continue
                role = item.get("role")
                content = item.get("content")
                if role not in ("user", "assistant"):
                    continue
                if not isinstance(content, str):
                    continue
                history.append({"role": role, "content": content})
        turn = 0
        step = 0
        steps_executed = 0
        denied_approvals_by_key: Dict[str, int] = dict(resume_replay_denied or {})

        try:
            while True:
                if _is_cancelled():
                    _emit_cancelled()
                    return
                if _wall_time_exceeded():
                    _emit_budget_exceeded(
                        message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}",
                    )
                    return

                turn += 1
                turn_id = f"turn_{turn}"

                # skills injection（Phase 2：仅显式 mention）
                injected: List[Tuple[Any, str, Optional[str]]] = []
                resolved = self._skills_manager.resolve_mentions(task)
                for skill, mention in resolved:
                    await self._ensure_skill_env_vars(skill, run_id=run_id, turn_id=turn_id, emit=_emit_event)
                    injected.append((skill, "mention", mention.mention_text))
                    _emit_event(
                        AgentEvent(
                            type="skill_injected",
                            ts=_now_rfc3339(),
                            run_id=run_id,
                            turn_id=turn_id,
                            payload={
                                "skill_name": skill.skill_name,
                                "skill_path": str(skill.path or skill.locator),
                                "source": "mention",
                                "mention_text": mention.mention_text,
                            },
                        )
                    )

                tools = registry.list_specs()
                messages, prompt_debug = self._prompt_manager.build_messages(
                    task=task,
                    cwd=str(self._workspace_root),
                    tools=tools,
                    skills_manager=self._skills_manager,
                    injected_skills=injected,
                    history=history,
                    user_input=None,
                )

                _emit_event(
                    AgentEvent(
                        type="llm_request_started",
                        ts=_now_rfc3339(),
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

                # 为了实现“硬 stop”（尽快中断正在进行的网络流读取），这里把 backend stream
                # 放到独立 task 中消费，并用 queue + 短超时轮询 cancel_checker。
                # 这样即使 SSE 长时间没有输出，也能在用户点击 Stop 后尽快退出 run。
                agen = self._backend.stream_chat(model=self._executor_model, messages=messages, tools=tools)
                q_backend: "asyncio.Queue[Any]" = asyncio.Queue()

                async def _consume_backend() -> None:
                    """消费 backend 事件流并写入队列；把异常也转为队列 item，便于主循环统一处理。"""

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
                        if _is_cancelled():
                            backend_task.cancel()
                            with contextlib.suppress(BaseException):
                                await asyncio.gather(backend_task, return_exceptions=True)
                            _emit_cancelled()
                            return
                        if _wall_time_exceeded():
                            backend_task.cancel()
                            with contextlib.suppress(BaseException):
                                await asyncio.gather(backend_task, return_exceptions=True)
                            _emit_budget_exceeded(
                                message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}",
                            )
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
                        # ChatStreamEvent 约定字段（来自 llm.chat_sse）
                        t = getattr(ev, "type", None)
                        if t == "text_delta":
                            text = getattr(ev, "text", "") or ""
                            assistant_text += text
                            _emit_event(
                                AgentEvent(
                                    type="llm_response_delta",
                                    ts=_now_rfc3339(),
                                    run_id=run_id,
                                    turn_id=turn_id,
                                    payload={"delta_type": "text", "text": text},
                                )
                            )
                        elif t == "tool_calls":
                            calls = getattr(ev, "tool_calls", None) or []
                            pending_tool_calls.extend(calls)
                            redaction_values = list((self._env_store or {}).values())
                            _emit_event(
                                AgentEvent(
                                    type="llm_response_delta",
                                    ts=_now_rfc3339(),
                                    run_id=run_id,
                                    turn_id=turn_id,
                                    payload={
                                        "delta_type": "tool_calls",
                                        "tool_calls": [
                                            {
                                                "call_id": c.call_id,
                                                "name": c.name,
                                                "arguments": _sanitize_tool_call_arguments_for_event(
                                                    c.name, args=c.args, redaction_values=redaction_values
                                                ),
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

                if pending_tool_calls:
                    # 先把 assistant tool_calls message 写入 history（以便回注 tool outputs）
                    tool_calls_wire = []
                    for c in pending_tool_calls:
                        raw_args = c.raw_arguments
                        if raw_args is None:
                            raw_args = json.dumps(c.args, ensure_ascii=False, separators=(",", ":"))
                        tool_calls_wire.append(
                            {"id": c.call_id, "type": "function", "function": {"name": c.name, "arguments": raw_args}}
                        )
                    history.append({"role": "assistant", "content": None, "tool_calls": tool_calls_wire})

                    for call in pending_tool_calls:
                        if _is_cancelled():
                            _emit_cancelled()
                            return
                        if _wall_time_exceeded():
                            _emit_budget_exceeded(
                                message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}",
                            )
                            return

                        step += 1
                        step_id = f"step_{step}"

                        # tool_call_requested
                        redaction_values = list((self._env_store or {}).values())
                        _emit_event(
                            AgentEvent(
                                type="tool_call_requested",
                                ts=_now_rfc3339(),
                                run_id=run_id,
                                turn_id=turn_id,
                                step_id=step_id,
                                payload={
                                    "call_id": call.call_id,
                                    "name": call.name,
                                    "arguments": _sanitize_tool_call_arguments_for_event(
                                        call.name, args=call.args, redaction_values=redaction_values
                                    ),
                                },
                            )
                        )

                        requires_approval = call.name in ("shell_exec", "file_write", "skill_exec", "apply_patch")
                        approval_reason: Optional[str] = None

                        sandbox_permissions: Optional[str] = None
                        sp = call.args.get("sandbox_permissions")
                        if isinstance(sp, str) and sp.strip():
                            sandbox_permissions = sp.strip()

                        if call.name == "shell_exec":
                            argv = call.args.get("argv")
                            argv_list: list[str] = (
                                argv if isinstance(argv, list) and all(isinstance(x, str) for x in argv) else []
                            )
                            risk = evaluate_command_risk(argv_list)
                            policy = evaluate_policy_for_shell_exec(
                                argv=argv_list,
                                risk=risk,
                                safety=self._safety,
                                sandbox_permissions=sandbox_permissions,
                            )
                            if policy.action == "deny":
                                denied_payload = ToolResultPayload(
                                    ok=False,
                                    stdout="",
                                    stderr=policy.reason,
                                    exit_code=None,
                                    duration_ms=0,
                                    truncated=False,
                                    data={"tool": call.name, "reason": policy.matched_rule},
                                    error_kind="permission",
                                    retryable=False,
                                    retry_after_ms=None,
                                )
                                denied_result = ToolResult.from_payload(denied_payload, message="policy denied")
                                _emit_event(
                                    AgentEvent(
                                        type="tool_call_finished",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        step_id=step_id,
                                        payload={"call_id": call.call_id, "tool": call.name, "result": denied_result.details or {}},
                                    )
                                )
                                history.append(
                                    {"role": "tool", "tool_call_id": call.call_id, "content": denied_result.content}
                                )
                                continue
                            requires_approval = policy.action == "ask"
                        elif call.name == "skill_exec":
                            # skill_exec 的真实执行本质是 shell 命令；在审批/策略层按 shell_exec 同等处理。
                            _summary0, req0 = _sanitize_approval_request(
                                "skill_exec",
                                args=call.args,
                                skills_manager=self._skills_manager,
                            )
                            argv0 = req0.get("argv")
                            argv_list: list[str] = (
                                argv0 if isinstance(argv0, list) and all(isinstance(x, str) for x in argv0) else []
                            )

                            # 无法解析 argv 时，本次不会执行危险命令（tool 会在后续校验失败），无需进入 policy/approval。
                            if not argv_list:
                                requires_approval = False
                            else:
                                risk = evaluate_command_risk(argv_list)
                                policy = evaluate_policy_for_shell_exec(
                                    argv=argv_list,
                                    risk=risk,
                                    safety=self._safety,
                                    sandbox_permissions=None,
                                )
                                if policy.action == "deny":
                                    denied_payload = ToolResultPayload(
                                        ok=False,
                                        stdout="",
                                        stderr=policy.reason,
                                        exit_code=None,
                                        duration_ms=0,
                                        truncated=False,
                                        data={"tool": call.name, "reason": policy.matched_rule},
                                        error_kind="permission",
                                        retryable=False,
                                        retry_after_ms=None,
                                    )
                                    denied_result = ToolResult.from_payload(denied_payload, message="policy denied")
                                    _emit_event(
                                        AgentEvent(
                                            type="tool_call_finished",
                                            ts=_now_rfc3339(),
                                            run_id=run_id,
                                            turn_id=turn_id,
                                            step_id=step_id,
                                            payload={
                                                "call_id": call.call_id,
                                                "tool": call.name,
                                                "result": denied_result.details or {},
                                            },
                                        )
                                    )
                                    history.append(
                                        {"role": "tool", "tool_call_id": call.call_id, "content": denied_result.content}
                                    )
                                    continue
                                requires_approval = policy.action == "ask"
                        elif call.name == "file_write":
                            mode = str(getattr(self._safety, "mode", "ask") or "ask").strip().lower()
                            if mode == "deny":
                                denied_payload = ToolResultPayload(
                                    ok=False,
                                    stdout="",
                                    stderr="Tool is denied by safety.mode=deny.",
                                    exit_code=None,
                                    duration_ms=0,
                                    truncated=False,
                                    data={"tool": call.name, "reason": "mode=deny"},
                                    error_kind="permission",
                                    retryable=False,
                                    retry_after_ms=None,
                                )
                                denied_result = ToolResult.from_payload(denied_payload, message="policy denied")
                                _emit_event(
                                    AgentEvent(
                                        type="tool_call_finished",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        step_id=step_id,
                                        payload={"call_id": call.call_id, "tool": call.name, "result": denied_result.details or {}},
                                    )
                                )
                                history.append(
                                    {"role": "tool", "tool_call_id": call.call_id, "content": denied_result.content}
                                )
                                continue
                            requires_approval = (mode == "ask") or (sandbox_permissions == "require_escalated")
                        elif call.name == "apply_patch":
                            mode = str(getattr(self._safety, "mode", "ask") or "ask").strip().lower()
                            if mode == "deny":
                                denied_payload = ToolResultPayload(
                                    ok=False,
                                    stdout="",
                                    stderr="Tool is denied by safety.mode=deny.",
                                    exit_code=None,
                                    duration_ms=0,
                                    truncated=False,
                                    data={"tool": call.name, "reason": "mode=deny"},
                                    error_kind="permission",
                                    retryable=False,
                                    retry_after_ms=None,
                                )
                                denied_result = ToolResult.from_payload(denied_payload, message="policy denied")
                                _emit_event(
                                    AgentEvent(
                                        type="tool_call_finished",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        step_id=step_id,
                                        payload={"call_id": call.call_id, "tool": call.name, "result": denied_result.details or {}},
                                    )
                                )
                                history.append(
                                    {"role": "tool", "tool_call_id": call.call_id, "content": denied_result.content}
                                )
                                continue
                            requires_approval = mode == "ask"
                        else:
                            # 其它工具默认不需要 approval（Phase 2/3 的最小集合）。
                            requires_approval = False

                        if requires_approval:
                            summary, request_obj = _sanitize_approval_request(
                                call.name,
                                args=call.args,
                                skills_manager=self._skills_manager if call.name == "skill_exec" else None,
                            )
                            approval_key = compute_approval_key(tool=call.name, request=request_obj)

                            if approval_key in self._approved_for_session_keys:
                                decision = ApprovalDecision.APPROVED_FOR_SESSION
                                approval_reason = "cached"
                            else:
                                _emit_event(
                                    AgentEvent(
                                        type="approval_requested",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        step_id=step_id,
                                        payload={
                                            "approval_key": approval_key,
                                            "tool": call.name,
                                            "summary": summary,
                                            "request": request_obj,
                                        },
                                    )
                                )

                                if self._approval_provider is None:
                                    decision = ApprovalDecision.DENIED
                                    approval_reason = "no_provider"
                                else:
                                    try:
                                        timeout_ms = int(getattr(self._safety, "approval_timeout_ms", 60_000) or 60_000)
                                    except Exception:
                                        timeout_ms = 60_000
                                    try:
                                        decision = await asyncio.wait_for(
                                            self._approval_provider.request_approval(
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

                            _emit_event(
                                AgentEvent(
                                    type="approval_decided",
                                    ts=_now_rfc3339(),
                                    run_id=run_id,
                                    turn_id=turn_id,
                                    step_id=step_id,
                                    payload={
                                        "approval_key": approval_key,
                                        "decision": decision.value,
                                        "reason": approval_reason,
                                    },
                                )
                            )

                            if decision == ApprovalDecision.APPROVED_FOR_SESSION:
                                self._approved_for_session_keys.add(approval_key)

                            if decision == ApprovalDecision.ABORT:
                                _emit_cancelled()
                                return

                            if decision == ApprovalDecision.DENIED:
                                denied_approvals_by_key[approval_key] = int(denied_approvals_by_key.get(approval_key, 0)) + 1
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
                                _emit_event(
                                    AgentEvent(
                                        type="tool_call_finished",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        step_id=step_id,
                                        payload={
                                            "call_id": call.call_id,
                                            "tool": call.name,
                                            "result": denied_result.details or {},
                                        },
                                    )
                                )
                                history.append(
                                    {"role": "tool", "tool_call_id": call.call_id, "content": denied_result.content}
                                )

                                # Fail-fast：若没有 ApprovalProvider，则任何需要审批的工具都不可能被执行。
                                # 继续循环只会让模型反复重试同一动作，导致 run “看似卡住”。
                                if approval_reason == "no_provider":
                                    _emit_event(
                                        AgentEvent(
                                            type="run_failed",
                                            ts=_now_rfc3339(),
                                            run_id=run_id,
                                            payload={
                                                "error_kind": "config_error",
                                                "message": f"ApprovalProvider is required for tool '{call.name}' but none is configured.",
                                                "retryable": False,
                                                "events_path": str(events_path),
                                                "details": {
                                                    "tool": call.name,
                                                    "approval_key": approval_key,
                                                    "reason": approval_reason,
                                                },
                                            },
                                        )
                                    )
                                    return

                                # Loop guard：同一 approval_key 被重复 denied 多次，视为模型陷入重试循环，直接中止本次 run。
                                if denied_approvals_by_key.get(approval_key, 0) >= 2:
                                    _emit_event(
                                        AgentEvent(
                                            type="run_failed",
                                            ts=_now_rfc3339(),
                                            run_id=run_id,
                                            payload={
                                                "error_kind": "approval_denied",
                                                "message": "Approval was denied repeatedly for the same action; aborting to prevent an infinite loop.",
                                                "retryable": False,
                                                "events_path": str(events_path),
                                                "details": {
                                                    "tool": call.name,
                                                    "approval_key": approval_key,
                                                    "reason": approval_reason,
                                                },
                                            },
                                        )
                                    )
                                    return
                                continue

                        if _is_cancelled():
                            _emit_cancelled()
                            return

                        # max_steps 预算按“实际开始执行的 tool call”计数（不把 policy/approval deny 计入）。
                        if steps_executed >= max_steps:
                            _emit_budget_exceeded(message=f"budget exceeded: max_steps={max_steps}")
                            return
                        steps_executed += 1

                        _emit_event(
                            AgentEvent(
                                type="tool_call_started",
                                ts=_now_rfc3339(),
                                run_id=run_id,
                                turn_id=turn_id,
                                step_id=step_id,
                                payload={"call_id": call.call_id, "tool": call.name},
                            )
                        )

                        pending_tool_events.clear()
                        result: ToolResult = registry.dispatch(call, turn_id=turn_id, step_id=step_id)
                        for te in pending_tool_events:
                            emit(te)

                        _emit_event(
                            AgentEvent(
                                type="tool_call_finished",
                                ts=_now_rfc3339(),
                                run_id=run_id,
                                turn_id=turn_id,
                                step_id=step_id,
                                payload={"call_id": call.call_id, "tool": call.name, "result": result.details or {}},
                            )
                        )

                        history.append({"role": "tool", "tool_call_id": call.call_id, "content": result.content})

                    continue

                # 无 tool_calls：认为本轮输出即最终回答
                if assistant_text:
                    history.append({"role": "assistant", "content": assistant_text})

                _emit_event(
                    AgentEvent(
                        type="run_completed",
                        ts=_now_rfc3339(),
                        run_id=run_id,
                        payload={"final_output": assistant_text, "artifacts": [], "events_path": str(events_path)},
                    )
                )
                return
        except BaseException as e:
            failed = _classify_run_exception(e)
            failed["events_path"] = str(events_path)
            _emit_event(
                AgentEvent(
                    type="run_failed",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    payload=failed,
                )
            )
            return

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
from agent_sdk.core.errors import FrameworkError, UserError
from agent_sdk.core.exec_sessions import ExecSessionsProvider
from agent_sdk.core.executor import Executor
from agent_sdk.core.loop_controller import LoopController
from agent_sdk.core.run_errors import MissingRequiredEnvVarError, RunError, RunErrorKind
from agent_sdk.state.jsonl_wal import JsonlWal
from agent_sdk.state.wal_emitter import WalEmitter
from agent_sdk.state.wal_protocol import WalBackend
from agent_sdk.tools.builtin import register_builtin_tools
from agent_sdk.tools.dispatcher import ToolDispatchInputs, ToolDispatcher
from agent_sdk.tools.protocol import ToolCall, ToolResult, ToolSpec
from agent_sdk.tools.registry import ToolExecutionContext, ToolRegistry

from agent_sdk.prompts.manager import PromptManager, PromptTemplates
from agent_sdk.prompts.compaction import (
    SUMMARY_PREFIX_TEMPLATE_ZH,
    build_compaction_messages,
    format_history_for_compaction,
)
from agent_sdk.llm.protocol import ChatRequest
from agent_sdk.skills.manager import SkillsManager
from agent_sdk.skills.models import Skill
from agent_sdk.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest, compute_approval_key
from agent_sdk.safety.guard import evaluate_command_risk
from agent_sdk.safety.policy import evaluate_policy_for_custom_tool, evaluate_policy_for_shell_exec
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


def _classify_run_exception(exc: BaseException) -> RunError:
    """
    将运行时异常映射为结构化 RunError（用于生成稳定 run_failed payload）。

    约束：
    - 不得包含 secrets（例如 API key value）
    - message 必须尽量简洁可读
    """

    # 框架结构化错误：通常属于配置/输入问题（fail-fast，不建议重试）
    if isinstance(exc, FrameworkError):
        return RunError(
            error_kind=RunErrorKind.CONFIG_ERROR,
            message=str(exc),
            retryable=False,
            details={"framework_code": getattr(exc, "code", None), "framework_details": dict(getattr(exc, "details", {}) or {})},
        )

    # httpx 相关异常分类（不强依赖 LLM backend 实现）
    try:
        import httpx  # type: ignore

        if isinstance(exc, httpx.TimeoutException):
            return RunError(error_kind=RunErrorKind.LLM_ERROR, message=str(exc), retryable=True, details={"kind": "timeout"})

        if isinstance(exc, httpx.RequestError):
            return RunError(error_kind=RunErrorKind.LLM_ERROR, message=str(exc), retryable=True, details={"kind": "request_error"})

        if isinstance(exc, httpx.HTTPStatusError):
            code = int(exc.response.status_code)
            retry_after_ms: Optional[int] = None

            kind = RunErrorKind.HTTP_ERROR
            retryable = False
            if code in (401, 403):
                kind = RunErrorKind.AUTH_ERROR
            elif code == 429:
                kind = RunErrorKind.RATE_LIMITED
                retryable = True
                ra = exc.response.headers.get("Retry-After")
                if ra:
                    try:
                        sec = int(str(ra).strip())
                        if sec > 0:
                            retry_after_ms = sec * 1000
                    except Exception:
                        retry_after_ms = None
            elif 500 <= code <= 599:
                kind = RunErrorKind.SERVER_ERROR
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

            return RunError(
                error_kind=kind,
                message=msg,
                retryable=retryable,
                retry_after_ms=retry_after_ms,
                details={"status_code": code},
            )
    except Exception:
        pass

    if isinstance(exc, MissingRequiredEnvVarError):
        details: Dict[str, Any] = {"missing_env_vars": list(exc.missing_env_vars)}
        if exc.skill_name is not None:
            details["skill_name"] = exc.skill_name
        if exc.skill_path is not None:
            details["skill_path"] = exc.skill_path
        if exc.policy is not None:
            details["policy"] = exc.policy
        return RunError(error_kind=RunErrorKind.MISSING_ENV_VAR, message=str(exc), retryable=False, details=details)

    if isinstance(exc, ValueError):
        # 常见：缺少 API key env；或配置加载问题
        return RunError(error_kind=RunErrorKind.CONFIG_ERROR, message=str(exc), retryable=False)

    # LLM 相关：显式可分类错误
    try:
        from agent_sdk.llm.errors import ContextLengthExceededError

        if isinstance(exc, ContextLengthExceededError):
            return RunError(error_kind=RunErrorKind.CONTEXT_LENGTH_EXCEEDED, message=str(exc), retryable=False)
    except Exception:
        pass

    try:
        from agent_sdk.core.errors import LlmError

        if isinstance(exc, LlmError):
            return RunError(error_kind=RunErrorKind.LLM_ERROR, message=str(exc), retryable=True)
    except Exception:
        pass

    return RunError(error_kind=RunErrorKind.UNKNOWN, message=str(exc), retryable=False)


class ChatBackend(Protocol):
    """
    LLM backend 抽象（Phase 2：chat.completions streaming）。
    """

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[Any]:
        """
        唯一入口：以单一 ChatRequest 参数包承载请求信息。

        约束：
        - 返回的 item 需满足 `agent_sdk.llm.chat_sse` 的事件约定（例如 `type=text_delta/tool_calls/completed`）。
        """

        ...


def _validate_chat_backend_protocol(backend: Any) -> None:
    """
    校验 ChatBackend 协议（fail-fast）。

    约束：
    - backend 必须实现 `stream_chat(request: ChatRequest)`；
    - 不允许 legacy `stream_chat(model, messages, ...)` 签名被当作“可用协议”。

    参数：
    - backend：待校验的 backend 实例

    异常：
    - ValueError：协议不匹配（将被映射为 run_failed 的 `config_error`）
    """

    fn = getattr(backend, "stream_chat", None)
    if not callable(fn):
        raise ValueError("ChatBackend protocol mismatch: missing stream_chat(request: ChatRequest)")

    try:
        sig = inspect.signature(fn)
    except Exception:
        # fail-open：无法可靠 introspect 时，至少确保可调用；实际调用失败会被 run_failed 捕获并分类
        return

    params = list(sig.parameters.values())
    if params and params[0].name in ("self", "cls"):
        params = params[1:]

    if not params:
        raise ValueError("ChatBackend.stream_chat must accept a `request` parameter")

    # 允许 request 为 positional/keyword-only，但必须存在名为 request 的参数。
    request_param = params[0]
    if request_param.name != "request":
        raise ValueError("ChatBackend.stream_chat must be stream_chat(request=...) (legacy signatures are not supported)")

    # 除 request 外，不允许出现“无默认值的额外参数”（避免误把 legacy 签名当成可用）。
    for p in params[1:]:
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            raise ValueError("ChatBackend.stream_chat must accept only `request` (additional required params are not supported)")


@dataclass(frozen=True)
class RunResult:
    """Agent.run 的返回结构（Phase 2 最小）。"""

    status: str  # completed|failed|cancelled
    final_output: str
    artifacts: List[str]
    events_path: str
    wal_locator: str


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
        self._event_hooks: List[Callable[[AgentEvent], None]] = [h for h in (event_hooks or []) if callable(h)]
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
        # 额外工具注册（custom tools / 外部注入扩展点）。
        # tuple: (ToolSpec, handler, override)
        self._extra_tools: List[Tuple[ToolSpec, Any, bool]] = []

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

            self.register_tool(spec, handler, override=False)
            return f

        if func is None:
            return _register
        return _register(func)

    def register_tool(self, spec: ToolSpec, handler: Any, *, override: bool = False) -> None:
        """
        注册一个预构造的 `ToolSpec + handler` 到 Agent（BL-031 公开扩展点）。

        参数：
        - spec：工具规格（包含 name/description/parameters）
        - handler：工具执行函数（签名需兼容 `ToolHandler`）
        - override：是否允许覆盖同名工具（语义与 `ToolRegistry.register(..., override=...)` 对齐）

        行为：
        - 默认拒绝重复注册（抛 `UserError`）
        - `override=True` 时会替换已注册的同名条目
        """

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
        run_id: str,
        turn_id: str,
        emit,
    ) -> bool:
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
            return True

        raw_policy = str(getattr(self._config.skills, "env_var_missing_policy", "ask_human") or "ask_human").strip().lower()
        policy = raw_policy if raw_policy in ("fail_fast", "ask_human", "skip_skill") else "ask_human"

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
                        "policy": policy,
                    },
                )
            )

            if policy == "skip_skill":
                emit(
                    AgentEvent(
                        type="skill_injection_skipped",
                        ts=_now_rfc3339(),
                        run_id=run_id,
                        turn_id=turn_id,
                        payload={
                            "skill_name": skill.skill_name,
                            "skill_path": str(skill.path or skill.locator),
                            "reason": "missing_env_var",
                            "missing_env_vars": [env_name],
                            "policy": policy,
                        },
                    )
                )
                return False

            if policy == "fail_fast":
                raise MissingRequiredEnvVarError(
                    missing_env_vars=[env_name],
                    skill_name=skill.skill_name,
                    skill_path=str(skill.path or skill.locator),
                    policy=policy,
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

        return True

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
        wal_locator = ""
        status = "completed"
        for ev in self.run_stream(task, run_id=run_id, initial_history=initial_history):
            if ev.type == "run_completed":
                final_output = str(ev.payload.get("final_output") or "")
                events_path = str(ev.payload.get("events_path") or ev.payload.get("wal_locator") or "")
                wal_locator = str(ev.payload.get("wal_locator") or "")
                status = "completed"
            if ev.type == "run_failed":
                final_output = str(ev.payload.get("message") or "")
                events_path = str(ev.payload.get("events_path") or ev.payload.get("wal_locator") or events_path or "")
                wal_locator = str(ev.payload.get("wal_locator") or wal_locator or "")
                status = "failed"
            if ev.type == "run_cancelled":
                final_output = str(ev.payload.get("message") or "")
                events_path = str(ev.payload.get("events_path") or ev.payload.get("wal_locator") or events_path or "")
                wal_locator = str(ev.payload.get("wal_locator") or wal_locator or "")
                status = "cancelled"
        if not events_path:
            events_path = str(wal_locator or "")
        return RunResult(status=status, final_output=final_output, artifacts=[], events_path=events_path, wal_locator=wal_locator)

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
        started_monotonic = time.monotonic()

        max_steps = int(self._config.run.max_steps)
        max_wall_time_sec = self._config.run.max_wall_time_sec
        cr = self._config.run.context_recovery
        context_recovery_mode = str(cr.mode)
        max_compactions_per_run = int(cr.max_compactions_per_run)
        ask_first_fallback_mode = str(cr.ask_first_fallback_mode)
        compaction_history_max_chars = int(cr.compaction_history_max_chars)
        compaction_keep_last_messages = int(cr.compaction_keep_last_messages)
        increase_budget_extra_steps = int(cr.increase_budget_extra_steps)
        increase_budget_extra_wall_time_sec = int(cr.increase_budget_extra_wall_time_sec)

        compactions_performed = 0
        compaction_artifacts: List[str] = []
        terminal_notices: List[Dict[str, Any]] = []

        existing_events_all = [ev for ev in wal.iter_events() if ev.run_id == run_id]
        existing_events_count = len(existing_events_all)
        existing_events_tail: List[AgentEvent] = list(deque(existing_events_all, maxlen=200))

        resume_strategy = str(self._config.run.resume_strategy)

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
            """统一事件出口：WAL append（如启用）→ hooks → stream（保持顺序一致）。"""

            wal_emitter.emit(ev)

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
                        "events_path": wal_locator,
                        "wal_locator": wal_locator,
                    },
                )
            )

        artifacts_dir = (run_dir / "artifacts").resolve()

        def _write_text_artifact(*, kind: str, content: str) -> str:
            """
            将文本写入 run artifacts 目录并返回文件路径（字符串）。

            参数：
            - kind：产物类型标识（用于文件名）
            - content：要写入的文本内容（UTF-8）
            """

            artifacts_dir.mkdir(parents=True, exist_ok=True)
            idx = len(compaction_artifacts) + 1
            name = f"{idx:03d}_{str(kind or 'artifact')}.md"
            p = (artifacts_dir / name).resolve()
            p.write_text(str(content or ""), encoding="utf-8")
            return str(p)

        def _refresh_terminal_notices() -> None:
            """
            刷新终态 notices（metadata），但不拼接进 final_output。

            说明：
            - 当前仅用于 compaction 的明显提示；
            - 若后续引入其它 notices 类型，建议同样在此处集中汇总。
            """

            terminal_notices.clear()
            if compactions_performed <= 0:
                return
            terminal_notices.append(
                {
                    "kind": "context_compacted",
                    "count": int(compactions_performed),
                    "message": f"本次运行发生过 {int(compactions_performed)} 次上下文压缩；摘要可能遗漏细节。",
                    "suggestion": "建议将任务拆分或开新 run，并把 handoff 摘要作为新 run 的起始上下文。",
                }
            )

        async def _ask_human_context_recovery_choice(*, turn_id: str) -> Optional[str]:
            """
            ask-first：向人类请求“如何恢复”的选择（四选一）。

            参数：
            - turn_id：用于事件关联

            返回：
            - choice 字符串（compact_continue/handoff_new_run/increase_budget_continue/terminate）或 None（无 provider）
            """

            if self._human_io is None:
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
            _emit_event(
                AgentEvent(
                    type="human_request",
                    ts=_now_rfc3339(),
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
                self._human_io.request_human_input,
                call_id=call_id,
                question=question,
                choices=choices,
                context={"kind": "context_recovery", "mode": "ask_first"},
                timeout_ms=self._config.run.human_timeout_ms,
            )
            ans = str(answer or "").strip()
            _emit_event(
                AgentEvent(
                    type="human_response",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={"call_id": call_id, "answer": ans},
                )
            )
            return ans

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

        def _emit_cancelled() -> None:
            """发出 `run_cancelled` 事件并包含 wal_locator，供调用方定位审计日志。"""

            _emit_event(
                AgentEvent(
                    type="run_cancelled",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    payload={"message": "cancelled by user", "events_path": wal_locator, "wal_locator": wal_locator},
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
            wal=None,  # tool 旁路事件必须走统一 emitter，避免绕过 hooks 或造成重复落盘
            event_emitter=wal_emitter,
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
        builtin_tool_names = set(s.name for s in registry.list_specs())
        for spec, handler, override in self._extra_tools:
            registry.register(spec, handler, override=bool(override))
        custom_tool_names = set(s.name for s, _h, _o in self._extra_tools)
        registered_tool_names = set(s.name for s in registry.list_specs())
        dispatcher = ToolDispatcher(registry=registry, now_rfc3339=_now_rfc3339)

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
        loop = LoopController(
            max_steps=max_steps,
            max_wall_time_sec=float(max_wall_time_sec) if max_wall_time_sec is not None else None,
            started_monotonic=started_monotonic,
            cancel_checker=self._cancel_checker,
            denied_approvals_by_key=dict(resume_replay_denied or {}),
        )

        async def _perform_compaction_turn_and_rebuild_history(*, reason: str, turn_id: str) -> str:
            """
            执行一次 compaction turn（tools 禁用），并用生成的摘要重建 history。

            参数：
            - reason：触发原因（例如 context_length_exceeded）
            - turn_id：用于事件关联

            返回：
            - artifact_path：摘要产物路径（可复制/可审计）
            """

            nonlocal compactions_performed

            if max_compactions_per_run > 0 and compactions_performed >= max_compactions_per_run:
                raise ValueError("max compactions per run exceeded")

            transcript = format_history_for_compaction(
                history,
                max_chars=compaction_history_max_chars,
                keep_last_messages=compaction_keep_last_messages,
            )
            compaction_messages = build_compaction_messages(task=task, transcript=transcript)

            _emit_event(
                AgentEvent(
                    type="compaction_started",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={"reason": str(reason or "unknown"), "mode": context_recovery_mode},
                )
            )

            summary_text = ""
            try:
                agen = self._backend.stream_chat(
                    ChatRequest(
                        model=self._executor_model,
                        messages=compaction_messages,
                        tools=None,  # 关键约束：compaction turn 禁用 tools
                        temperature=0.2,
                        run_id=run_id,
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
                _emit_event(
                    AgentEvent(
                        type="compaction_failed",
                        ts=_now_rfc3339(),
                        run_id=run_id,
                        turn_id=turn_id,
                        payload={"reason": str(reason or "unknown"), "error": str(e)},
                    )
                )
                # fallback：用 transcript 兜底，保证可复制/可继续（质量可能较差，但可回归）
                summary_text = f"(compaction failed; fallback transcript excerpt)\n\n{transcript}"

            summary_text = str(summary_text or "").strip()
            summary_full = (SUMMARY_PREFIX_TEMPLATE_ZH + "\n" + summary_text).strip() + "\n"
            artifact_path = _write_text_artifact(kind="handoff_summary", content=summary_full)
            compaction_artifacts.append(artifact_path)

            compactions_performed += 1
            _refresh_terminal_notices()

            sha256 = hashlib.sha256(summary_full.encode("utf-8")).hexdigest()
            _emit_event(
                AgentEvent(
                    type="context_compacted",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={
                        "reason": str(reason or "unknown"),
                        "count": int(compactions_performed),
                        "artifact_path": artifact_path,
                        "summary_len": len(summary_full),
                        "summary_sha256": sha256,
                    },
                )
            )

            # rebuild history：摘要（assistant）+ 最近 N 条 user/assistant 原文消息（保留即时语境）
            kept_tail: List[Dict[str, Any]] = []
            for m in history:
                if not isinstance(m, dict):
                    continue
                if m.get("role") not in ("user", "assistant"):
                    continue
                content = m.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                kept_tail.append({"role": m.get("role"), "content": content})
            kept_tail = kept_tail[-max(0, int(compaction_keep_last_messages)) :] if compaction_keep_last_messages else []

            history.clear()
            history.append({"role": "assistant", "content": summary_full})
            history.extend(kept_tail)

            _emit_event(
                AgentEvent(
                    type="compaction_finished",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    turn_id=turn_id,
                    payload={"reason": str(reason or "unknown"), "count": int(compactions_performed), "artifact_path": artifact_path},
                )
            )

            return artifact_path

        try:
            if self._backend is None:
                raise ValueError("未配置 LLM backend（backend=None）")
            _validate_chat_backend_protocol(self._backend)

            while True:
                if loop.is_cancelled():
                    _emit_cancelled()
                    return
                if loop.wall_time_exceeded():
                    _emit_budget_exceeded(
                        message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}",
                    )
                    return

                turn_id = loop.next_turn_id()

                # skills injection（Phase 2：仅显式 mention）
                injected: List[Tuple[Any, str, Optional[str]]] = []
                resolved = self._skills_manager.resolve_mentions(task)
                for skill, mention in resolved:
                    ok_to_inject = await self._ensure_skill_env_vars(skill, run_id=run_id, turn_id=turn_id, emit=_emit_event)
                    if not ok_to_inject:
                        continue
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

                try:
                    # 为了实现“硬 stop”（尽快中断正在进行的网络流读取），这里把 backend stream
                    # 放到独立 task 中消费，并用 queue + 短超时轮询 cancel_checker。
                    # 这样即使 SSE 长时间没有输出，也能在用户点击 Stop 后尽快退出 run。
                    def _on_retry(info: Dict[str, Any]) -> None:
                        """backend 重试决策可观测（通过 AgentEvent + hooks 输出）。"""

                        try:
                            _emit_event(
                                AgentEvent(
                                    type="llm_retry_scheduled",
                                    ts=_now_rfc3339(),
                                    run_id=run_id,
                                    turn_id=turn_id,
                                    payload=dict(info or {}),
                                )
                            )
                        except Exception:
                            # fail-open：观测不应影响主链路
                            pass

                    agen = self._backend.stream_chat(
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
                            if loop.is_cancelled():
                                backend_task.cancel()
                                with contextlib.suppress(BaseException):
                                    await asyncio.gather(backend_task, return_exceptions=True)
                                _emit_cancelled()
                                return
                            if loop.wall_time_exceeded():
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

                except BaseException as e:
                    # 上下文溢出恢复（BL-037/BL-009）：按 run.context_recovery.mode 决定是否压缩/询问/失败。
                    try:
                        from agent_sdk.llm.errors import ContextLengthExceededError

                        is_ctx = isinstance(e, ContextLengthExceededError)
                    except Exception:
                        is_ctx = False

                    if not is_ctx:
                        raise

                    _emit_event(
                        AgentEvent(
                            type="context_length_exceeded",
                            ts=_now_rfc3339(),
                            run_id=run_id,
                            turn_id=turn_id,
                            payload={"mode": context_recovery_mode, "compactions": int(compactions_performed)},
                        )
                    )

                    if context_recovery_mode == "fail_fast":
                        raise

                    effective_mode = context_recovery_mode
                    decision: Optional[str] = None
                    handoff_artifact_path: Optional[str] = None

                    if context_recovery_mode == "ask_first":
                        decision = await _ask_human_context_recovery_choice(turn_id=turn_id)
                        if decision is None:
                            effective_mode = ask_first_fallback_mode
                            _emit_event(
                                AgentEvent(
                                    type="context_recovery_decided",
                                    ts=_now_rfc3339(),
                                    run_id=run_id,
                                    turn_id=turn_id,
                                    payload={
                                        "mode": "ask_first",
                                        "decision": "no_human_provider",
                                        "fallback_mode": effective_mode,
                                    },
                                )
                            )
                        else:
                            _emit_event(
                                AgentEvent(
                                    type="context_recovery_decided",
                                    ts=_now_rfc3339(),
                                    run_id=run_id,
                                    turn_id=turn_id,
                                    payload={"mode": "ask_first", "decision": decision},
                                )
                            )

                            if decision == "terminate":
                                _emit_event(
                                    AgentEvent(
                                        type="run_failed",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        payload={
                                            "error_kind": "terminated",
                                            "message": "terminated by user decision (ask_first)",
                                            "retryable": False,
                                            "events_path": wal_locator,
                                            "wal_locator": wal_locator,
                                        },
                                    )
                                )
                                return

                            if decision == "handoff_new_run":
                                # handoff：生成摘要（可复制）并结束本次 run（不继续执行）。
                                try:
                                    handoff_artifact_path = await _perform_compaction_turn_and_rebuild_history(
                                        reason="context_length_exceeded",
                                        turn_id=turn_id,
                                    )
                                except Exception as ce:
                                    _emit_event(
                                        AgentEvent(
                                            type="run_failed",
                                            ts=_now_rfc3339(),
                                            run_id=run_id,
                                            payload={
                                                "error_kind": "context_length_exceeded",
                                                "message": f"context recovery failed: {ce}",
                                                "retryable": False,
                                                "events_path": wal_locator,
                                                "wal_locator": wal_locator,
                                            },
                                        )
                                    )
                                    return

                                _emit_event(
                                    AgentEvent(
                                        type="run_completed",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        payload={
                                            "final_output": "",
                                            "artifacts": list(compaction_artifacts),
                                            "events_path": wal_locator,
                                            "wal_locator": wal_locator,
                                            "metadata": {
                                                "notices": list(terminal_notices),
                                                "handoff": {"artifact_path": handoff_artifact_path},
                                            },
                                        },
                                    )
                                )
                                return

                            if decision == "increase_budget_continue":
                                old_steps = int(loop.max_steps)
                                loop.max_steps = int(loop.max_steps) + int(max(0, increase_budget_extra_steps))
                                old_wall = loop.max_wall_time_sec
                                if old_wall is not None:
                                    loop.max_wall_time_sec = float(old_wall) + float(max(0, increase_budget_extra_wall_time_sec))
                                _emit_event(
                                    AgentEvent(
                                        type="budget_increased",
                                        ts=_now_rfc3339(),
                                        run_id=run_id,
                                        turn_id=turn_id,
                                        payload={
                                            "reason": "context_recovery",
                                            "old": {"max_steps": old_steps, "max_wall_time_sec": old_wall},
                                            "new": {"max_steps": int(loop.max_steps), "max_wall_time_sec": loop.max_wall_time_sec},
                                        },
                                    )
                                )
                                # context_length_exceeded 本身无法通过“提高步骤预算”解决：仍需压缩再继续。
                                effective_mode = "compact_first"

                            if decision == "compact_continue":
                                effective_mode = "compact_first"

                    if effective_mode == "compact_first":
                        try:
                            await _perform_compaction_turn_and_rebuild_history(
                                reason="context_length_exceeded",
                                turn_id=turn_id,
                            )
                        except Exception as ce:
                            _emit_event(
                                AgentEvent(
                                    type="run_failed",
                                    ts=_now_rfc3339(),
                                    run_id=run_id,
                                    payload={
                                        "error_kind": "context_length_exceeded",
                                        "message": f"context recovery failed: {ce}",
                                        "retryable": False,
                                        "events_path": wal_locator,
                                        "wal_locator": wal_locator,
                                    },
                                )
                            )
                            return
                        # retry：进入下一轮 turn，重新构建 messages 并再次 sampling
                        continue

                    # 保守兜底：未知模式直接 fail-fast
                    raise

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
                        if loop.is_cancelled():
                            _emit_cancelled()
                            return
                        if loop.wall_time_exceeded():
                            _emit_budget_exceeded(
                                message=f"budget exceeded: max_wall_time_sec={max_wall_time_sec}",
                            )
                            return

                        step_id = loop.next_step_id()

                        # tool_call_requested
                        redaction_values = list((self._env_store or {}).values())
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
                            except Exception as e:
                                raw_arguments_validation_error = str(e)

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
                                    **(
                                        {
                                            "arguments_valid": False,
                                            "raw_arguments_len": raw_arguments_len,
                                            "raw_arguments_sha256": raw_arguments_sha256,
                                            "raw_arguments_error": raw_arguments_validation_error,
                                        }
                                        if raw_arguments_validation_error is not None
                                        else {}
                                    ),
                                },
                            )
                        )

                        # 约束（BL-008）：arguments JSON 解析失败必须 fail-closed（不执行工具），也不应进入 approvals/policy。
                        if raw_arguments_validation_error is not None:
                            validation_result = ToolResult.error_payload(
                                error_kind="validation",
                                stderr=f"invalid tool arguments JSON: {raw_arguments_validation_error}",
                                data={
                                    "raw_arguments_len": raw_arguments_len,
                                    "raw_arguments_sha256": raw_arguments_sha256,
                                },
                            )
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
                                        "result": validation_result.details or {},
                                    },
                                )
                            )
                            history.append(
                                {"role": "tool", "tool_call_id": call.call_id, "content": validation_result.content}
                            )
                            continue

                        requires_approval = False
                        approval_reason: Optional[str] = None

                        sandbox_permissions: Optional[str] = None
                        sp = call.args.get("sandbox_permissions")
                        if isinstance(sp, str) and sp.strip():
                            sandbox_permissions = sp.strip()

                        is_registered = call.name in registered_tool_names
                        is_custom_tool = (call.name in custom_tool_names) or (
                            is_registered and call.name not in builtin_tool_names
                        )

                        if is_custom_tool:
                            # Custom tools（自定义工具）审批门禁（Route A）：
                            # - 默认 ask（除非显式 allowlist）
                            # - denylist 命中直接拒绝（不进入 approvals）
                            policy = evaluate_policy_for_custom_tool(tool=call.name, safety=self._safety)
                            if policy.action == "deny":
                                denied_payload = ToolResultPayload(
                                    ok=False,
                                    stdout="",
                                    stderr=policy.reason,
                                    exit_code=None,
                                    duration_ms=0,
                                    truncated=False,
                                    data={
                                        "tool": call.name,
                                        "reason": str(policy.matched_rule or ""),
                                    },
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
                        elif call.name == "shell_exec":
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
                                skills_manager=self._skills_manager
                                if (call.name == "skill_exec" and not is_custom_tool)
                                else None,
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
                                                "events_path": wal_locator,
                                                "wal_locator": wal_locator,
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
                                if loop.should_abort_due_to_repeated_denial(approval_key=approval_key):
                                    _emit_event(
                                        AgentEvent(
                                            type="run_failed",
                                            ts=_now_rfc3339(),
                                            run_id=run_id,
                                            payload={
                                                "error_kind": "approval_denied",
                                                "message": "Approval was denied repeatedly for the same action; aborting to prevent an infinite loop.",
                                                "retryable": False,
                                                "events_path": wal_locator,
                                                "wal_locator": wal_locator,
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

                        if loop.is_cancelled():
                            _emit_cancelled()
                            return

                        # max_steps 预算按“实际开始执行的 tool call”计数（不把 policy/approval deny 计入）。
                        if not loop.try_consume_tool_step():
                            _emit_budget_exceeded(message=f"budget exceeded: max_steps={max_steps}")
                            return

                        result = dispatcher.dispatch_one(
                            inputs=ToolDispatchInputs(call=call, run_id=run_id, turn_id=turn_id, step_id=step_id),
                            pending_tool_events=pending_tool_events,
                            emit_event=_emit_event,
                            emit_stream=wal_emitter.stream_only,
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
                        payload={
                            "final_output": assistant_text,
                            "artifacts": list(compaction_artifacts),
                            "events_path": wal_locator,
                            "wal_locator": wal_locator,
                            "metadata": {"notices": list(terminal_notices)},
                        },
                    )
                )
                return
        except BaseException as e:
            failed = _classify_run_exception(e).to_payload()
            failed["events_path"] = wal_locator
            failed["wal_locator"] = wal_locator
            _emit_event(
                AgentEvent(
                    type="run_failed",
                    ts=_now_rfc3339(),
                    run_id=run_id,
                    payload=failed,
                )
            )
            return

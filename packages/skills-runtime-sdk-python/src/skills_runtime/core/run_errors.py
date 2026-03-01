"""
Run 失败错误类型化（RunErrorKind / RunError）。

对齐 OpenSpec（本仓重构）：
- `openspec/changes/sdk-production-refactor-p0/specs/typed-run-errors/spec.md`
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from skills_runtime.core.errors import FrameworkError


class RunErrorKind(str, Enum):
    """run_failed 的稳定错误分类（机器可消费）。"""

    AUTH_ERROR = "auth_error"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    HTTP_ERROR = "http_error"

    CONFIG_ERROR = "config_error"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    LLM_ERROR = "llm_error"

    MISSING_ENV_VAR = "missing_env_var"

    UNKNOWN = "unknown"


class MissingRequiredEnvVarError(ValueError):
    """
    缺失 required env var 的结构化异常（用于稳定分类为 `missing_env_var`）。

    说明：
    - 该异常仅承载“缺失哪些 env var”以及可选上下文（skill 信息），不得包含 env value。
    - 主要用于 `skills.env_var_missing_policy=fail_fast` 的无人值守场景，让集成方无需解析 message。
    """

    def __init__(
        self,
        *,
        missing_env_vars: list[str],
        skill_name: Optional[str] = None,
        skill_path: Optional[str] = None,
        policy: Optional[str] = None,
    ) -> None:
        """
        初始化异常（仅携带“缺失的 env var 名称”与可选 skill 上下文）。

        参数：
        - missing_env_vars：缺失的 env var 名称列表（必须非空）
        - skill_name：可选；触发缺失的 skill 名称
        - skill_path：可选；触发缺失的 skill 路径/locator
        - policy：可选；env var 缺失策略（例如 fail_fast/ask_human/skip_skill）
        """

        if not missing_env_vars or not all(isinstance(x, str) and x.strip() for x in missing_env_vars):
            raise ValueError("missing_env_vars must be a non-empty list of strings")

        self.missing_env_vars = [str(x).strip() for x in missing_env_vars]
        self.skill_name = str(skill_name) if skill_name is not None else None
        self.skill_path = str(skill_path) if skill_path is not None else None
        self.policy = str(policy) if policy is not None else None

        msg = "missing required env var"
        if len(self.missing_env_vars) == 1:
            msg = f"{msg}: {self.missing_env_vars[0]}"
        super().__init__(msg)


@dataclass(frozen=True)
class RunError:
    """
    RunError：结构化运行错误（用于生成稳定 run_failed payload）。

    字段：
    - error_kind：稳定分类
    - message：可读错误消息（必须避免 secrets）
    - retryable：是否建议上层重试
    - retry_after_ms：可选；建议的重试等待毫秒数（例如 429 + Retry-After）
    - details：可选；结构化上下文（必须可 JSON 序列化）
    """

    error_kind: RunErrorKind
    message: str
    retryable: bool = False
    retry_after_ms: Optional[int] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        """转换为 run_failed 的 payload dict（稳定字段名）。"""

        out: Dict[str, Any] = {
            "error_kind": str(self.error_kind.value),
            "message": str(self.message or ""),
            "retryable": bool(self.retryable),
        }
        if self.retry_after_ms is not None:
            out["retry_after_ms"] = int(self.retry_after_ms)
        if self.details:
            out["details"] = dict(self.details)
        return out


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
                    except (ValueError, TypeError):
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
            except (json.JSONDecodeError, KeyError, TypeError):
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
    except (ImportError, AttributeError):
        # 防御性兜底：可选依赖未安装时跳过。
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
        from skills_runtime.llm.errors import ContextLengthExceededError

        if isinstance(exc, ContextLengthExceededError):
            return RunError(error_kind=RunErrorKind.CONTEXT_LENGTH_EXCEEDED, message=str(exc), retryable=False)
    except ImportError:
        pass

    try:
        from skills_runtime.core.errors import LlmError

        if isinstance(exc, LlmError):
            return RunError(error_kind=RunErrorKind.LLM_ERROR, message=str(exc), retryable=True)
    except ImportError:
        pass

    return RunError(error_kind=RunErrorKind.UNKNOWN, message=str(exc), retryable=False)

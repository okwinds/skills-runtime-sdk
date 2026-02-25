"""
Run 失败错误类型化（RunErrorKind / RunError）。

对齐 OpenSpec（本仓重构）：
- `openspec/changes/sdk-production-refactor-p0/specs/typed-run-errors/spec.md`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


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

"""
事件参数脱敏（纯函数）。

本模块从 `core.agent_loop` 拆出，统一处理 event/WAL 参数的脱敏表示。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Sequence

from skills_runtime.core.approval_sanitizers import _sanitize_approval_request

if TYPE_CHECKING:  # pragma: no cover
    from skills_runtime.skills.manager import SkillsManager


def _redact_event_data(data: Any, *, redaction_values: Sequence[str] = ()) -> Any:
    """
    递归脱敏事件数据（best-effort）。

    规则：
    - 字符串中出现已知 secret 值时替换为 `<redacted>`；
    - `env` 字段仅保留 `env_keys`；
    - 保留原有结构，避免影响可观测性。
    """

    def _redact_str(text: str) -> str:
        """将字符串中的已知 secret 值替换为 `<redacted>`（best-effort）。"""
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

    if isinstance(data, str):
        return _redact_str(data)
    if isinstance(data, list):
        return [_redact_event_data(x, redaction_values=redaction_values) for x in data]
    if isinstance(data, dict):
        out: Dict[str, Any] = {}
        for k, v in data.items():
            key = str(k)
            if key == "env" and isinstance(v, dict):
                out["env_keys"] = sorted(str(kk) for kk in v.keys())
                continue
            out[key] = _redact_event_data(v, redaction_values=redaction_values)
        return out
    return data


def _sanitize_tool_call_arguments_for_event(
    tool: str,
    *,
    args: Dict[str, Any],
    redaction_values: Sequence[str] = (),
    skills_manager: SkillsManager | None = None,
) -> Dict[str, Any]:
    """
    将 tool args 转成“可观测但不泄露 secrets”的事件表示。

    说明：
    - 该函数只用于事件/WAL（`tool_call_requested`、`llm_response_delta(tool_calls)` 等）；
    - 不影响真实执行参数；
    - 必须尽量保持可调试性（保留结构/关键字段），同时满足隐私 Gate。
    """

    # 对齐 approvals 的更严格表示（避免同一参数在不同事件里口径漂移）
    if tool in ("shell_exec", "shell", "shell_command", "exec_command", "write_stdin", "file_write", "skill_exec", "apply_patch"):
        _summary, req = _sanitize_approval_request(tool, args=args, skills_manager=skills_manager)
        return req

    return _redact_event_data(dict(args), redaction_values=redaction_values)  # copy，避免外部引用被修改


__all__ = ["_redact_event_data", "_sanitize_tool_call_arguments_for_event"]

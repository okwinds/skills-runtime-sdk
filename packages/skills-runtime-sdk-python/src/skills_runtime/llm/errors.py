"""
LLM 错误类型（可分类、可回归）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/llm-backend.md`
- `docs/specs/skills-runtime-sdk/docs/production-hardening.md` §3.5

说明：
- 这些异常用于模块间传递“可程序化处理”的失败原因；
- 最终对外（事件/WAL）应映射为稳定的 `run_failed.payload.error_kind`。
"""

from __future__ import annotations

from skills_runtime.core.errors import LlmError


class ContextLengthExceededError(LlmError):
    """
    上下文长度超限（finish_reason=length 或 provider 明确报错）。

    说明：
    - 该错误通常不可通过“重试同一请求”解决；
    - 上层可选择压缩历史、减少注入内容、或请求用户确认继续策略。
    """


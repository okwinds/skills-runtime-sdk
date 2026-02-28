"""
规则审批（RuleBasedApprovalProvider）。

动机：
- 云端无人值守场景不应“等待人类点击”，而应使用程序化规则做审批决策；
- 默认必须 fail-closed：任何未命中规则的请求一律拒绝；
- condition 抛异常时视为不匹配，避免 fail-open 风险。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, List, Optional

from skills_runtime.safety.approvals import ApprovalDecision, ApprovalProvider, ApprovalRequest

logger = logging.getLogger(__name__)


ApprovalCondition = Callable[[ApprovalRequest], bool]


@dataclass(frozen=True)
class ApprovalRule:
    """
    审批规则（最小集合）。

    字段：
    - tool：工具名（精确匹配）
    - condition：可选谓词；返回 True 表示命中；抛异常视为不命中（fail-closed）
    - decision：命中后的决策
    """

    tool: str
    condition: Optional[ApprovalCondition] = None
    decision: ApprovalDecision = ApprovalDecision.DENIED


class RuleBasedApprovalProvider(ApprovalProvider):
    """
    基于规则的程序化审批 Provider（云端无人值守首选）。

    约束：
    - 默认 fail-closed：无规则命中时返回 DENIED；
    - condition 异常视为不命中，避免“异常导致放行”。
    """

    def __init__(
        self,
        *,
        rules: List[ApprovalRule],
        default: ApprovalDecision = ApprovalDecision.DENIED,
    ) -> None:
        """
        创建规则审批 Provider。

        参数：
        - rules：审批规则列表（按顺序匹配，首个命中即返回）
        - default：默认决策（未命中规则时返回；默认 DENIED，fail-closed）
        """

        self._rules = list(rules or [])
        self._default = default

    async def request_approval(self, *, request: ApprovalRequest, timeout_ms: Optional[int] = None) -> ApprovalDecision:  # type: ignore[override]
        """
        根据规则返回审批决策（不等待人类交互）。

        参数：
        - request：审批请求（已由 SDK 脱敏）
        - timeout_ms：保留参数；规则审批不使用该值
        """

        tool = str(request.tool or "").strip()
        for rule in self._rules:
            if str(rule.tool or "").strip() != tool:
                continue
            cond = rule.condition
            if cond is None:
                return rule.decision
            try:
                if bool(cond(request)):
                    return rule.decision
            except Exception:
                # fail-closed：条件异常视为不命中，避免 fail-open 风险。
                logger.debug("Approval rule condition raised an exception", exc_info=True)
                continue
        return self._default

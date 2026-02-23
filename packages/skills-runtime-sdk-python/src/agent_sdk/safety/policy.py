"""
Safety Policy（允许/询问/禁止）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/safety.md`（Policy 决策规则）
- `docs/specs/skills-runtime-sdk/docs/production-hardening.md`（approval timeout + sandbox_permissions）

说明：
- Guard（危险命令检测）只负责给出 risk_level 与 reason；
- Policy 负责结合配置（mode/allowlist/denylist）给出 allow/ask/deny 的确定性决策；
- “容错/提示”属于产品层，不在本模块实现。
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Iterable, List, Optional

from agent_sdk.config.loader import AgentSdkSafetyConfig
from agent_sdk.safety.guard import CommandRisk


@dataclass(frozen=True)
class PolicyDecision:
    """
    Policy 决策输出（确定性）。

    字段：
    - action：allow|ask|deny
    - reason：英文摘要（用于事件/日志；产品层可映射为中文）
    - matched_rule：命中的规则（可选；便于诊断）
    """

    action: str
    reason: str
    matched_rule: Optional[str] = None


def _format_argv(argv: List[str]) -> str:
    """把 argv 格式化为可读命令串（用于 prefix 匹配与审计展示）。"""

    return " ".join(shlex.quote(x) for x in argv)


def _matches_prefixes(argv: List[str], prefixes: Iterable[str]) -> Optional[str]:
    """
    判断 argv 是否命中任意前缀规则。

    匹配策略（稳定、可复现）：
    - 若 prefix 含空格：按完整命令串前缀匹配（`<cmd> <args...>`）。
    - 否则：优先匹配 argv[0]（命令名），其次匹配完整命令串前缀。

    返回：
    - 命中的 prefix（字符串）；未命中返回 None。
    """

    full = _format_argv(argv)
    cmd0 = argv[0] if argv else ""
    for p in prefixes:
        pp = str(p or "").strip()
        if not pp:
            continue
        if " " in pp:
            if full.startswith(pp):
                return pp
            continue
        if cmd0 == pp or full.startswith(pp):
            return pp
    return None


def evaluate_policy_for_shell_exec(
    *,
    argv: List[str],
    risk: CommandRisk,
    safety: AgentSdkSafetyConfig,
    sandbox_permissions: Optional[str] = None,
) -> PolicyDecision:
    """
    对 `shell_exec` 做 policy 决策（allow/ask/deny）。

    参数：
    - argv：命令 argv
    - risk：Guard 产出的风险等级
    - safety：安全配置
    - sandbox_permissions：工具请求的 sandbox 权限语义（例如 require_escalated）

    返回：
    - PolicyDecision：确定性的 allow/ask/deny 决策
    """

    # denylist 命中：直接拒绝
    denied = _matches_prefixes(argv, safety.denylist or [])
    if denied:
        return PolicyDecision(action="deny", reason="Command is denied by safety.denylist.", matched_rule=denied)

    mode = str(getattr(safety, "mode", "ask") or "ask").strip().lower()

    # mode=deny：对 shell_exec 一律拒绝（框架级保守策略）
    if mode == "deny":
        return PolicyDecision(action="deny", reason="Tool is denied by safety.mode=deny.", matched_rule="mode=deny")

    # require_escalated：必须进入审批（即使 mode=allow）
    if sandbox_permissions == "require_escalated":
        return PolicyDecision(action="ask", reason="Tool requires escalated sandbox permissions.", matched_rule="sandbox")

    # allowlist 命中：允许（视为明确授权）
    allowed = _matches_prefixes(argv, safety.allowlist or [])
    if allowed:
        return PolicyDecision(action="allow", reason="Command is allowed by safety.allowlist.", matched_rule=allowed)

    if mode == "allow":
        return PolicyDecision(action="allow", reason="Allowed by safety.mode=allow.", matched_rule="mode=allow")

    # mode=ask：高危必须询问；其它允许但可由上层决定是否也询问
    if risk.risk_level == "high":
        return PolicyDecision(action="ask", reason="High-risk command requires approval.", matched_rule="risk=high")

    return PolicyDecision(action="ask", reason="Approval required by safety.mode=ask.", matched_rule="mode=ask")


def evaluate_policy_for_custom_tool(*, tool: str, safety: AgentSdkSafetyConfig) -> PolicyDecision:
    """
    对 custom tool 做 policy 决策（allow/ask/deny）。

    背景：
    - custom tools 指“不在 builtin tools 名称集合内”的工具（包括 `Agent.tool` 注册与 `Agent.register_tool` 注入）。
    - 在 `safety.mode=ask` 下，custom tools 默认必须进入 approvals（fail-closed），只有显式 allowlist 才可免审批执行。

    参数：
    - tool：工具名（`ToolCall.name`）
    - safety：安全配置（包含 mode/tool_allowlist/tool_denylist）

    返回：
    - PolicyDecision：确定性的 allow/ask/deny 决策（matched_rule 用于诊断与审计）
    """

    tool_name = str(tool or "").strip()

    tool_deny = set(str(x or "").strip() for x in (getattr(safety, "tool_denylist", []) or []) if str(x or "").strip())
    if tool_name and tool_name in tool_deny:
        return PolicyDecision(
            action="deny",
            reason="Tool is denied by safety.tool_denylist.",
            matched_rule="tool_denylist",
        )

    mode = str(getattr(safety, "mode", "ask") or "ask").strip().lower()

    if mode == "deny":
        return PolicyDecision(action="deny", reason="Tool is denied by safety.mode=deny.", matched_rule="mode=deny")

    if mode == "allow":
        return PolicyDecision(action="allow", reason="Allowed by safety.mode=allow.", matched_rule="mode=allow")

    tool_allow = set(
        str(x or "").strip() for x in (getattr(safety, "tool_allowlist", []) or []) if str(x or "").strip()
    )
    if tool_name and tool_name in tool_allow:
        return PolicyDecision(
            action="allow",
            reason="Tool is allowed by safety.tool_allowlist.",
            matched_rule="tool_allowlist",
        )

    return PolicyDecision(action="ask", reason="Approval required by safety.mode=ask.", matched_rule="mode=ask")

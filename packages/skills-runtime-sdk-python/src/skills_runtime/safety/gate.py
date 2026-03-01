"""统一安全门禁：将 tool call 的 policy/approval 决策从 Agent loop 中解耦。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from skills_runtime.config.loader import AgentSdkSafetyConfig
from skills_runtime.safety.descriptors import DenyDescriptor
from skills_runtime.safety.guard import CommandRisk
from skills_runtime.safety.policy import (
    PolicyDecision,
    evaluate_policy_for_custom_tool,
    evaluate_policy_for_shell_exec,
)
from skills_runtime.tools.protocol import (
    PassthroughDescriptor,
    ToolCall,
    ToolResult,
    ToolResultPayload,
    ToolSafetyDescriptor,
)

logger = logging.getLogger(__name__)


@dataclass
class GateDecision:
    """安全门禁决策输出。"""

    action: str
    reason: str
    summary: str
    sanitized_request: Dict[str, Any]
    matched_rule: Optional[str] = None


class SafetyGate:
    """统一安全门禁。"""

    def __init__(
        self,
        *,
        safety_config: AgentSdkSafetyConfig,
        get_descriptor: Callable[[str], ToolSafetyDescriptor],
        skills_manager: Optional[Any] = None,
        sandbox_permissions: Optional[str] = None,
        is_custom_tool: Optional[Callable[[str], bool]] = None,
    ) -> None:
        """创建 SafetyGate 实例并绑定配置/descriptor 获取器。"""

        self._safety_config = safety_config
        self._get_descriptor_fn = get_descriptor
        self._skills_manager = skills_manager
        self._sandbox_permissions = sandbox_permissions
        self._is_custom_tool_fn = is_custom_tool

    def _get_descriptor(self, tool_name: str) -> ToolSafetyDescriptor:
        """获取工具 descriptor；缺失时使用 passthrough 兜底。"""

        try:
            desc = self._get_descriptor_fn(tool_name)
        except Exception:
            # 防御性兜底：descriptor 查找函数由外部注入，可能抛出任意异常。
            # 安全策略：fail-closed，避免因门禁组件异常导致工具无门禁执行。
            logger.error("Descriptor lookup failed for %r — denying", tool_name, exc_info=True)
            return DenyDescriptor()
        return desc if desc is not None else PassthroughDescriptor()

    @staticmethod
    def _normalize_argv(argv: Any) -> list[str]:
        """
        将 `argv` 规范化为 `list[str]`（仅保留 str 元素）。

        参数：`argv`：任意输入；仅当其为 list 时才会提取其中的 str。
        返回：规范化后的参数列表；非 list 输入返回空列表。
        """

        if isinstance(argv, list):
            return [x for x in argv if isinstance(x, str)]
        return []

    @staticmethod
    def _command_summary(tool_name: str, request: Dict[str, Any]) -> str:
        """
        生成面向审批 UI 的单行摘要文本。

        参数：`tool_name`：工具名；`request`：已可展示/脱敏的请求 dict（读取 `argv/command/cmd`）。
        返回：摘要字符串（用于人类审批提示）。
        """

        argv = request.get("argv")
        if isinstance(argv, list) and argv:
            cmd = " ".join(str(x) for x in argv)
        else:
            cmd = str(request.get("command") or request.get("cmd") or "<unknown>")
        return f"授权：{tool_name} 执行命令：{cmd}"

    def _extract_risk(self, descriptor: ToolSafetyDescriptor, args: Dict[str, Any]) -> tuple[list[str], CommandRisk]:
        """
        从 descriptor 提取风险信息，并统一为 `(argv, CommandRisk)` 形态。

        参数：`descriptor`：工具安全描述符；`args`：原始 tool call args。
        返回：`(argv, risk)`；其中 `argv` 为规范化的 `list[str]`，`risk` 为 `CommandRisk`。
        """

        try:
            risk_raw = descriptor.extract_risk(args, skills_manager=self._skills_manager)
        except TypeError:
            risk_raw = descriptor.extract_risk(args)

        if isinstance(risk_raw, tuple) and len(risk_raw) == 2:
            argv_raw, risk_obj = risk_raw
            argv = self._normalize_argv(argv_raw)
            if isinstance(risk_obj, CommandRisk):
                return argv, risk_obj
            if isinstance(risk_obj, dict):
                return argv, CommandRisk(
                    risk_level=str(risk_obj.get("risk_level") or "low"),
                    reason=str(risk_obj.get("reason") or ""),
                )
            return argv, CommandRisk(risk_level="low", reason="unknown risk payload")

        if isinstance(risk_raw, dict):
            argv = self._normalize_argv(risk_raw.get("argv"))
            return argv, CommandRisk(
                risk_level=str(risk_raw.get("risk_level") or "low"),
                reason=str(risk_raw.get("reason") or ""),
            )

        return [], CommandRisk(risk_level="low", reason="descriptor risk unavailable")

    def _sanitize_for_approval(self, call: ToolCall, descriptor: ToolSafetyDescriptor) -> tuple[str, Dict[str, Any]]:
        """
        生成审批用的 `(summary, request_dict)`（需为可展示/已脱敏内容）。

        参数：`call`：ToolCall；`descriptor`：工具安全描述符。
        返回：`(summary, request_dict)`；若 descriptor 仅返回 `request_dict`，则自动生成 summary。
        """

        try:
            payload = descriptor.sanitize_for_approval(call.args, skills_manager=self._skills_manager)
        except TypeError:
            payload = descriptor.sanitize_for_approval(call.args)

        if isinstance(payload, tuple) and len(payload) == 2:
            summary_raw, request_raw = payload
            summary = str(summary_raw or "")
            request = dict(request_raw) if isinstance(request_raw, dict) else {}
            return summary, request

        if isinstance(payload, dict):
            request = dict(payload)
            return self._command_summary(call.name, request), request

        return self._command_summary(call.name, {}), {}

    def evaluate(self, call: ToolCall) -> GateDecision:
        """对单次 tool call 进行安全决策。"""

        descriptor = self._get_descriptor(call.name)
        category = str(getattr(descriptor, "policy_category", "none") or "none").strip().lower()
        if category == "none" and self._is_custom_tool_fn is not None and self._is_custom_tool_fn(call.name):
            category = "custom"

        if category == "none":
            return GateDecision(
                action="allow",
                reason="No safety gate required",
                summary="",
                sanitized_request={},
                matched_rule=None,
            )

        argv, risk = self._extract_risk(descriptor, call.args)
        summary, sanitized = self._sanitize_for_approval(call, descriptor)

        if category == "deny":
            policy = PolicyDecision(
                action="deny",
                reason="Tool is denied because safety descriptor lookup failed (fail-closed).",
                matched_rule="descriptor=deny",
            )
        elif category == "shell":
            policy = evaluate_policy_for_shell_exec(
                argv=argv,
                risk=risk,
                safety=self._safety_config,
                sandbox_permissions=self._sandbox_permissions,
            )
        elif category == "file":
            mode = str(getattr(self._safety_config, "mode", "ask") or "ask").strip().lower()
            if mode == "deny":
                policy = PolicyDecision(
                    action="deny",
                    reason="Tool is denied by safety.mode=deny.",
                    matched_rule="mode=deny",
                )
            elif mode == "allow":
                policy = PolicyDecision(
                    action="allow",
                    reason="Allowed by safety.mode=allow.",
                    matched_rule="mode=allow",
                )
            else:
                policy = PolicyDecision(
                    action="ask",
                    reason="Approval required by safety.mode=ask.",
                    matched_rule="mode=ask",
                )
        elif category == "custom":
            policy = evaluate_policy_for_custom_tool(tool=call.name, safety=self._safety_config)
        else:
            policy = PolicyDecision(action="ask", reason="Unknown policy category", matched_rule=None)

        return GateDecision(
            action=policy.action,
            reason=policy.reason,
            summary=summary,
            sanitized_request=sanitized,
            matched_rule=policy.matched_rule,
        )

    def build_denied_result(self, call: ToolCall, decision: GateDecision) -> ToolResult:
        """基于门禁决策构造 denied ToolResult。"""

        denied_payload = ToolResultPayload(
            ok=False,
            stdout="",
            stderr=str(decision.reason or "policy denied"),
            exit_code=None,
            duration_ms=0,
            truncated=False,
            data={
                "tool": call.name,
                "reason": str(decision.matched_rule or decision.reason or "policy"),
            },
            error_kind="permission",
            retryable=False,
            retry_after_ms=None,
        )
        return ToolResult.from_payload(denied_payload, message="policy denied")

    def sanitize_for_approval(self, call: ToolCall) -> tuple[str, Dict[str, Any]]:
        """获取审批阶段的脱敏摘要/请求。"""

        descriptor = self._get_descriptor(call.name)
        return self._sanitize_for_approval(call, descriptor)

    def sanitize_for_event(self, call: ToolCall, **ctx: Any) -> Dict[str, Any]:
        """获取 WAL 事件使用的脱敏参数。"""

        descriptor = self._get_descriptor(call.name)
        try:
            payload = descriptor.sanitize_for_event(call.args, skills_manager=self._skills_manager, **ctx)
        except TypeError:
            try:
                payload = descriptor.sanitize_for_event(call.args, **ctx)
            except TypeError:
                payload = descriptor.sanitize_for_event(call.args)
        return dict(payload) if isinstance(payload, dict) else {}

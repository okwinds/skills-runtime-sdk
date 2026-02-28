"""
ToolRegistry：工具注册表与派发（dispatch）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools.md`（注册表 API 与冲突策略）
- `docs/specs/skills-runtime-sdk/docs/core-contracts.md`（事件结构：AgentEvent）

本模块提供：
- 注册：`register/get_spec/list_specs`
- 执行：`dispatch(ToolCall) -> ToolResult`
- 事件落盘（WAL）：dispatch 过程写入 `tool_call_requested` / `tool_call_started` / `tool_call_finished` 事件
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, TYPE_CHECKING, Union

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.errors import UserError
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.state.jsonl_wal import JsonlWal
from skills_runtime.state.wal_emitter import WalEmitter
from skills_runtime.tools.protocol import HumanIOProvider, ToolCall, ToolResult, ToolResultPayload, ToolSpec

try:  # 避免循环依赖；Executor 仅在 builtin tools 场景使用
    from skills_runtime.core.executor import Executor
except Exception:  # pragma: no cover
    Executor = object  # type: ignore[assignment,misc]

try:  # exec sessions 是可选能力（仅 Phase 5 parity tools 使用）
    from skills_runtime.core.exec_sessions import ExecSessionManager
except Exception:  # pragma: no cover
    ExecSessionManager = object  # type: ignore[assignment,misc]

if TYPE_CHECKING:  # pragma: no cover
    from skills_runtime.skills.manager import SkillsManager


ToolHandler = Callable[[ToolCall, "ToolExecutionContext"], ToolResult]
EventSink = Callable[[AgentEvent], None]


def _sanitize_tool_call_arguments_for_event(
    tool: str,
    *,
    args: Dict[str, Any],
    redaction_values: Sequence[str] = (),
) -> Dict[str, Any]:
    """
    将 tool args 转成“可观测但不泄露 secrets”的事件表示（用于 WAL）。

    Gate（最小）：
    - env 只记录 keys，不记录 values
    - file_write.content 不落盘（只记录 bytes + sha256）
    - 字符串字段 best-effort 替换已知 secret values
    """

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
                if key == "env" and isinstance(v, dict):
                    out["env_keys"] = sorted(str(kk) for kk in v.keys())
                    continue
                out[key] = _sanitize_obj(v)
            return out
        return obj

    if tool == "file_write":
        content = args.get("content")
        bytes_count: Optional[int] = None
        sha256: Optional[str] = None
        if isinstance(content, str):
            b = content.encode("utf-8")
            bytes_count = len(b)
            sha256 = hashlib.sha256(b).hexdigest()
        base = dict(args)
        base.pop("content", None)
        base["bytes"] = bytes_count
        base["content_sha256"] = sha256
        return _sanitize_obj(base)

    return _sanitize_obj(dict(args))


@dataclass
class ToolExecutionContext:
    """
    Tool 执行上下文（派发层注入）。

    字段：
    - workspace_root：相对路径解析基准目录
    - run_id：用于 WAL 事件关联
    - wal：可选；为 None 时不落盘
    - event_emitter：可选；统一事件管道（WAL append + hooks + stream）。若提供，则 emit_event 不直接调用 wal.append
    - executor：可选；shell_exec 需要
    - human_io：可选；ask_human 需要
    - default_timeout_ms：tool 未提供 timeout_ms 时的默认值（shell_exec）
    - max_file_bytes：file_read 默认最大读取字节数
    - sandbox_policy_default：tool 未显式指定 sandbox 时的默认策略（shell_exec 使用）
    - sandbox_adapter：OS sandbox adapter（可选；restricted 且缺失时应报错）
    - emit_tool_events：是否由 ToolRegistry 负责写入 tool_call_* 事件（默认 true）
    - event_sink：可选；事件旁路输出（用于 streaming），不会替代 wal
    - skills_manager：可选；skills 外延能力（skill_exec/ref-read）需要
    - exec_sessions：可选；exec_command/write_stdin 需要（PTY-backed）
    - web_search_provider：可选；web_search 需要（默认 fail-closed）
    - collab_manager：可选；spawn_agent/wait/send_input/close_agent/resume_agent 需要
    """

    workspace_root: Path
    run_id: str
    wal: Optional[JsonlWal] = None
    event_emitter: Optional[WalEmitter] = None
    executor: Optional[Executor] = None
    human_io: Optional[HumanIOProvider] = None
    env: Optional[Dict[str, str]] = None
    cancel_checker: Optional[Callable[[], bool]] = None
    redaction_values: Optional[Union[Sequence[str], Callable[[], Sequence[str]]]] = None
    default_timeout_ms: int = 60_000
    max_file_bytes: int = 256 * 1024
    sandbox_policy_default: str = "none"
    sandbox_adapter: Optional[object] = None
    emit_tool_events: bool = True
    event_sink: Optional[EventSink] = None
    skills_manager: Optional["SkillsManager"] = None
    exec_sessions: Optional[ExecSessionManager] = None
    web_search_provider: Optional[object] = None
    collab_manager: Optional[object] = None

    def emit_event(self, event: AgentEvent) -> None:
        """
        写入事件（WAL + 可选 sink）。

        说明：
        - wal：用于持久化/回放
        - event_sink：用于实时推送（例如 Web SSE）；不建议在 sink 中做重 IO
        """

        if self.event_emitter is not None:
            # 统一事件基座：由 emitter 决定 WAL/hook/stream 的顺序。
            # 注意：这里使用 append-only，避免 tool 执行期的旁路事件插入 approvals 序列。
            self.event_emitter.append(event)
            if self.event_sink is not None:
                self.event_sink(event)
            return

        # 兼容：旧路径（不经过 hooks）。
        if self.wal is not None:
            self.wal.append(event)
        if self.event_sink is not None:
            self.event_sink(event)

    def resolve_path(self, path: str) -> Path:
        """
        将用户提供的 path 解析为绝对路径，并限制在 workspace_root 下。

        参数：
        - path：相对或绝对路径

        返回：
        - 解析后的绝对路径（已 resolve）

        异常：
        - `UserError`：当路径逃逸 workspace_root 时抛出
        """

        root = Path(self.workspace_root).resolve()
        p = Path(path)
        if not p.is_absolute():
            p = root / p
        p = p.resolve()
        if not p.is_relative_to(root):
            raise UserError(f"禁止访问 workspace_root 之外的路径：{p}")
        return p

    def merged_env(self, extra: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        """
        合并 tool env（ctx.env + extra）。

        规则：
        - ctx.env 作为 base（session-only env_store）
        - extra 覆盖 ctx.env
        - 若两者都为空，返回 None
        """

        base = dict(self.env or {})
        if extra:
            base.update({str(k): str(v) for k, v in extra.items()})
        return base if base else None

    def get_redaction_values(self) -> Sequence[str]:
        """返回用于脱敏的 values 列表（支持 callable 动态获取）。"""

        rv = self.redaction_values
        if rv is None:
            return []
        if callable(rv):
            try:
                return list(rv())
            except Exception:
                # 防御性兜底：redaction_values 由外部注入，可能抛出任意异常；fail-open 返回空列表。
                return []
        return list(rv)

    def redact_text(self, text: str) -> str:
        """
        对输出文本做最小脱敏：把已知 secret value 替换为 `<redacted>`。

        说明：
        - 仅用于降低“工具输出意外回显 secrets”的风险；
        - 不保证绝对安全（例如变形/截断/编码后的 secret 无法匹配）。
        """

        if not text:
            return text
        values = list(self.get_redaction_values() or [])
        for v in values:
            if not isinstance(v, str):
                continue
            vv = v.strip()
            if len(vv) < 4:
                continue
            text = text.replace(vv, "<redacted>")
        return text


class ToolRegistry:
    """工具注册表（Phase 2：最小实现）。"""

    def __init__(self, *, ctx: ToolExecutionContext) -> None:
        """创建注册表并绑定执行上下文（包含 WAL、executor、env_store 等依赖）。"""

        self._ctx = ctx
        self._specs: Dict[str, ToolSpec] = {}
        self._handlers: Dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler, *, override: bool = False) -> None:
        """
        注册工具。

        参数：
        - spec：工具规格
        - handler：工具执行函数
        - override：是否允许覆盖同名工具；默认 False（重复注册抛 UserError）
        """

        name = spec.name
        if name in self._specs and not override:
            raise UserError(f"重复注册 tool：{name}")
        self._specs[name] = spec
        self._handlers[name] = handler

    def get_spec(self, name: str) -> ToolSpec:
        """获取工具规格；不存在则抛 `UserError`。"""

        try:
            return self._specs[name]
        except KeyError as e:
            raise UserError(f"未注册的 tool：{name}") from e

    def list_specs(self) -> list[ToolSpec]:
        """按注册顺序返回所有工具规格。"""

        return list(self._specs.values())

    def dispatch(self, call: ToolCall, *, turn_id: Optional[str] = None, step_id: Optional[str] = None) -> ToolResult:
        """
        派发执行一个 ToolCall，并在 WAL 中记录 tool_call_* 事件。

        参数：
        - call：工具调用（已解析 arguments）
        - turn_id/step_id：可选，用于与更高层 turn/step 关联

        返回：
        - ToolResult
        """

        if self._ctx.emit_tool_events:
            self._append_event(
                type_="tool_call_requested",
                turn_id=turn_id,
                step_id=step_id,
                payload={
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": _sanitize_tool_call_arguments_for_event(
                        call.name, args=call.args, redaction_values=self._ctx.get_redaction_values()
                    ),
                },
            )

        handler = self._handlers.get(call.name)
        if handler is None:
            result = ToolResult.error_payload(
                error_kind="not_found",
                stderr=f"未注册的 tool：{call.name}",
                data={"tool": call.name},
            )
            if self._ctx.emit_tool_events:
                self._append_tool_result(call, result, turn_id=turn_id, step_id=step_id)
            return result

        if self._ctx.emit_tool_events:
            self._append_event(
                type_="tool_call_started",
                turn_id=turn_id,
                step_id=step_id,
                payload={"call_id": call.call_id, "tool": call.name},
            )

        try:
            result = handler(call, self._ctx)
        except UserError as e:
            result = ToolResult.error_payload(error_kind="validation", stderr=str(e))
        except Exception as e:  # pragma: no cover（防御性兜底）
            result = ToolResult.error_payload(error_kind="unknown", stderr=str(e))

        result = self._redact_tool_result(result)

        if self._ctx.emit_tool_events:
            self._append_tool_result(call, result, turn_id=turn_id, step_id=step_id)
        return result

    def _redact_tool_result(self, result: ToolResult) -> ToolResult:
        """
        对 ToolResult 做最小脱敏（stdout/stderr/content/details）。

        说明：
        - 脱敏值来自 `ToolExecutionContext.redaction_values`；
        - 仅用于降低“工具输出意外回显 secrets”的风险，不保证绝对安全。
        """

        values = list(self._ctx.get_redaction_values() or [])
        if not values:
            return result

        def _redact_obj(obj: Any) -> Any:
            """递归脱敏对象结构中的字符串字段（用于 details / json content）。"""

            if isinstance(obj, str):
                return self._ctx.redact_text(obj)
            if isinstance(obj, list):
                return [_redact_obj(x) for x in obj]
            if isinstance(obj, dict):
                return {k: _redact_obj(v) for k, v in obj.items()}
            return obj

        details = _redact_obj(result.details) if result.details is not None else None
        message = self._ctx.redact_text(result.message or "") if result.message else None
        content = result.content
        try:
            import json

            obj = json.loads(content)
            if isinstance(obj, (dict, list)):
                red = _redact_obj(obj)
                content = json.dumps(red, ensure_ascii=False)
        except json.JSONDecodeError:
            content = self._ctx.redact_text(content)

        return ToolResult(
            ok=result.ok,
            content=content,
            error_kind=result.error_kind,
            message=message,
            details=details,
        )

    def _append_tool_result(self, call: ToolCall, result: ToolResult, *, turn_id: Optional[str], step_id: Optional[str]) -> None:
        """把 ToolResult 规范化为 payload 并写入 `tool_call_finished` 事件。"""

        payload_result: Dict[str, Any]
        if result.details is not None:
            payload_result = result.details
        else:
            payload_result = ToolResultPayload(ok=result.ok, error_kind=result.error_kind).model_dump(exclude_none=True)

        self._append_event(
            type_="tool_call_finished",
            turn_id=turn_id,
            step_id=step_id,
            payload={"call_id": call.call_id, "tool": call.name, "result": payload_result},
        )

    def _append_event(
        self,
        *,
        type_: str,
        payload: Dict[str, Any],
        turn_id: Optional[str],
        step_id: Optional[str],
    ) -> None:
        """向 WAL 追加一条事件（若未配置 wal 则 no-op）。"""

        self._ctx.emit_event(
            AgentEvent(
                type=type_,
                timestamp=now_rfc3339(),
                run_id=self._ctx.run_id,
                turn_id=turn_id,
                step_id=step_id,
                payload=payload,
            )
        )

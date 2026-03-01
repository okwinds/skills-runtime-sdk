"""
RunContext：单次 run 的共享可变状态容器（用于拆分 agent_loop.py）。

目标：
- 将 `agent_loop.AgentLoop._run_stream_async` 中的大量 nonlocal 状态与闭包迁移为显式结构；
- 便于在其它模块中复用/测试（context_recovery、tool_orchestration 等）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.core.utils import now_rfc3339
from skills_runtime.state.wal_emitter import WalEmitter
from skills_runtime.state.wal_protocol import WalBackend


@dataclass
class RunContext:
    """
    单次 run 的共享上下文。

    注意：
    - `history` 为 LLM messages 的累积（role/content/tool/tool_calls），由主 loop 与工具编排共同维护。
    - 所有事件必须通过 `emit_event` 输出，保证 WAL append → hooks → stream 的顺序一致。
    """

    run_id: str
    run_dir: Path
    wal: WalBackend
    wal_locator: str
    wal_emitter: WalEmitter
    history: List[Dict[str, Any]]
    artifacts_dir: Path

    compactions_performed: int = 0
    compaction_artifacts: List[str] = field(default_factory=list)
    terminal_notices: List[Dict[str, Any]] = field(default_factory=list)

    max_steps: int = 100
    max_wall_time_sec: Optional[float] = None
    context_recovery_mode: str = "compact_first"
    max_compactions_per_run: int = 5
    ask_first_fallback_mode: str = "compact_first"
    compaction_history_max_chars: int = 50_000
    compaction_keep_last_messages: int = 10
    increase_budget_extra_steps: int = 50
    increase_budget_extra_wall_time_sec: int = 300

    def emit_event(self, ev: AgentEvent) -> None:
        """统一事件出口：WAL append（如启用）→ hooks → stream（保持顺序一致）。"""

        self.wal_emitter.emit(ev)

    def emit_cancelled(self) -> None:
        """发出 `run_cancelled` 事件并包含 wal_locator，供调用方定位审计日志。"""

        self.emit_event(
            AgentEvent(
                type="run_cancelled",
                timestamp=now_rfc3339(),
                run_id=self.run_id,
                payload={"message": "cancelled by user", "wal_locator": self.wal_locator},
            )
        )

    def emit_budget_exceeded(self, *, message: str) -> None:
        """
        发出预算耗尽的 `run_failed` 事件（Phase 2：框架级严格 fail-fast）。

        约束：
        - error_kind 固定为 budget_exceeded
        - retryable 固定为 false
        """

        self.emit_event(
            AgentEvent(
                type="run_failed",
                timestamp=now_rfc3339(),
                run_id=self.run_id,
                payload={
                    "error_kind": "budget_exceeded",
                    "message": message,
                    "retryable": False,
                    "wal_locator": self.wal_locator,
                },
            )
        )

    def write_text_artifact(self, *, kind: str, content: str) -> str:
        """将文本写入 run artifacts 目录并返回文件路径（字符串）。"""

        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        idx = len(self.compaction_artifacts) + 1
        name = f"{idx:03d}_{str(kind or 'artifact')}.md"
        p = (self.artifacts_dir / name).resolve()
        p.write_text(str(content or ""), encoding="utf-8")
        return str(p)

    def refresh_terminal_notices(self) -> None:
        """
        刷新终态 notices（metadata），但不拼接进 final_output。

        说明：
        - 当前仅用于 compaction 的明显提示；
        - 若后续引入其它 notices 类型，建议同样在此处集中汇总。
        """

        self.terminal_notices.clear()
        if self.compactions_performed <= 0:
            return
        self.terminal_notices.append(
            {
                "kind": "context_compacted",
                "count": int(self.compactions_performed),
                "message": f"本次运行发生过 {int(self.compactions_performed)} 次上下文压缩；摘要可能遗漏细节。",
                "suggestion": "建议将任务拆分或开新 run，并把 handoff 摘要作为新 run 的起始上下文。",
            }
        )


__all__ = ["RunContext"]


"""
LoopController：Agent Loop 的计数/预算/取消控制（internal）。

目标：
- 将“turn/step 计数、max_steps、wall time、cancel_checker、重复 denied guard”等状态收敛到单一对象，
  以便后续把 Agent 内核进一步拆分（不改变对外语义）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/agent-loop.md`
- `docs/specs/skills-runtime-sdk/docs/safety.md`（重复 denied 的 loop guard）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass
class LoopController:
    """
    LoopController（internal）。

    字段：
    - max_steps：最大允许执行的 tool call 次数（仅统计“实际开始执行”的 tool call）
    - max_wall_time_sec：wall time 预算（None 表示不限制）
    - started_monotonic：起始 monotonic 时间戳（用于 wall time 计算）
    - cancel_checker：取消检测回调（返回 True 表示应尽快停止；异常时 fail-open）
    - denied_approvals_by_key：同一 approval_key 的 denied 次数统计（用于 loop guard）
    """

    max_steps: int
    max_wall_time_sec: Optional[float]
    started_monotonic: float
    cancel_checker: Optional[Callable[[], bool]] = None
    denied_approvals_by_key: Dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """初始化内部计数器与默认字段。"""

        if self.denied_approvals_by_key is None:
            self.denied_approvals_by_key = {}
        self._turn = 0
        self._step = 0
        self._steps_executed = 0

    def next_turn_id(self) -> str:
        """推进 turn 计数并返回 turn_id（形如 `turn_1`）。"""

        self._turn += 1
        return f"turn_{self._turn}"

    def next_step_id(self) -> str:
        """推进 step 计数并返回 step_id（形如 `step_1`）。"""

        self._step += 1
        return f"step_{self._step}"

    def is_cancelled(self) -> bool:
        """
        检查是否需要取消本次 run。

        约束：
        - 异常时 fail-open：返回 False。
        """

        if self.cancel_checker is None:
            return False
        try:
            return bool(self.cancel_checker())
        except Exception:
            return False

    def wall_time_exceeded(self) -> bool:
        """检查 wall time 预算是否耗尽（未配置则返回 False）。"""

        if self.max_wall_time_sec is None:
            return False
        elapsed = time.monotonic() - float(self.started_monotonic)
        return elapsed > float(self.max_wall_time_sec)

    def try_consume_tool_step(self) -> bool:
        """
        尝试消耗一次 tool call 执行预算（max_steps）。

        说明：
        - 仅在“实际开始执行 tool call”时调用；
        - policy deny / approval denied 不应调用本方法。

        返回：
        - True：预算充足，且已消耗一次
        - False：预算耗尽，且未消耗
        """

        if self._steps_executed >= int(self.max_steps):
            return False
        self._steps_executed += 1
        return True

    def record_denied_approval(self, approval_key: str) -> int:
        """
        记录一次 approval denied，并返回该 key 的累计 denied 次数。

        参数：
        - approval_key：审批缓存 key

        返回：
        - denied 次数（包含本次）
        """

        k = str(approval_key or "")
        self.denied_approvals_by_key[k] = int(self.denied_approvals_by_key.get(k, 0)) + 1
        return int(self.denied_approvals_by_key.get(k, 0))

    def should_abort_due_to_repeated_denial(self, *, approval_key: str, threshold: int = 2) -> bool:
        """
        判断是否应因同一 approval_key 的重复 denied 而中止（loop guard）。

        参数：
        - approval_key：审批缓存 key
        - threshold：阈值（默认 2；即第二次 denied 触发中止）

        返回：
        - True：应中止
        - False：继续
        """

        k = str(approval_key or "")
        return int(self.denied_approvals_by_key.get(k, 0)) >= int(threshold)


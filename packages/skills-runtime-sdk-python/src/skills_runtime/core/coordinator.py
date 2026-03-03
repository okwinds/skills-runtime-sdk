"""
Coordinator（多 Agent 调度器，Phase 2 最小实现）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/api-reference.md` §2
- `docs/specs/skills-runtime-sdk/docs/multi-agent.md`（Phase 2：同步调用 child 并回灌 summary）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Optional

from skills_runtime.core.agent import Agent, RunResult


@dataclass(frozen=True)
class ChildResult:
    """
    子 agent 的结构化结果（Phase 2 最小）。

    字段：
    - summary：子 agent 的最终输出（作为主 agent 的上下文摘要注入）。
    - status：completed/failed/cancelled（透传 RunResult.status）。
    - artifacts：子 agent 产物路径列表（Phase 2 可能为空）。
    - wal_locator：推荐字段；WAL 定位符（用于审计/调试；可能为空字符串）。
    """

    summary: str
    status: str
    artifacts: List[str]
    wal_locator: str


class Coordinator:
    """
    多 Agent 协作入口（Phase 2 最小）。

    设计说明：
    - Phase 2 不做“智能拆分”；仅提供可测试的同步协作原语。
    - `run(...)` 默认只使用主 agent（`agents[0]`）。
    - `run_with_child(...)` 提供“child → summary → 注入主 agent initial_history → 继续主任务”的最小闭环。
    """

    def __init__(self, *, agents: List[Agent]) -> None:
        """
        创建 Coordinator。

        参数：
        - agents：Agent 列表；`agents[0]` 作为主 agent，其它可作为子 agent。

        异常：
        - ValueError：agents 为空或包含非 Agent 对象。
        """

        if not isinstance(agents, list) or not agents:
            raise ValueError("Coordinator 需要非空 agents 列表")
        for a in agents:
            if not isinstance(a, Agent):
                raise ValueError("Coordinator.agents 必须全部为 Agent 实例")
        self._agents = list(agents)

    @property
    def agents(self) -> List[Agent]:
        """返回 Coordinator 管理的 agents（按传入顺序）。"""

        return list(self._agents)

    def run(self, task: str) -> RunResult:
        """
        运行主任务（仅主 agent）。

        参数：
        - task：用户任务文本。

        返回：
        - RunResult：主 agent 的结果。
        """

        return self._agents[0].run(task)

    def run_child_task(self, task: str, *, child_index: int = 1) -> ChildResult:
        """
        同步运行一个子 agent，并返回结构化摘要。

        参数：
        - task：子任务文本。
        - child_index：子 agent 下标（默认 1，即 `agents[1]`）。

        返回：
        - ChildResult：包含 summary/status/artifacts/wal_locator。

        异常：
        - ValueError：child_index 越界。
        """

        if child_index < 0 or child_index >= len(self._agents):
            raise ValueError(f"child_index 越界：{child_index}")
        child = self._agents[child_index]
        r = child.run(task)
        return ChildResult(
            summary=str(r.final_output or ""),
            status=str(r.status or ""),
            artifacts=list(r.artifacts or []),
            wal_locator=str(r.wal_locator or ""),
        )

    def run_with_child(
        self,
        task: str,
        *,
        child_task: str,
        child_index: int = 1,
        primary_initial_history: Optional[List[dict]] = None,
    ) -> RunResult:
        """
        运行“子任务 → 回灌 summary → 主任务”的最小协作闭环。

        参数：
        - task：主任务文本（由主 agent 执行）。
        - child_task：子任务文本（由子 agent 执行）。
        - child_index：子 agent 下标（默认 1）。
        - primary_initial_history：可选的主 agent 初始历史（role/content 形态）；会与 child summary 注入合并。

        返回：
        - RunResult：主 agent 的结果。
        """

        child = self.run_child_task(child_task, child_index=child_index)

        injected_summary = (
            "[ChildAgent Summary]\n"
            f"child_index: {child_index}\n"
            f"status: {child.status}\n"
            f"wal_locator: {child.wal_locator}\n"
            f"summary: {child.summary}"
        )
        history: List[dict] = []
        if primary_initial_history:
            history.extend(primary_initial_history)
        history.append({"role": "assistant", "content": injected_summary})
        return self._agents[0].run(task, initial_history=history)

    async def run_children_concurrent(
        self,
        child_tasks: List[str],
        *,
        start_index: int = 1,
    ) -> List[ChildResult]:
        """
        并发运行多个子 agent（asyncio.gather），每个子任务对应 `child_tasks[i]`。

        参数：
        - child_tasks：子任务文本列表；每个任务由 `agents[start_index + i]` 执行。
        - start_index：子 agent 起始下标（默认 1，即 `agents[1]` 对应 `child_tasks[0]`）。

        返回：
        - List[ChildResult]：按 child_tasks 顺序排列的结果列表。

        异常：
        - ValueError：child_tasks 为空，或子 agent 下标越界。
        """

        if not child_tasks:
            raise ValueError("child_tasks 不能为空")
        end_index = start_index + len(child_tasks)
        if end_index > len(self._agents):
            raise ValueError(
                f"agent 数量不足：需要 agents[{start_index}:{end_index}]，"
                f"当前只有 {len(self._agents)} 个"
            )

        async def _run_one(agent: Agent, task: str) -> ChildResult:
            """运行单个子 agent 并收集终态结果。"""
            final_output = ""
            status = "completed"
            wal_locator = ""
            async for ev in agent.run_stream_async(task):
                if ev.type == "run_completed":
                    final_output = str(ev.payload.get("final_output") or "")
                    wal_locator = str(ev.payload.get("wal_locator") or "")
                    status = "completed"
                elif ev.type == "run_failed":
                    final_output = str(ev.payload.get("message") or "")
                    wal_locator = str(ev.payload.get("wal_locator") or wal_locator or "")
                    status = "failed"
                elif ev.type == "run_cancelled":
                    final_output = str(ev.payload.get("message") or "")
                    wal_locator = str(ev.payload.get("wal_locator") or wal_locator or "")
                    status = "cancelled"
            return ChildResult(summary=final_output, status=status, artifacts=[], wal_locator=wal_locator)

        agents_and_tasks = [
            (self._agents[start_index + i], child_tasks[i])
            for i in range(len(child_tasks))
        ]
        results = await asyncio.gather(*[_run_one(agent, task) for agent, task in agents_and_tasks])
        return list(results)

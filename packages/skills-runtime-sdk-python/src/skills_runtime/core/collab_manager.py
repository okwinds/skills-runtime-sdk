"""
Collaboration manager（tool-level multi-agent primitives）。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-collab.md`

说明：
- 本模块提供“单进程/单 run 生命周期内”的子 agent 管理能力：
  - spawn/wait/send/close/resume
- 不做跨进程持久化；如需跨进程 resume，应在更高阶段引入独立 runtime/服务化。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from queue import Queue
from typing import Callable, Optional


ChildAgentRunner = Callable[[str, "ChildAgentContext"], str]


@dataclass
class ChildAgentContext:
    """
    子 agent 执行上下文（最小可用）。

    字段：
    - inbox：输入队列（send_input 投递）
    - cancel_event：取消信号（close_agent 触发）
    """

    inbox: Queue[str]
    cancel_event: threading.Event


@dataclass
class ChildAgentHandle:
    """子 agent 句柄（内存态）。"""

    id: str
    agent_type: str
    inbox: Queue[str]
    cancel_event: threading.Event
    thread: threading.Thread
    status: str = "running"  # running|completed|failed|cancelled
    final_output: Optional[str] = None
    error: Optional[str] = None


class CollabManager:
    """
    子 agent 管理器（最小实现）。

    约束：
    - 所有操作线程安全（锁粒度尽量小）
    - 子 agent 由 runner 负责具体工作；本 manager 只负责生命周期管理
    """

    def __init__(self, *, runner: ChildAgentRunner) -> None:
        """
        创建子 agent 管理器。

        参数：
        - runner：子 agent 的执行函数（输入初始 message + context，返回 final_output 字符串）
        """

        self._runner = runner
        self._lock = threading.Lock()
        self._agents: dict[str, ChildAgentHandle] = {}

    def spawn(self, *, message: str, agent_type: str = "default") -> ChildAgentHandle:
        """
        生成子 agent 并启动后台执行。

        参数：
        - message：初始任务文本
        - agent_type：类型（最小实现仅记录；不强制）
        """

        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")

        agent_id = uuid.uuid4().hex
        inbox: Queue[str] = Queue()
        cancel_event = threading.Event()
        ctx = ChildAgentContext(inbox=inbox, cancel_event=cancel_event)

        handle = ChildAgentHandle(
            id=agent_id,
            agent_type=str(agent_type or "default"),
            inbox=inbox,
            cancel_event=cancel_event,
            thread=threading.Thread(target=self._run_child, args=(agent_id, message, ctx), daemon=True),
        )

        with self._lock:
            self._agents[agent_id] = handle
        handle.thread.start()
        return handle

    def _run_child(self, agent_id: str, message: str, ctx: ChildAgentContext) -> None:
        """
        子线程入口：运行 runner 并写回状态。

        参数：
        - agent_id：子 agent id
        - message：初始任务文本
        - ctx：子 agent 上下文（inbox/cancel_event）
        """

        try:
            if ctx.cancel_event.is_set():
                self._set_status(agent_id, status="cancelled", final_output=None, error=None)
                return
            out = self._runner(message, ctx)
            if ctx.cancel_event.is_set():
                self._set_status(agent_id, status="cancelled", final_output=None, error=None)
                return
            self._set_status(agent_id, status="completed", final_output=str(out), error=None)
        except Exception as exc:
            self._set_status(agent_id, status="failed", final_output=None, error=str(exc))

    def _set_status(self, agent_id: str, *, status: str, final_output: Optional[str], error: Optional[str]) -> None:
        """
        更新子 agent 状态（线程安全）。

        参数：
        - agent_id：子 agent id
        - status：running/completed/failed/cancelled
        - final_output：完成输出（仅 completed 时建议填充）
        - error：失败原因（仅 failed 时建议填充）
        """

        with self._lock:
            h = self._agents.get(agent_id)
            if h is None:
                return
            h.status = status
            h.final_output = final_output
            h.error = error

    def get(self, agent_id: str) -> Optional[ChildAgentHandle]:
        """获取子 agent 句柄（不存在返回 None）。"""

        with self._lock:
            return self._agents.get(str(agent_id))

    def wait(self, *, ids: list[str], timeout_ms: Optional[int] = None) -> list[ChildAgentHandle]:
        """
        等待一组子 agent（或超时返回当前状态）。

        参数：
        - ids：子 agent id 列表（必须都存在）
        - timeout_ms：总超时（毫秒）
        """

        if not ids:
            raise ValueError("ids must not be empty")

        handles: list[ChildAgentHandle] = []
        with self._lock:
            missing = [i for i in ids if str(i) not in self._agents]
            if missing:
                raise KeyError(f"unknown ids: {missing}")
            handles = [self._agents[str(i)] for i in ids]

        deadline = None if timeout_ms is None else (time.monotonic() + timeout_ms / 1000.0)
        for h in handles:
            if not h.thread.is_alive():
                continue
            if deadline is None:
                h.thread.join()
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                h.thread.join(timeout=remaining)

        # 返回快照（避免暴露内部可变对象给调用方写入）
        out: list[ChildAgentHandle] = []
        with self._lock:
            for h in handles:
                cur = self._agents.get(h.id)
                if cur is None:
                    continue
                out.append(
                    ChildAgentHandle(
                        id=cur.id,
                        agent_type=cur.agent_type,
                        inbox=cur.inbox,
                        cancel_event=cur.cancel_event,
                        thread=cur.thread,
                        status=cur.status,
                        final_output=cur.final_output,
                        error=cur.error,
                    )
                )
        return out

    def send_input(self, *, agent_id: str, message: str) -> None:
        """向子 agent 投递输入（最小实现：inbox put）。"""

        if not isinstance(message, str):
            raise ValueError("message must be a string")
        h = self.get(str(agent_id))
        if h is None:
            raise KeyError("agent not found")
        h.inbox.put(str(message))

    def close(self, *, agent_id: str) -> None:
        """取消/关闭子 agent（最小：设置 cancel_event）。"""

        h = self.get(str(agent_id))
        if h is None:
            raise KeyError("agent not found")
        h.cancel_event.set()
        self._set_status(h.id, status="cancelled", final_output=None, error=None)

    def resume(self, *, agent_id: str) -> ChildAgentHandle:
        """恢复/查询子 agent 状态（最小：no-op，返回当前句柄快照）。"""

        h = self.get(str(agent_id))
        if h is None:
            raise KeyError("agent not found")
        return ChildAgentHandle(
            id=h.id,
            agent_type=h.agent_type,
            inbox=h.inbox,
            cancel_event=h.cancel_event,
            thread=h.thread,
            status=h.status,
            final_output=h.final_output,
            error=h.error,
        )

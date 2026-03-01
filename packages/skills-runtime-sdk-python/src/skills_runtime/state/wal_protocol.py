"""
WAL 后端协议（WalBackend）与内存实现（InMemoryWal）。

设计目标：
- 让 Agent 的事件持久化不再硬绑本地文件系统；
- 支持云端无人值守场景下把 WAL 写入内存/远端存储；
- 保持接口极简，可用于离线回归与结构化审计。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Protocol, runtime_checkable

from skills_runtime.core.contracts import AgentEvent


@runtime_checkable
class WalBackend(Protocol):
    """
    WAL 后端协议（最小集合）。

    约束：
    - `append` MUST 返回 0-based index，便于回放与定位。
    - `iter_events` MUST 按写入顺序返回事件。
    - `locator` MUST 返回稳定的定位符字符串（可为路径或 URI）。
    """

    def append(self, event: AgentEvent) -> int:
        """追加一条事件并返回其 index（0-based）。"""

        ...

    def iter_events(self, *, run_id: Optional[str] = None) -> Iterator[AgentEvent]:
        """按写入顺序迭代 WAL 中的事件（可选按 run_id 过滤）。"""

        ...

    def locator(self) -> str:
        """
        返回 WAL 定位符（locator）。

        说明：
        - 对文件型 WAL：建议返回绝对路径字符串。
        - 对远端型 WAL：建议返回 `wal://...` URI 或其它可稳定关联的字符串。
        """

        ...


@dataclass
class InMemoryWal:
    """
    内存 WAL 实现（用于云端/测试）。

    约束：
    - 仅保证同进程内可回放；跨进程恢复需要远端 WalBackend 实现。
    - append/iter_events 线程安全（最小实现：锁保护 + copy-on-iter）。
    """

    locator_str: str = "wal://in-memory"
    _events: List[AgentEvent] = field(default_factory=list, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def locator(self) -> str:
        """返回本 WAL 的定位符字符串。"""

        return str(self.locator_str or "wal://in-memory")

    def append(self, event: AgentEvent) -> int:
        """追加一条事件并返回其 index（0-based）。"""

        with self._lock:
            idx = len(self._events)
            self._events.append(event)
            return idx

    def iter_events(self, *, run_id: Optional[str] = None) -> Iterator[AgentEvent]:
        """按写入顺序迭代 WAL 中的事件（返回快照，可选按 run_id 过滤）。"""

        with self._lock:
            snap = list(self._events)
        if run_id is None:
            return iter(snap)
        return (ev for ev in snap if ev.run_id == run_id)

"""
WalEmitter：事件落盘 + 对外推送的统一出口（internal）。

说明：
- Agent Loop 需要一个“单点出口”来保证事件顺序一致：
  1) 先写入 WAL（append-only）
  2) 再推送给调用方（stream）

约束：
- 本模块不负责事件的业务语义，只负责“如何发出”；
- 旁路事件（已由其它组件写入 WAL）可以只做 stream（避免重复落盘）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_sdk.core.contracts import AgentEvent
from agent_sdk.state.jsonl_wal import JsonlWal


EventStream = Callable[[AgentEvent], None]


@dataclass(frozen=True)
class WalEmitter:
    """
    WalEmitter（internal）。

    字段：
    - wal：JSONL WAL（append-only）
    - stream：对外事件流回调（例如 run_stream 的 yield 管道）
    """

    wal: JsonlWal
    stream: EventStream

    def emit(self, ev: AgentEvent) -> None:
        """
        统一事件出口：先追加到 WAL，再推送给调用方（保持顺序一致）。

        参数：
        - ev：AgentEvent
        """

        self.wal.append(ev)
        self.stream(ev)

    def stream_only(self, ev: AgentEvent) -> None:
        """
        仅推送事件到调用方，不追加到 WAL。

        用途：
        - 某些事件已经由下游组件写入 WAL（例如 ToolExecutionContext.emit_event），此处只负责补齐 stream。
        """

        self.stream(ev)


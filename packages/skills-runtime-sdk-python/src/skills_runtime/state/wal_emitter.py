"""
WalEmitter：事件落盘 + hooks + 对外推送的统一出口（internal）。

说明：
- Agent Loop 需要一个“单点出口”来保证事件顺序一致：
  1) 先写入 WAL（append-only）
  2) 再调用 hooks（可观测性）
  3) 再推送给调用方（stream）

约束：
- 本模块不负责事件的业务语义，只负责“如何发出”；
- 旁路事件（已由其它组件写入 WAL）可以只做 stream + hooks（避免重复落盘）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from skills_runtime.core.contracts import AgentEvent
from skills_runtime.state.wal_protocol import WalBackend


EventStream = Callable[[AgentEvent], None]
EventHook = Callable[[AgentEvent], None]


@dataclass(frozen=True)
class WalEmitter:
    """
    WalEmitter（internal）。

    字段：
    - wal：WAL 后端（append-only 语义由具体实现保证）
    - stream：对外事件流回调（例如 run_stream 的 yield 管道）
    - hooks：可观测性 hooks（用于监控/metrics/转发等；必须不改变事件对象）
    """

    wal: WalBackend
    stream: EventStream
    hooks: Sequence[EventHook] = ()

    def _call_hooks(self, ev: AgentEvent) -> None:
        """
        依次调用 hooks（fail-open）。

        约束：
        - hooks 异常不得影响主流程（避免“监控把主链路打挂”）
        - hooks 不得修改事件对象（事件应被视为不可变）
        """

        for h in self.hooks or ():
            try:
                h(ev)
            except Exception:
                # fail-open：hook 失败只影响可观测性，不应中断 run
                continue

    def append(self, ev: AgentEvent) -> None:
        """
        仅追加到 WAL（不调用 hooks、不推送 stream）。

        用途：
        - tool 执行期的“旁路事件”：为了保持 approvals/event 序列可控，
          事件会先落 WAL，然后由上层在合适的时机 flush 到 stream 并触发 hooks。
        """

        self.wal.append(ev)

    def emit(self, ev: AgentEvent) -> None:
        """
        统一事件出口：WAL append → hooks → stream（保持顺序一致）。

        参数：
        - ev：AgentEvent
        """

        self.wal.append(ev)
        self._call_hooks(ev)
        self.stream(ev)

    def stream_only(self, ev: AgentEvent) -> None:
        """
        仅推送事件到调用方（含 hooks），不追加到 WAL。

        用途：
        - 某些事件已经由下游组件写入 WAL（例如 ToolExecutionContext.emit_event），此处只负责补齐 stream。
        """

        self._call_hooks(ev)
        self.stream(ev)

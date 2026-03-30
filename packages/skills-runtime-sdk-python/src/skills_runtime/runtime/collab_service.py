"""
协作 agent 管理服务（child agent 生命周期）。

职责：
- 管理 child agent 的创建、输入投递、关闭、恢复、等待
- 维护 _children（活跃）和 _terminal_children（已完成/失败/取消）两个索引
- 提供 CLI 默认 runner（最小可用，无 LLM 依赖）

约束：
- 线程安全（所有操作在 _children_lock 下）
- child 线程为 daemon（server 退出时自动终止）
"""

from __future__ import annotations

import contextlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class _ChildState:
    """runtime 内部 child agent 状态（线程内存态）。"""

    id: str
    agent_type: str
    message: str
    inbox: Queue[str]
    cancel_event: threading.Event
    thread: threading.Thread
    status: str = "running"
    final_output: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class _TerminalChildState:
    """关闭/完成后的 child 最小记录，仅供 wait 查询。"""

    id: str
    agent_type: str
    status: str
    final_output: Optional[str] = None
    error: Optional[str] = None


class CollabAgentService:
    """协作 agent 管理服务。"""

    def __init__(self, *, wait_join_poll_sec: float = 0.05) -> None:
        """
        创建协作 agent 服务。

        参数：
        - wait_join_poll_sec：collab.wait 的 join 轮询间隔（秒）
        """
        self._wait_join_poll_sec = max(0.01, float(wait_join_poll_sec))
        self._children_lock = threading.Lock()
        self._children: Dict[str, _ChildState] = {}
        self._terminal_children: Dict[str, _TerminalChildState] = {}

    @staticmethod
    def is_live_child_status(status: str) -> bool:
        """
        判断 child 状态是否属于"仍占用 runtime 活性"的 live 状态。

        约定：
        - `running`：执行中
        - `waiting_human`：等待人工输入，可恢复
        """
        return str(status) in {"running", "waiting_human"}

    def _terminalize_child_locked(
        self,
        child_id: str,
        *,
        status: str,
        final_output: Optional[str],
        error: Optional[str],
    ) -> Optional[_TerminalChildState]:
        """
        将 active child 转成 terminal 记录并移出 live 索引。

        说明：
        - terminal 记录只保留 wait 所需的最小字段；
        - 一旦 terminalize，后续 send_input/resume 不再命中 live handle。
        """
        child = self._children.pop(child_id, None)
        if child is None:
            return self._terminal_children.get(child_id)
        record = _TerminalChildState(
            id=child.id,
            agent_type=child.agent_type,
            status=str(status),
            final_output=final_output,
            error=error,
        )
        self._terminal_children[child_id] = record
        return record

    def _cli_default_runner(self, message: str, child: _ChildState) -> str:
        """
        CLI 默认 child runner（最小可用，无 LLM 依赖）。

        语义：
        - `wait_input:*`：等待一条输入后返回 `got:<input>`
        - 其它：返回 `echo:<message>`
        """
        if child.cancel_event.is_set():
            return "cancelled"
        msg = str(message)
        if msg.startswith("wait_input:"):
            # wait_input:* 是显式的 human wait 阶段：对外应可观测为 waiting_human。
            with self._children_lock:
                cur = self._children.get(child.id)
                if cur is not None and not cur.cancel_event.is_set():
                    cur.status = "waiting_human"
            while not child.cancel_event.is_set():
                try:
                    x = child.inbox.get(timeout=0.05)
                    return f"got:{x}"
                except Exception:
                    # 防御性兜底：Queue.get(timeout) 在取消/关闭时可能抛出非 queue.Empty 异常。
                    continue
            return "cancelled"
        return f"echo:{msg}"

    def _spawn_child(self, *, message: str, agent_type: str) -> _ChildState:
        """
        创建并启动一个 child（线程执行）。

        参数：
        - message：初始任务文本（非空）
        - agent_type：类型（最小实现仅记录）
        """
        if not str(message or "").strip():
            raise ValueError("message must be non-empty")
        cid = secrets.token_hex(16)
        inbox: Queue[str] = Queue()
        cancel_event = threading.Event()

        dummy = _ChildState(
            id=cid,
            agent_type=str(agent_type or "default"),
            message=str(message),
            inbox=inbox,
            cancel_event=cancel_event,
            thread=threading.Thread(),
        )

        def _run() -> None:
            """child 线程入口：执行 runner 并写回状态。"""
            try:
                out = self._cli_default_runner(message, dummy)
                with self._children_lock:
                    cur = self._children.get(cid)
                    if cur is None:
                        return
                    if cur.cancel_event.is_set():
                        self._terminalize_child_locked(cid, status="cancelled", final_output=None, error=None)
                        return
                    self._terminalize_child_locked(cid, status="completed", final_output=str(out), error=None)
            except Exception as e:
                # 防御性兜底：child runner 可能抛出任意异常；记录错误状态，不影响 server 主循环。
                with self._children_lock:
                    cur = self._children.get(cid)
                    if cur is None:
                        return
                    self._terminalize_child_locked(cid, status="failed", final_output=None, error=str(e))

        t = threading.Thread(target=_run, daemon=True)
        dummy.thread = t
        with self._children_lock:
            self._children[cid] = dummy
        t.start()
        return dummy

    def handle_collab_spawn(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPC：collab.spawn。"""
        child = self._spawn_child(message=str(params.get("message") or ""), agent_type=str(params.get("agent_type") or "default"))
        return {"id": child.id, "status": child.status}

    def handle_collab_send_input(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPC：collab.send_input。"""
        cid = str(params.get("id") or "")
        msg = str(params.get("message") or "")
        if not cid:
            raise ValueError("id must be non-empty")
        if not msg:
            raise ValueError("message must be non-empty")
        with self._children_lock:
            child = self._children.get(cid)
        if child is None:
            raise KeyError("child not found")
        child.inbox.put(msg)
        return {"id": cid}

    def handle_collab_close(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPC：collab.close。"""
        cid = str(params.get("id") or "")
        if not cid:
            raise ValueError("id must be non-empty")
        with self._children_lock:
            child = self._children.get(cid)
            if child is None:
                if cid in self._terminal_children:
                    return {"id": cid}
                raise KeyError("child not found")
            child.cancel_event.set()
            self._terminalize_child_locked(cid, status="cancelled", final_output=None, error=None)
        return {"id": cid}

    def handle_collab_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPC：collab.resume（最小：no-op 查询）。"""
        cid = str(params.get("id") or "")
        if not cid:
            raise ValueError("id must be non-empty")
        with self._children_lock:
            child = self._children.get(cid)
            if child is not None:
                return {"id": child.id, "status": child.status}
            terminal = self._terminal_children.get(cid)
        if terminal is None or terminal.status == "cancelled":
            raise KeyError("child not found")
        return {"id": terminal.id, "status": terminal.status}

    def handle_collab_wait(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPC：collab.wait。"""
        ids = params.get("ids")
        timeout_ms = params.get("timeout_ms")
        if not isinstance(ids, list) or not ids:
            raise ValueError("ids must be non-empty list")
        ids_s = [str(x) for x in ids]
        deadline = None
        if timeout_ms is not None:
            deadline = time.monotonic() + int(timeout_ms) / 1000.0

        # 先取快照，避免长 join 持锁
        with self._children_lock:
            missing = [i for i in ids_s if i not in self._children and i not in self._terminal_children]
            if missing:
                raise KeyError(f"unknown ids: {missing}")
            handles = [self._children[i] for i in ids_s if i in self._children]

        pending = list(handles)
        while pending:
            next_pending = []
            for h in pending:
                if not h.thread.is_alive():
                    continue
                wait_sec = self._wait_join_poll_sec
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        next_pending.append(h)
                        continue
                    wait_sec = min(wait_sec, remaining)
                h.thread.join(timeout=wait_sec)
                if h.thread.is_alive():
                    next_pending.append(h)
            if not next_pending:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            pending = next_pending

        results = []
        with self._children_lock:
            for cid in ids_s:
                cur = self._children.get(cid)
                terminal = self._terminal_children.get(cid)
                if cur is not None:
                    item: Dict[str, Any] = {"id": cur.id, "status": cur.status}
                    if cur.status == "completed" and cur.final_output is not None:
                        item["final_output"] = cur.final_output
                elif terminal is not None:
                    item = {"id": terminal.id, "status": terminal.status}
                    if terminal.status == "completed" and terminal.final_output is not None:
                        item["final_output"] = terminal.final_output
                else:
                    continue
                results.append(item)
        return {"results": results}

    def has_running_children(self) -> bool:
        """判断是否存在活跃 child agent。"""
        with self._children_lock:
            for c in self._children.values():
                if self.is_live_child_status(c.status):
                    return True
        return False

    def get_active_children_count(self) -> int:
        """返回活跃 child 数量（用于 runtime.status）。"""
        with self._children_lock:
            return sum(1 for c in self._children.values() if self.is_live_child_status(c.status))

    def cancel_all(self) -> int:
        """取消所有 child 并返回取消数量（用于 runtime.cleanup 和 server 退出）。"""
        cancelled = 0
        with self._children_lock:
            for cid, child in list(self._children.items()):
                if child.status == "running":
                    cancelled += 1
                child.cancel_event.set()
                child.status = "cancelled"
            self._children.clear()
            self._terminal_children.clear()
        return cancelled

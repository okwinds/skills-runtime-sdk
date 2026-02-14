"""
跨进程持久化的 Collaboration manager（runtime-backed）。

对齐 backlog：
- BL-005：多 agent 协作的“跨进程/可持久化”运行时（当前 tools CLI 为 in-process）

说明：
- 本模块提供与 `CollabManager` 相同的最小方法集（spawn/wait/send_input/close/resume）；
- 具体执行由 workspace 级本地 runtime 服务承担（Unix socket JSON RPC）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RemoteChildHandle:
    """跨进程 child agent 句柄快照（只包含工具所需字段）。"""

    id: str
    status: str
    final_output: Optional[str] = None
    error: Optional[str] = None


class PersistentCollabManager:
    """
    runtime-backed 的 collab manager（跨进程）。

    注意：
    - 本实现用于 tool-level primitives（spawn_agent/wait/send_input/close_agent/resume_agent）；
    - child 的执行逻辑由 runtime server 内置的 runner 决定（当前为 CLI 最小 runner）。
    """

    def __init__(self, *, workspace_root: Path) -> None:
        """
        创建一个 runtime-backed 的 collab manager。

        参数：
        - workspace_root：工作区根目录（用于发现/启动 workspace 级 runtime server）
        """

        from agent_sdk.runtime.client import RuntimeClient

        self._client = RuntimeClient(workspace_root=Path(workspace_root))

    def spawn(self, *, message: str, agent_type: str = "default") -> RemoteChildHandle:
        """
        生成子 agent 并开始执行。

        参数：
        - message：子任务文本（非空）
        - agent_type：子 agent 类型（最小实现仅记录）

        返回：
        - RemoteChildHandle：包含 child id 与初始状态
        """

        data = self._client.call(
            method="collab.spawn",
            params={"message": str(message), "agent_type": str(agent_type or "default")},
        )
        return RemoteChildHandle(id=str(data.get("id") or ""), status=str(data.get("status") or "running"))

    def wait(self, *, ids: List[str], timeout_ms: Optional[int] = None) -> List[RemoteChildHandle]:
        """
        等待一组子 agent 完成（或超时返回当前状态）。

        参数：
        - ids：child id 列表（必须全部存在）
        - timeout_ms：总超时（毫秒；可空）

        返回：
        - RemoteChildHandle 列表（按 server 返回顺序）
        """

        try:
            data = self._client.call(
                method="collab.wait",
                params={"ids": [str(i) for i in ids], "timeout_ms": (None if timeout_ms is None else int(timeout_ms))},
            )
        except RuntimeError as e:
            # server 侧会把 unknown ids 抛为 KeyError -> error string；工具层期望 KeyError 走 validation
            msg = str(e)
            if "unknown ids" in msg:
                raise KeyError(msg)
            raise
        results = data.get("results") if isinstance(data, dict) else None
        out: List[RemoteChildHandle] = []
        if isinstance(results, list):
            for it in results:
                if isinstance(it, dict):
                    out.append(
                        RemoteChildHandle(
                            id=str(it.get("id") or ""),
                            status=str(it.get("status") or "unknown"),
                            final_output=(None if it.get("final_output") is None else str(it.get("final_output"))),
                        )
                    )
        return out

    def send_input(self, *, agent_id: str, message: str) -> None:
        """
        向子 agent 投递输入（用于解除 ask_human 等等待）。

        参数：
        - agent_id：child id
        - message：输入消息（非空）
        """

        try:
            self._client.call(method="collab.send_input", params={"id": str(agent_id), "message": str(message)})
        except RuntimeError as e:
            if "child not found" in str(e):
                raise KeyError("agent not found")
            raise

    def close(self, *, agent_id: str) -> None:
        """
        取消/关闭子 agent（best-effort）。

        参数：
        - agent_id：child id
        """

        try:
            self._client.call(method="collab.close", params={"id": str(agent_id)})
        except RuntimeError as e:
            if "child not found" in str(e):
                raise KeyError("agent not found")
            raise

    def resume(self, *, agent_id: str) -> RemoteChildHandle:
        """
        恢复/查询子 agent 状态（最小语义：no-op，返回当前状态快照）。

        参数：
        - agent_id：child id
        """

        try:
            data = self._client.call(method="collab.resume", params={"id": str(agent_id)})
        except RuntimeError as e:
            if "child not found" in str(e):
                raise KeyError("agent not found")
            raise
        return RemoteChildHandle(id=str(data.get("id") or agent_id), status=str(data.get("status") or "unknown"))

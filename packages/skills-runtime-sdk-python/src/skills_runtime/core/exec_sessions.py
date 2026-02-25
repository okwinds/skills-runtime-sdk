"""
Exec sessions（PTY-backed）最小实现。

对齐规格：
- `docs/specs/skills-runtime-sdk/docs/tools-exec-sessions.md`

设计目标：
- 为 `exec_command` / `write_stdin` 提供“单 run 生命周期内”的交互式会话原语
- 默认不做持久化（session-only）；由调用方（Agent loop/CLI 代理层）决定生命周期

扩展（对齐 backlog：BL-004）：
- `PersistentExecSessionManager` 提供“跨进程持久化”能力：
  - 通过 workspace 级本地 runtime 服务维持 PTY 与子进程；
  - 允许不同进程的 `exec_command` / `write_stdin` 复用同一 session_id。
"""

from __future__ import annotations

import os
import pty
import select
import subprocess
import time
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@dataclass
class ExecSession:
    """进程会话（PTY 模式）。"""

    session_id: int
    proc: subprocess.Popen[bytes]
    master_fd: int
    created_at_ms: int


@dataclass
class ExecSessionWriteResult:
    """write_stdin 结果（结构化）。"""

    stdout: str
    stderr: str
    exit_code: Optional[int]
    running: bool
    truncated: bool


class ExecSessionManager:
    """
    Exec session 管理器（最小可用）。

    说明：
    - 本实现面向 macOS/Linux（不考虑 Windows）。
    - PTY 输出为 stdout/stderr 合流（stderr 为空），与大多数交互式 CLI 预期一致。
    """

    def __init__(self) -> None:
        """
        创建 exec session 管理器。

        说明：
        - session_id 在单进程内自增生成；
        - session 仅保存在内存中（不落盘）。
        """

        self._next_id = 1
        self._sessions: dict[int, ExecSession] = {}

    def spawn(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        tty: bool = True,
    ) -> ExecSession:
        """
        启动一个新的 exec session。

        参数：
        - argv：命令 argv
        - cwd：工作目录
        - env：环境变量（会覆盖父进程同名项）
        - tty：是否分配 TTY（当前实现始终使用 PTY；该字段仅为协议占位）

        返回：
        - ExecSession：包含 session_id/proc/master_fd
        """

        if not argv:
            raise ValueError("argv must not be empty")

        cwd_path = Path(cwd)
        if not cwd_path.exists() or not cwd_path.is_dir():
            raise ValueError("cwd must be an existing directory")

        master_fd, slave_fd = pty.openpty()
        merged_env = dict(os.environ)
        if env:
            merged_env.update({str(k): str(v) for k, v in env.items()})

        # 让子进程获得 slave 作为 stdio（交互式语义）；并成为新的进程组 leader（更可控的终止策略由上层实现）。
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=str(cwd_path),
            env=merged_env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)

        sid = self._next_id
        self._next_id += 1
        session = ExecSession(session_id=sid, proc=proc, master_fd=master_fd, created_at_ms=int(time.time() * 1000))
        self._sessions[sid] = session
        return session

    def has(self, session_id: int) -> bool:
        """判断 session 是否存在（仍由本 manager 持有）。"""

        return int(session_id) in self._sessions

    def write(
        self,
        *,
        session_id: int,
        chars: str = "",
        yield_time_ms: int = 50,
        max_output_bytes: int = 64 * 1024,
    ) -> ExecSessionWriteResult:
        """
        向 session 写入 chars，并读取可用输出。

        参数：
        - session_id：会话 id
        - chars：要写入的字符串（utf-8）；为空表示仅轮询输出
        - yield_time_ms：等待输出的时间（毫秒）
        - max_output_bytes：本次读取的最大字节数（尾部截断）
        """

        sid = int(session_id)
        if sid not in self._sessions:
            raise KeyError("session not found")

        if yield_time_ms < 0:
            raise ValueError("yield_time_ms must be >= 0")
        if max_output_bytes < 0:
            raise ValueError("max_output_bytes must be >= 0")

        session = self._sessions[sid]
        proc = session.proc
        master_fd = session.master_fd

        if chars:
            os.write(master_fd, chars.encode("utf-8", errors="replace"))

        deadline = time.monotonic() + (yield_time_ms / 1000.0)
        chunks: list[bytes] = []
        total = 0
        truncated = False

        # 轮询读取：直到超时或无数据可读
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            if timeout <= 0:
                break
            rlist, _, _ = select.select([master_fd], [], [], timeout)
            if not rlist:
                break
            try:
                b = os.read(master_fd, 4096)
            except OSError:
                break
            if not b:
                break

            if max_output_bytes == 0:
                truncated = True
                continue

            # 尾部截断：超出上限则丢弃头部
            chunks.append(b)
            total += len(b)
            while total > max_output_bytes and chunks:
                drop = min(total - max_output_bytes, len(chunks[0]))
                if drop >= len(chunks[0]):
                    total -= len(chunks[0])
                    chunks.pop(0)
                else:
                    chunks[0] = chunks[0][drop:]
                    total -= drop
                truncated = True

        out_b = b"".join(chunks)
        out_text = out_b.decode("utf-8", errors="replace")

        exit_code: Optional[int] = None
        running = proc.poll() is None
        if not running:
            exit_code = proc.returncode
            self._cleanup_session(sid)

        return ExecSessionWriteResult(
            stdout=out_text,
            stderr="",
            exit_code=exit_code,
            running=running,
            truncated=truncated,
        )

    def close(self, session_id: int) -> None:
        """关闭 session（best-effort：terminate 进程并清理资源）。"""

        sid = int(session_id)
        if sid not in self._sessions:
            return
        session = self._sessions[sid]
        # 进程是新的 session leader（start_new_session=True），优先按进程组终止，避免子孙进程残留。
        pid = int(getattr(session.proc, "pid", 0) or 0)
        try:
            if pid > 0:
                os.killpg(pid, signal.SIGTERM)
            else:
                session.proc.terminate()
        except Exception:
            pass
        self._cleanup_session(sid)

    def close_all(self) -> None:
        """关闭所有 session（用于 run 结束清理）。"""

        for sid in list(self._sessions.keys()):
            self.close(sid)

    def _cleanup_session(self, sid: int) -> None:
        """
        清理 session 资源（关闭 master fd 并从内存移除）。

        参数：
        - sid：session id
        """

        session = self._sessions.pop(sid, None)
        if session is None:
            return
        try:
            os.close(session.master_fd)
        except Exception:
            pass


@runtime_checkable
class ExecSessionsProvider(Protocol):
    """
    Exec sessions 抽象接口（用于同时支持 in-process 与 runtime 持久化实现）。

    注意：
    - 只定义 builtin tools 所需的最小方法集（spawn/write/has/close/close_all）。
    """

    def spawn(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        tty: bool = True,
    ) -> Any:
        """
        启动一个新的 exec session（协议）。

        参数：
        - argv/cwd/env/tty：语义同 `ExecSessionManager.spawn`
        """

        ...

    def has(self, session_id: int) -> bool:
        """判断 session 是否存在（协议；最小实现允许近似判断）。"""

        ...

    def write(
        self,
        *,
        session_id: int,
        chars: str = "",
        yield_time_ms: int = 50,
        max_output_bytes: int = 64 * 1024,
    ) -> ExecSessionWriteResult:
        """
        向 session 写入并读取输出（协议）。

        参数：
        - session_id/chars/yield_time_ms/max_output_bytes：语义同 `ExecSessionManager.write`
        """

        ...

    def close(self, session_id: int) -> None:
        """关闭 session（协议；best-effort）。"""

        ...

    def close_all(self) -> None:
        """关闭所有 session（协议；best-effort）。"""

        ...


@dataclass(frozen=True)
class ExecSessionRef:
    """跨进程 session 引用（仅保留可复用字段）。"""

    session_id: int
    created_at_ms: int


class PersistentExecSessionManager:
    """
    基于本地 runtime 服务的 exec sessions manager（跨进程持久化）。

    说明：
    - 该 manager 只实现“跨进程复用 session_id”的最小能力；
    - 具体的 sandbox/approval 仍由 builtin tool 层决定（本 manager 只执行已准备好的 argv/cwd/env）。
    """

    def __init__(self, *, workspace_root: Path) -> None:
        """
        创建一个 runtime-backed 的 exec sessions manager。

        参数：
        - workspace_root：工作区根目录（用于发现/启动 workspace 级 runtime server）
        """

        from skills_runtime.runtime.client import RuntimeClient

        self._client = RuntimeClient(workspace_root=Path(workspace_root))

    def spawn(
        self,
        *,
        argv: list[str],
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        tty: bool = True,
    ) -> ExecSessionRef:
        """
        启动一个新的跨进程 session（PTY）。

        参数：
        - argv：命令 argv
        - cwd：工作目录
        - env：环境变量（会覆盖 server 进程同名项）
        - tty：是否分配 TTY（占位；当前 server 总是 PTY）
        """

        params: dict[str, Any] = {"argv": list(argv), "cwd": str(Path(cwd).resolve()), "tty": bool(tty)}
        if env is not None:
            params["env"] = {str(k): str(v) for k, v in dict(env).items()}
        data = self._client.call(method="exec.spawn", params=params)
        return ExecSessionRef(session_id=int(data["session_id"]), created_at_ms=int(data.get("created_at_ms") or 0))

    def has(self, session_id: int) -> bool:
        """
        判断 session 是否存在（最小实现：尝试一次 0ms 的 write 轮询）。

        注意：
        - 该方法为 best-effort，主要用于清理/健康检查；
        - 对于正常工作流，推荐直接调用 `write()` 并处理 KeyError。
        """

        # Phase 1：不暴露 list/has；写入时由 server 抛 KeyError 即可。
        try:
            _ = self._client.call(method="exec.write", params={"session_id": int(session_id), "chars": "", "yield_time_ms": 0})
            return True
        except Exception:
            return False

    def write(
        self,
        *,
        session_id: int,
        chars: str = "",
        yield_time_ms: int = 50,
        max_output_bytes: int = 64 * 1024,
    ) -> ExecSessionWriteResult:
        """
        向 session 写入并读取输出（跨进程）。

        异常：
        - KeyError：当 session 不存在（not_found）
        - ValueError/RuntimeError：参数或运行时错误
        """

        try:
            data = self._client.call(
                method="exec.write",
                params={
                    "session_id": int(session_id),
                    "chars": str(chars or ""),
                    "yield_time_ms": int(yield_time_ms),
                    "max_output_bytes": int(max_output_bytes),
                },
            )
        except RuntimeError as e:
            msg = str(e)
            if "session not found" in msg:
                raise KeyError("session not found")
            raise
        return ExecSessionWriteResult(
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            exit_code=(None if data.get("exit_code") is None else int(data.get("exit_code"))),
            running=bool(data.get("running")),
            truncated=bool(data.get("truncated")),
        )

    def close(self, session_id: int) -> None:
        """
        关闭 session（最小实现：best-effort）。

        说明：
        - runtime server 提供 `exec.close` RPC；这里做 best-effort 调用；
        - 若 session 不存在：保持 no-op（与 in-process `ExecSessionManager.close` 一致）。
        """

        try:
            self._client.call(method="exec.close", params={"session_id": int(session_id)})
        except Exception:
            return

    def close_all(self) -> None:
        """
        关闭所有 session（best-effort）。

        说明：
        - 主要用于“run 结束清理”或测试清理；
        - runtime server 仍会提供 idle auto-exit 作为兜底，但 close_all 能更快回收资源。
        """

        try:
            self._client.call(method="exec.close_all")
        except Exception:
            return

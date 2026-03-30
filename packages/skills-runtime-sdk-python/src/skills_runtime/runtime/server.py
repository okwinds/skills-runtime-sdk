from __future__ import annotations

import json
import logging
import contextlib
import os
import signal
import secrets
import socket
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any, Dict, Optional

from skills_runtime.core.exec_sessions import ExecSessionManager, ExecSessionWriteResult
from skills_runtime.runtime.paths import get_runtime_paths


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


class RuntimeServer:
    """
    workspace 级 runtime server（Unix socket JSON RPC）。

    行为：
    - server 启动后写入 `.skills_runtime_sdk/runtime/server.json`（pid/secret/socket_path）；
    - 当无 running sessions/children 且 idle 超过阈值时自动退出（避免测试/脚本泄露后台进程）。
    """

    def __init__(
        self,
        *,
        workspace_root: Path,
        secret: str,
        idle_timeout_ms: int = 10_000,
        max_request_bytes: int = 1 * 1024 * 1024,
        request_read_timeout_sec: float = 1.0,
        wait_join_poll_ms: int = 50,
    ) -> None:
        """
        创建 workspace 级 runtime server。

        参数：
        - workspace_root：工作区根目录（用于写入 server.json 与日志）
        - secret：本地鉴权 secret（客户端需携带；仅本机使用）
        - idle_timeout_ms：无运行资源时的空闲退出阈值（毫秒）
        - max_request_bytes：单次 RPC 请求体最大字节数（用于防止内存 DoS；超限返回 validation 错误）
        """

        self._workspace_root = Path(workspace_root).resolve()
        self._secret = str(secret or "")
        self._idle_timeout_ms = int(idle_timeout_ms)
        self._max_request_bytes = max(1, int(max_request_bytes))
        self._request_read_timeout_sec = max(0.1, float(request_read_timeout_sec))
        self._wait_join_poll_sec = max(0.01, int(wait_join_poll_ms) / 1000.0)
        self._paths = get_runtime_paths(workspace_root=self._workspace_root)

        self._created_at_ms = int(time.time() * 1000)
        self._started_monotonic = time.monotonic()
        # 用于 orphan cleanup 的 “进程身份标记”。每次 server 启动都会生成新的 marker。
        # 该 marker 会注入到 exec session 子进程 env，并落盘到 registry；restart 后可用于验证是否“本框架产物”。
        self._exec_marker = secrets.token_hex(8)

        self._exec = ExecSessionManager()
        self._exec_lock = threading.Lock()
        self._children_lock = threading.Lock()
        self._children: Dict[str, _ChildState] = {}
        self._terminal_children: Dict[str, _TerminalChildState] = {}

        self._shutdown = threading.Event()
        self._last_activity = time.monotonic()
        self._last_orphan_cleanup: Dict[str, Any] = {"ok": True, "killed": 0, "skipped": 0, "errors": []}

    @staticmethod
    def _is_live_child_status(status: str) -> bool:
        """
        判断 child 状态是否属于“仍占用 runtime 活性”的 live 状态。

        约定：
        - `running`：执行中
        - `waiting_human`：等待人工输入，可恢复
        """

        return str(status) in {"running", "waiting_human"}

    def _write_server_info(self) -> None:
        """写入 `server.json`（pid/secret/socket_path/created_at_ms）。"""

        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        obj = {
            "pid": os.getpid(),
            "secret": self._secret,
            "socket_path": str(self._paths.socket_path),
            "created_at_ms": int(self._created_at_ms),
        }
        tmp = self._paths.server_info_path.with_name(f"{self._paths.server_info_path.name}.tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        # best-effort：server.json 含本地 secret，尽力收紧权限（POSIX 0600）。
        with contextlib.suppress(OSError, PermissionError):
            tmp.chmod(stat.S_IRUSR | stat.S_IWUSR)
        tmp.replace(self._paths.server_info_path)
        with contextlib.suppress(OSError, PermissionError):
            self._paths.server_info_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

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

    def _read_exec_registry(self) -> Dict[str, Any]:
        """
        读取 exec registry（用于 orphan cleanup 与 status 可观测）。

        返回：
        - dict：至少包含 `exec_sessions`（mapping）
        """

        p = self._paths.exec_registry_path
        if not p.exists():
            return {"schema": 1, "workspace_root": str(self._workspace_root), "exec_sessions": {}}
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema": 1, "workspace_root": str(self._workspace_root), "exec_sessions": {}}
        if not isinstance(obj, dict):
            return {"schema": 1, "workspace_root": str(self._workspace_root), "exec_sessions": {}}
        if not isinstance(obj.get("exec_sessions"), dict):
            obj["exec_sessions"] = {}
        if not isinstance(obj.get("workspace_root"), str):
            obj["workspace_root"] = str(self._workspace_root)
        if not isinstance(obj.get("schema"), int):
            obj["schema"] = 1
        return obj

    def _write_exec_registry(self, obj: Dict[str, Any]) -> None:
        """
        原子写入 exec registry（best-effort）。

        参数：
        - obj：registry dict
        """

        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        p = self._paths.exec_registry_path
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def _register_exec_session(self, *, session_id: int, pid: int, created_at_ms: int, argv: list[str], cwd: str) -> None:
        """
        将 exec session 记录到 registry（用于 crash/restart 后 orphan cleanup）。

        参数：
        - session_id：server 内部 session id（仅用于关联）
        - pid：子进程 pid（同时也是 pgid；ExecSessionManager.spawn 使用 start_new_session=True）
        - created_at_ms：创建时间（ms）
        - argv：原始 argv（便于审计/排障）
        - cwd：工作目录（绝对路径字符串）
        """

        reg = self._read_exec_registry()
        sessions = reg.get("exec_sessions")
        if not isinstance(sessions, dict):
            sessions = {}
            reg["exec_sessions"] = sessions
        sessions[str(int(session_id))] = {
            "pid": int(pid),
            "pgid": int(pid),
            "created_at_ms": int(created_at_ms),
            "argv": [str(x) for x in list(argv)],
            "cwd": str(cwd),
            "marker": str(self._exec_marker),
        }
        reg["updated_at_ms"] = int(time.time() * 1000)
        self._write_exec_registry(reg)

    def _unregister_exec_session(self, session_id: int) -> None:
        """从 registry 移除一个 session（best-effort）。"""

        reg = self._read_exec_registry()
        sessions = reg.get("exec_sessions")
        if not isinstance(sessions, dict):
            return
        if str(int(session_id)) in sessions:
            sessions.pop(str(int(session_id)), None)
            reg["updated_at_ms"] = int(time.time() * 1000)
            self._write_exec_registry(reg)

    def _pid_alive(self, pid: int) -> bool:
        """判断 pid 是否存活（best-effort）。"""

        try:
            os.kill(int(pid), 0)
            return True
        except OSError:
            return False

    def _ps_env_contains_marker(self, pid: int, marker: str) -> bool:
        """
        通过 `ps eww -p <pid>` 判断环境变量中是否包含 marker（best-effort）。

        说明：
        - 用于降低“pid 复用误杀”的风险；
        - 若命令不可用或权限不足，返回 False（由上层决定是否 fallback）。
        """

        try:
            import subprocess

            cp = subprocess.run(  # noqa: S603
                ["ps", "eww", "-p", str(int(pid))],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            out = (cp.stdout or "") + "\n" + (cp.stderr or "")
            return str(marker) in out
        except OSError:
            return False

    def _kill_process_group(self, pid: int) -> bool:
        """
        终止进程组（best-effort）。

        参数：
        - pid：进程 pid（也是 pgid）

        返回：
        - bool：是否发出了信号（不代表一定成功）
        """

        try:
            os.killpg(int(pid), signal.SIGTERM)
        except OSError:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                return False

        deadline = time.monotonic() + 0.6
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.05)

        # 最后兜底：SIGKILL
        try:
            os.killpg(int(pid), signal.SIGKILL)
        except OSError:
            with contextlib.suppress(OSError):
                os.kill(int(pid), signal.SIGKILL)
        return True

    def _resolve_under_workspace(self, path: str) -> Path:
        """
        将 runtime RPC 传入的路径解析到 workspace_root 下。

        说明：
        - runtime server 是 workspace 级能力边界；
        - 因此即使来自本机同用户的 RPC，也不得允许 `cwd` 逃逸出 `workspace_root`。
        """

        candidate = Path(str(path))
        if not candidate.is_absolute():
            candidate = (self._workspace_root / candidate).resolve()
        else:
            candidate = candidate.resolve()

        if not candidate.is_relative_to(self._workspace_root):
            raise PermissionError(f"cwd escapes workspace_root: {candidate}")
        return candidate

    def _orphan_cleanup_on_startup(self) -> None:
        """
        启动期 orphan cleanup（crash/restart 兜底）。

        语义：
        - 读取 registry 中记录的 pids；
        - 仅在 marker（或未来更强身份校验）确认后再 kill；
        - cleanup 后清空 registry（避免无限重试与误判）。
        """

        reg = self._read_exec_registry()
        sessions = reg.get("exec_sessions") or {}
        if not isinstance(sessions, dict) or not sessions:
            self._last_orphan_cleanup = {"ok": True, "killed": 0, "skipped": 0, "errors": []}
            return

        killed = 0
        skipped = 0
        errors: list[str] = []
        remaining: Dict[str, Any] = {}

        for sid, item in list(sessions.items()):
            if not isinstance(item, dict):
                skipped += 1
                continue
            pid = int(item.get("pid") or 0)
            marker = str(item.get("marker") or "").strip()
            argv = item.get("argv") or []
            argv0 = str(argv[0]) if isinstance(argv, list) and argv else ""
            if pid <= 0:
                skipped += 1
                continue
            if not self._pid_alive(pid):
                killed += 1  # 视为已无残留（无需保留条目）
                continue

            verified = False
            if marker:
                verified = self._ps_env_contains_marker(pid, marker)

            if not verified:
                skipped += 1
                # 进程存活但无法验证身份：记录详细日志便于人工排查
                logger.warning(
                    "orphan_cleanup: cannot verify pid=%d (marker=%s, argv0=%s), "
                    "marking for manual cleanup",
                    pid,
                    marker[:8] + "..." if marker else "<none>",
                    argv0[:32] if argv0 else "<none>",
                )
                remaining[str(sid)] = dict(item, needs_manual_cleanup=True, last_seen_alive_ms=int(time.time() * 1000))
                continue

            try:
                if self._kill_process_group(pid):
                    killed += 1
                else:
                    errors.append(f"failed_to_kill pid={pid}")
                    remaining[str(sid)] = dict(item, last_kill_error="failed_to_kill", last_seen_alive_ms=int(time.time() * 1000))
            except (OSError, RuntimeError) as e:
                errors.append(f"kill_error pid={pid} err={e}")
                remaining[str(sid)] = dict(item, last_kill_error=str(e), last_seen_alive_ms=int(time.time() * 1000))

        # 更新 registry：移除已确认不存活或已终止的条目；保留无法验证/终止失败的条目，供人工排障。
        reg["exec_sessions"] = remaining
        reg["updated_at_ms"] = int(time.time() * 1000)
        with contextlib.suppress(Exception):
            self._write_exec_registry(reg)

        self._last_orphan_cleanup = {"ok": not errors, "killed": int(killed), "skipped": int(skipped), "errors": list(errors)}

    def _cleanup_files(self) -> None:
        """清理 socket 与 server.json（best-effort）。"""

        with contextlib.suppress(Exception):
            if self._paths.socket_path.exists():
                self._paths.socket_path.unlink()
        with contextlib.suppress(Exception):
            if self._paths.server_info_path.exists():
                self._paths.server_info_path.unlink()

    def _has_running_resources(self) -> bool:
        """
        判断当前是否存在“需要保持 server 存活”的运行资源。

        规则：
        - 任一 exec session 存在（in-memory 持有）视为 running；
        - 任一 child 状态为 running 视为 running。
        """

        with self._exec_lock:
            if any(self._exec.has(sid) for sid in list(getattr(self._exec, "_sessions", {}).keys())):
                return True
        with self._children_lock:
            for c in self._children.values():
                if self._is_live_child_status(c.status):
                    return True
        return False

    def _handle_exec_spawn(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：exec.spawn。

        参数（params）：
        - argv/cwd/env/tty：语义对齐 ExecSessionManager.spawn
        """

        argv = params.get("argv")
        cwd = params.get("cwd")
        env = params.get("env")
        tty = bool(params.get("tty", True))
        if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
            raise ValueError("argv must be list[str]")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("cwd must be string")
        cwd_path = self._resolve_under_workspace(cwd)
        env_map: Optional[Dict[str, str]] = None
        if env is not None:
            if not isinstance(env, dict):
                raise ValueError("env must be dict")
            env_map = {str(k): str(v) for k, v in env.items()}

        # 注入 marker，便于 crash/restart 后 orphan cleanup 精准识别
        env2 = dict(env_map or {})
        env2["SKILLS_RUNTIME_SDK_RUNTIME_EXEC_SESSION_MARKER"] = str(self._exec_marker)
        env2["SKILLS_RUNTIME_SDK_RUNTIME_WORKSPACE_ROOT"] = str(self._workspace_root)

        with self._exec_lock:
            s = self._exec.spawn(argv=[str(x) for x in argv], cwd=cwd_path, env=env2, tty=tty)
            self._register_exec_session(
                session_id=int(s.session_id),
                pid=int(getattr(s.proc, "pid", 0) or 0),
                created_at_ms=int(s.created_at_ms),
                argv=[str(x) for x in argv],
                cwd=str(cwd_path),
            )
            return {
                "session_id": int(s.session_id),
                "created_at_ms": int(s.created_at_ms),
                "pid": int(getattr(s.proc, "pid", 0) or 0),
            }

    def _handle_exec_write(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：exec.write。

        参数（params）：
        - session_id/chars/yield_time_ms/max_output_bytes：语义对齐 ExecSessionManager.write
        """

        session_id = int(params.get("session_id"))
        chars = str(params.get("chars") or "")
        yield_time_ms = int(params.get("yield_time_ms", 50))
        max_output_bytes = int(params.get("max_output_bytes", 64 * 1024))
        with self._exec_lock:
            wr: ExecSessionWriteResult = self._exec.write(
                session_id=session_id,
                chars=chars,
                yield_time_ms=yield_time_ms,
                max_output_bytes=max_output_bytes,
            )
            if not wr.running:
                # session 已退出：从 registry 移除，避免 restart 后误认为 orphan
                with contextlib.suppress(Exception):
                    self._unregister_exec_session(session_id)
        return {
            "stdout": wr.stdout,
            "stderr": wr.stderr,
            "exit_code": wr.exit_code,
            "running": wr.running,
            "truncated": wr.truncated,
        }

    def _handle_exec_close(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：exec.close。

        参数（params）：
        - session_id：要关闭的 session id

        语义：
        - best-effort：若 session 不存在则 no-op；
        - 若存在则尝试 terminate 进程并回收资源。
        """

        session_id = int(params.get("session_id"))
        with self._exec_lock:
            existed = bool(self._exec.has(session_id))
            self._exec.close(session_id)
            self._unregister_exec_session(session_id)
        return {"ok": True, "session_id": int(session_id), "found": bool(existed)}

    def _handle_exec_close_all(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：exec.close_all。

        语义：
        - best-effort 关闭当前 server 持有的所有 sessions。
        """

        _ = params
        with self._exec_lock:
            self._exec.close_all()
            with contextlib.suppress(Exception):
                reg = self._read_exec_registry()
                reg["exec_sessions"] = {}
                reg["updated_at_ms"] = int(time.time() * 1000)
                self._write_exec_registry(reg)
        return {"ok": True}

    def _handle_runtime_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：runtime.status。

        返回：
        - server pid/created_at_ms/uptime_ms
        - active_exec_sessions / active_children
        - exec_registry 摘要（便于审计/排障）
        """

        _ = params
        with self._children_lock:
            children = list(self._children.values())
        active_children = sum(1 for c in children if self._is_live_child_status(c.status))

        # ExecSessionManager 当前未公开 list API，先走 best-effort 私有字段快照（单线程 server 内使用可接受）。
        with self._exec_lock:
            sessions = list(getattr(self._exec, "_sessions", {}).keys())
            active_exec = sum(1 for sid in sessions if self._exec.has(sid))

        reg = self._read_exec_registry()
        reg_sessions = reg.get("exec_sessions") or {}
        reg_count = len(reg_sessions) if isinstance(reg_sessions, dict) else 0

        return {
            "ok": True,
            "pid": int(os.getpid()),
            "created_at_ms": int(self._created_at_ms),
            "uptime_ms": int((time.monotonic() - self._started_monotonic) * 1000),
            "active_exec_sessions": int(active_exec),
            "active_children": int(active_children),
            "exec_registry": {
                "path": str(self._paths.exec_registry_path),
                "count": int(reg_count),
                "last_orphan_cleanup": dict(self._last_orphan_cleanup),
            },
        }

    def _handle_runtime_cleanup(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：runtime.cleanup（显式 stop/cleanup 汇总入口）。

        参数：
        - exec：是否关闭 exec sessions（默认 true）
        - children：是否取消 child agents（默认 true）
        """

        close_exec = bool(params.get("exec", True))
        close_children = bool(params.get("children", True))

        if close_exec:
            with self._exec_lock:
                self._exec.close_all()
                with contextlib.suppress(Exception):
                    reg = self._read_exec_registry()
                    reg["exec_sessions"] = {}
                    reg["updated_at_ms"] = int(time.time() * 1000)
                    self._write_exec_registry(reg)

        cancelled_children = 0
        if close_children:
            with self._children_lock:
                for cid, child in list(self._children.items()):
                    if child.status == "running":
                        cancelled_children += 1
                    child.cancel_event.set()
                    child.status = "cancelled"
                # cleanup 的目标是“快速回收”，不保证保留历史；直接清空，避免无限增长。
                self._children.clear()
                self._terminal_children.clear()

        return {"ok": True, "exec": bool(close_exec), "children": bool(close_children), "cancelled_children": int(cancelled_children)}

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

    def _handle_collab_spawn(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPC：collab.spawn。"""

        child = self._spawn_child(message=str(params.get("message") or ""), agent_type=str(params.get("agent_type") or "default"))
        return {"id": child.id, "status": child.status}

    def _handle_collab_send_input(self, params: Dict[str, Any]) -> Dict[str, Any]:
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

    def _handle_collab_close(self, params: Dict[str, Any]) -> Dict[str, Any]:
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

    def _handle_collab_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
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

    def _handle_collab_wait(self, params: Dict[str, Any]) -> Dict[str, Any]:
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

    def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        """
        路由 RPC 方法到对应 handler。

        注意：
        - 不对外暴露网络；仅用于本机同用户访问；
        - 未知方法抛 ValueError（客户端收到 ok=false）。
        """

        if method == "ping":
            return {"pong": True}
        if method == "shutdown":
            self._shutdown.set()
            return {"ok": True}

        if method == "runtime.status":
            return self._handle_runtime_status(params)
        if method == "runtime.cleanup":
            return self._handle_runtime_cleanup(params)

        if method == "exec.spawn":
            return self._handle_exec_spawn(params)
        if method == "exec.write":
            return self._handle_exec_write(params)
        if method == "exec.close":
            return self._handle_exec_close(params)
        if method == "exec.close_all":
            return self._handle_exec_close_all(params)

        if method == "collab.spawn":
            return self._handle_collab_spawn(params)
        if method == "collab.wait":
            return self._handle_collab_wait(params)
        if method == "collab.send_input":
            return self._handle_collab_send_input(params)
        if method == "collab.close":
            return self._handle_collab_close(params)
        if method == "collab.resume":
            return self._handle_collab_resume(params)

        raise ValueError(f"unknown method: {method}")

    def _format_rpc_error(self, e: Exception) -> Dict[str, Any]:
        """
        将异常映射为稳定的 RPC 错误结构。

        返回：
        - error_kind：validation|permission|not_found|internal
        - error：稳定可读错误信息
        """

        kind = "internal"
        if isinstance(e, ValueError):
            kind = "validation"
        elif isinstance(e, PermissionError):
            kind = "permission"
        elif isinstance(e, KeyError):
            kind = "not_found"

        msg = str(e)
        # KeyError 默认会带引号："'session not found'"，这里做一个稳定化处理，便于上层做字符串匹配。
        if isinstance(e, KeyError):
            msg = msg.strip()
            if msg.startswith("'") and msg.endswith("'") and len(msg) >= 2:
                msg = msg[1:-1]
        if not msg:
            msg = kind
        return {"error_kind": kind, "error": msg}

    def _read_request(self, conn: socket.socket) -> Optional[Dict[str, Any]]:
        """
        读取并解析一个连接上的 RPC 请求。

        返回：
        - dict：已完整收到并成功解析的请求对象
        - None：连接在活性窗口内未形成完整请求，server 直接放弃该连接
        """

        conn.settimeout(self._request_read_timeout_sec)
        raw = bytearray()
        while True:
            try:
                b = conn.recv(65536)
            except socket.timeout:
                return None
            if not b:
                break
            if len(raw) + len(b) > self._max_request_bytes:
                raise ValueError("request too large")
            raw.extend(b)

        if not raw:
            raise ValueError("invalid request")

        req = json.loads(raw.decode("utf-8", errors="replace"))
        if not isinstance(req, dict):
            raise ValueError("invalid request")
        return req

    def _serve_connection(self, conn: socket.socket) -> None:
        """
        处理单个已接受连接。

        说明：
        - 读请求、dispatch、回包都在独立 worker 中完成；
        - 若请求在活性窗口内未完整形成，则直接关闭连接，不强制返回 JSON。
        """

        with conn:
            try:
                req = self._read_request(conn)
                if req is None:
                    return
                if str(req.get("secret") or "") != self._secret:
                    raise PermissionError("invalid secret")
                method = str(req.get("method") or "")
                params = req.get("params") or {}
                if not isinstance(params, dict):
                    raise ValueError("params must be object")
                self._last_activity = time.monotonic()
                data = self._dispatch(method, params)
                resp = {"ok": True, "data": data}
            except Exception as e:
                # 防御性兜底：单连接 worker 不得因请求异常拖垮整个 server。
                resp = {"ok": False, **self._format_rpc_error(e)}

            with contextlib.suppress(Exception):
                conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8"))

    def serve_forever(self) -> None:
        """
        监听 Unix socket 并处理请求，直到 shutdown 或 idle auto-exit。
        """

        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        # crash/restart 兜底：启动期先做 orphan cleanup，再进入 accept loop
        with contextlib.suppress(Exception):
            self._orphan_cleanup_on_startup()
        # 清理旧 socket（可能来自异常退出）
        with contextlib.suppress(Exception):
            if self._paths.socket_path.exists():
                self._paths.socket_path.unlink()

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(str(self._paths.socket_path))
            os.chmod(self._paths.socket_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            s.listen(64)
            s.settimeout(0.2)
            self._write_server_info()

            while not self._shutdown.is_set():
                # idle shutdown（无资源 + 长时间无请求）
                if not self._has_running_resources():
                    idle_ms = int((time.monotonic() - self._last_activity) * 1000)
                    if idle_ms > self._idle_timeout_ms:
                        break

                try:
                    conn, _ = s.accept()
                except socket.timeout:
                    continue
                except Exception:
                    # 防御性兜底：socket accept 可能因信号/系统调用中断等抛出任意异常；
                    # 继续循环避免 server 崩溃。
                    continue
                self._last_activity = time.monotonic()
                try:
                    t = threading.Thread(target=self._serve_connection, args=(conn,), daemon=True)
                    t.start()
                except Exception:
                    with contextlib.suppress(Exception):
                        conn.close()
        finally:
            # 进程正常退出时尽量回收资源，避免遗留 orphan。
            with contextlib.suppress(Exception):
                with self._exec_lock:
                    self._exec.close_all()
            with contextlib.suppress(Exception):
                with self._children_lock:
                    for c in self._children.values():
                        c.cancel_event.set()
                    self._children.clear()
                    self._terminal_children.clear()
            with contextlib.suppress(Exception):
                s.close()
            self._cleanup_files()


def main() -> int:
    """
    模块入口：从环境变量读取 workspace_root/secret 并启动 server。

    环境变量：
    - `SKILLS_RUNTIME_SDK_RUNTIME_WORKSPACE_ROOT`
    - `SKILLS_RUNTIME_SDK_RUNTIME_SECRET`
    """

    secret = str(os.environ.get("SKILLS_RUNTIME_SDK_RUNTIME_SECRET") or "").strip()
    ws = str(os.environ.get("SKILLS_RUNTIME_SDK_RUNTIME_WORKSPACE_ROOT") or "").strip()
    if not secret:
        secret = secrets.token_urlsafe(24)
    if not ws:
        ws = str(Path.cwd().resolve())

    server = RuntimeServer(workspace_root=Path(ws), secret=secret)
    server.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

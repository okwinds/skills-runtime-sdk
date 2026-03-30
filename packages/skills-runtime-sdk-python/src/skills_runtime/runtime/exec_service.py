"""
Exec session 管理服务（跨进程持久化）。

职责：
- 封装 ExecSessionManager（PTY 会话管理）
- 维护 exec_registry.json（用于 crash/restart 后识别 orphan）
- 提供 exec.spawn / exec.write / exec.close / exec.close_all 的业务逻辑

约束：
- 线程安全（所有操作在 _exec_lock 下）
- registry 写入为 best-effort（不阻断主流程）
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from skills_runtime.core.exec_sessions import ExecSessionManager, ExecSessionWriteResult
from skills_runtime.runtime.paths import RuntimePaths


class ExecSessionService:
    """Exec session 管理服务。"""

    def __init__(
        self,
        *,
        workspace_root: Path,
        paths: RuntimePaths,
        exec_marker: str,
    ) -> None:
        """
        创建 exec session 服务。

        参数：
        - workspace_root：工作区根目录
        - paths：RuntimePaths（用于 exec_registry_path）
        - exec_marker：当前 server 实例的身份标记（注入到子进程 env）
        """
        self._workspace_root = Path(workspace_root).resolve()
        self._paths = paths
        self._exec_marker = str(exec_marker)
        self._exec = ExecSessionManager()
        self._exec_lock = threading.Lock()

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

    def handle_exec_spawn(
        self, params: Dict[str, Any], *, resolve_path: Callable[[str], Path]
    ) -> Dict[str, Any]:
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
        cwd_path = resolve_path(cwd)
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

    def handle_exec_write(self, params: Dict[str, Any]) -> Dict[str, Any]:
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

    def handle_exec_close(self, params: Dict[str, Any]) -> Dict[str, Any]:
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

    def handle_exec_close_all(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：exec.close_all。

        语义：
        - best-effort 关闭当前 server 持有的所有 sessions。
        """
        _ = params
        self.close_all_and_clear_registry()
        return {"ok": True}

    def has_running_sessions(self) -> bool:
        """判断是否存在活跃 exec session。"""
        with self._exec_lock:
            return any(
                self._exec.has(sid)
                for sid in list(getattr(self._exec, "_sessions", {}).keys())
            )

    def close_all_and_clear_registry(self) -> None:
        """关闭所有 session 并清空 registry（用于 runtime.cleanup 和 server 退出）。"""
        with self._exec_lock:
            self._exec.close_all()
            with contextlib.suppress(Exception):
                reg = self._read_exec_registry()
                reg["exec_sessions"] = {}
                reg["updated_at_ms"] = int(time.time() * 1000)
                self._write_exec_registry(reg)

    def get_status_snapshot(self) -> Dict[str, Any]:
        """返回 exec service 的状态快照（用于 runtime.status）。"""
        with self._exec_lock:
            sessions = list(getattr(self._exec, "_sessions", {}).keys())
            active_exec = sum(1 for sid in sessions if self._exec.has(sid))
        reg = self._read_exec_registry()
        reg_sessions = reg.get("exec_sessions") or {}
        reg_count = len(reg_sessions) if isinstance(reg_sessions, dict) else 0
        return {
            "active_exec_sessions": int(active_exec),
            "exec_registry": {
                "path": str(self._paths.exec_registry_path),
                "count": int(reg_count),
            },
        }

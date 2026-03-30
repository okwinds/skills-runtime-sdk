from __future__ import annotations

import json
import logging
import contextlib
import os
import secrets
import socket
import stat
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from skills_runtime.runtime.paths import get_runtime_paths
from skills_runtime.runtime.exec_service import ExecSessionService
from skills_runtime.runtime.collab_service import CollabAgentService


logger = logging.getLogger(__name__)


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
        self._paths = get_runtime_paths(workspace_root=self._workspace_root)

        self._created_at_ms = int(time.time() * 1000)
        self._started_monotonic = time.monotonic()
        # 用于 orphan cleanup 的 "进程身份标记"。每次 server 启动都会生成新的 marker。
        # 该 marker 会注入到 exec session 子进程 env，并落盘到 registry；restart 后可用于验证是否"本框架产物"。
        self._exec_marker = secrets.token_hex(8)

        # 服务实例
        self._exec_service = ExecSessionService(
            workspace_root=self._workspace_root,
            paths=self._paths,
            exec_marker=self._exec_marker,
        )
        self._collab_service = CollabAgentService(
            wait_join_poll_sec=max(0.01, int(wait_join_poll_ms) / 1000.0),
        )

        self._shutdown = threading.Event()
        self._last_activity = time.monotonic()
        self._last_orphan_cleanup: Dict[str, Any] = {"ok": True, "killed": 0, "skipped": 0, "errors": []}

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
        判断当前是否存在"需要保持 server 存活"的运行资源。

        规则：
        - 任一 exec session 存在（in-memory 持有）视为 running；
        - 任一 child 状态为 running 视为 running。
        """

        if self._exec_service.has_running_sessions():
            return True
        if self._collab_service.has_running_children():
            return True
        return False

    def _handle_runtime_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：runtime.status。

        返回：
        - server pid/created_at_ms/uptime_ms
        - active_exec_sessions / active_children
        - exec_registry 摘要（便于审计/排障）
        """

        _ = params
        active_children = self._collab_service.get_active_children_count()

        exec_snap = self._exec_service.get_status_snapshot()

        return {
            "ok": True,
            "pid": int(os.getpid()),
            "created_at_ms": int(self._created_at_ms),
            "uptime_ms": int((time.monotonic() - self._started_monotonic) * 1000),
            "active_exec_sessions": exec_snap["active_exec_sessions"],
            "active_children": int(active_children),
            "exec_registry": {
                "path": exec_snap["exec_registry"]["path"],
                "count": exec_snap["exec_registry"]["count"],
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
            self._exec_service.close_all_and_clear_registry()

        cancelled_children = 0
        if close_children:
            cancelled_children = self._collab_service.cancel_all()

        return {"ok": True, "exec": bool(close_exec), "children": bool(close_children), "cancelled_children": int(cancelled_children)}

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
            return self._exec_service.handle_exec_spawn(
                params, resolve_path=self._resolve_under_workspace
            )
        if method == "exec.write":
            return self._exec_service.handle_exec_write(params)
        if method == "exec.close":
            return self._exec_service.handle_exec_close(params)
        if method == "exec.close_all":
            return self._exec_service.handle_exec_close_all(params)

        if method == "collab.spawn":
            return self._collab_service.handle_collab_spawn(params)
        if method == "collab.wait":
            return self._collab_service.handle_collab_wait(params)
        if method == "collab.send_input":
            return self._collab_service.handle_collab_send_input(params)
        if method == "collab.close":
            return self._collab_service.handle_collab_close(params)
        if method == "collab.resume":
            return self._collab_service.handle_collab_resume(params)

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
            from skills_runtime.runtime.process_reaper import ProcessReaper
            reaper = ProcessReaper(exec_registry_path=self._paths.exec_registry_path)
            self._last_orphan_cleanup = reaper.orphan_cleanup_on_startup(
                workspace_root=self._workspace_root
            )
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
                self._exec_service.close_all_and_clear_registry()
            with contextlib.suppress(Exception):
                self._collab_service.cancel_all()
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

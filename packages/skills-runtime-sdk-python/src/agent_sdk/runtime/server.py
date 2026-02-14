from __future__ import annotations

import json
import contextlib
import os
import secrets
import socket
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any, Dict, Optional

from agent_sdk.core.exec_sessions import ExecSessionManager, ExecSessionWriteResult
from agent_sdk.runtime.paths import get_runtime_paths


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


class RuntimeServer:
    """
    workspace 级 runtime server（Unix socket JSON RPC）。

    行为：
    - server 启动后写入 `.skills_runtime_sdk/runtime/server.json`（pid/secret/socket_path）；
    - 当无 running sessions/children 且 idle 超过阈值时自动退出（避免测试/脚本泄露后台进程）。
    """

    def __init__(self, *, workspace_root: Path, secret: str, idle_timeout_ms: int = 10_000) -> None:
        """
        创建 workspace 级 runtime server。

        参数：
        - workspace_root：工作区根目录（用于写入 server.json 与日志）
        - secret：本地鉴权 secret（客户端需携带；仅本机使用）
        - idle_timeout_ms：无运行资源时的空闲退出阈值（毫秒）
        """

        self._workspace_root = Path(workspace_root).resolve()
        self._secret = str(secret or "")
        self._idle_timeout_ms = int(idle_timeout_ms)
        self._paths = get_runtime_paths(workspace_root=self._workspace_root)

        self._exec = ExecSessionManager()
        self._children_lock = threading.Lock()
        self._children: Dict[str, _ChildState] = {}

        self._shutdown = threading.Event()
        self._last_activity = time.monotonic()

    def _write_server_info(self) -> None:
        """写入 `server.json`（pid/secret/socket_path/created_at_ms）。"""

        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        obj = {
            "pid": os.getpid(),
            "secret": self._secret,
            "socket_path": str(self._paths.socket_path),
            "created_at_ms": int(time.time() * 1000),
        }
        self._paths.server_info_path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

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

        if any(self._exec.has(sid) for sid in list(getattr(self._exec, "_sessions", {}).keys())):
            return True
        with self._children_lock:
            for c in self._children.values():
                if c.status == "running":
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
        env_map: Optional[Dict[str, str]] = None
        if env is not None:
            if not isinstance(env, dict):
                raise ValueError("env must be dict")
            env_map = {str(k): str(v) for k, v in env.items()}

        s = self._exec.spawn(argv=[str(x) for x in argv], cwd=Path(cwd), env=env_map, tty=tty)
        return {"session_id": int(s.session_id), "created_at_ms": int(s.created_at_ms)}

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
        wr: ExecSessionWriteResult = self._exec.write(
            session_id=session_id,
            chars=chars,
            yield_time_ms=yield_time_ms,
            max_output_bytes=max_output_bytes,
        )
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
        self._exec.close(session_id)
        return {"ok": True, "session_id": int(session_id)}

    def _handle_exec_close_all(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        RPC：exec.close_all。

        语义：
        - best-effort 关闭当前 server 持有的所有 sessions。
        """

        _ = params
        self._exec.close_all()
        return {"ok": True}

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
            while not child.cancel_event.is_set():
                try:
                    x = child.inbox.get(timeout=0.05)
                    return f"got:{x}"
                except Exception:
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
                        cur.status = "cancelled"
                        cur.final_output = None
                        return
                    cur.status = "completed"
                    cur.final_output = str(out)
            except Exception as e:
                with self._children_lock:
                    cur = self._children.get(cid)
                    if cur is None:
                        return
                    cur.status = "failed"
                    cur.error = str(e)

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
            raise KeyError("child not found")
        child.cancel_event.set()
        with self._children_lock:
            if cid in self._children:
                self._children[cid].status = "cancelled"
        return {"id": cid}

    def _handle_collab_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RPC：collab.resume（最小：no-op 查询）。"""

        cid = str(params.get("id") or "")
        if not cid:
            raise ValueError("id must be non-empty")
        with self._children_lock:
            child = self._children.get(cid)
        if child is None:
            raise KeyError("child not found")
        return {"id": child.id, "status": child.status}

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
            missing = [i for i in ids_s if i not in self._children]
            if missing:
                raise KeyError(f"unknown ids: {missing}")
            handles = [self._children[i] for i in ids_s]

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

        results = []
        with self._children_lock:
            for cid in ids_s:
                cur = self._children.get(cid)
                if cur is None:
                    continue
                item: Dict[str, Any] = {"id": cur.id, "status": cur.status}
                if cur.status == "completed" and cur.final_output is not None:
                    item["final_output"] = cur.final_output
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

    def serve_forever(self) -> None:
        """
        监听 Unix socket 并处理请求，直到 shutdown 或 idle auto-exit。
        """

        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
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
                    continue

                with conn:
                    try:
                        raw = b""
                        while True:
                            b = conn.recv(65536)
                            if not b:
                                break
                            raw += b
                        req = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
                        if not isinstance(req, dict):
                            raise ValueError("invalid request")
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
                        resp = {"ok": False, "error": str(e)}
                    conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
        finally:
            with contextlib.suppress(Exception):
                s.close()
            self._cleanup_files()


def main() -> int:
    """
    模块入口：从环境变量读取 workspace_root/secret 并启动 server。

    环境变量：
    - `AGENT_SDK_RUNTIME_WORKSPACE_ROOT`
    - `AGENT_SDK_RUNTIME_SECRET`
    """

    secret = str(os.environ.get("AGENT_SDK_RUNTIME_SECRET") or "").strip()
    ws = str(os.environ.get("AGENT_SDK_RUNTIME_WORKSPACE_ROOT") or "").strip()
    if not secret:
        secret = secrets.token_urlsafe(24)
    if not ws:
        ws = str(Path.cwd().resolve())

    server = RuntimeServer(workspace_root=Path(ws), secret=secret)
    server.serve_forever()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

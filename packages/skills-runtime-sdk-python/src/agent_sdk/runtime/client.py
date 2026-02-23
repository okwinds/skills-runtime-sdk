from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from agent_sdk.runtime.paths import get_runtime_paths


@dataclass(frozen=True)
class RuntimeServerInfo:
    """runtime server 发现信息（从 server.json 读取）。"""

    pid: int
    secret: str
    socket_path: str
    created_at_ms: int


def _pid_alive(pid: int) -> bool:
    """
    判断 pid 是否存活（best-effort）。

    参数：
    - pid：进程号
    """

    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


class RuntimeClient:
    """
    本地 runtime client（Unix socket JSON RPC）。

    说明：
    - client 会在首次请求时确保 server 已启动；
    - server 为“workspace 级单例”，位于 `.skills_runtime_sdk/runtime/`。
    """

    def __init__(self, *, workspace_root: Path, start_timeout_ms: int = 2000) -> None:
        """
        创建 runtime client。

        参数：
        - workspace_root：工作区根目录（用于定位 `.skills_runtime_sdk/runtime/`）
        - start_timeout_ms：启动 server 的最长等待时间
        """

        self._workspace_root = Path(workspace_root).resolve()
        self._start_timeout_ms = int(start_timeout_ms)
        self._paths = get_runtime_paths(workspace_root=self._workspace_root)

    def _read_server_info(self) -> Optional[RuntimeServerInfo]:
        """
        读取 server.json 并解析为 RuntimeServerInfo。

        返回：
        - RuntimeServerInfo：成功时返回
        - None：文件不存在/解析失败
        """

        p = self._paths.server_info_path
        if not p.exists():
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(obj, dict):
            return None
        try:
            return RuntimeServerInfo(
                pid=int(obj.get("pid")),
                secret=str(obj.get("secret") or ""),
                socket_path=str(obj.get("socket_path") or ""),
                created_at_ms=int(obj.get("created_at_ms") or 0),
            )
        except Exception:
            return None

    def _cleanup_stale_server_files(self) -> None:
        """
        清理旧 runtime 文件（best-effort）。

        说明：
        - 用于处理 server 异常退出留下的残余 socket / server.json；
        - 仅清理本 workspace 对应的 paths。
        """

        # best-effort：清理旧 socket / server.json，避免阻断启动
        with contextlib.suppress(Exception):
            if self._paths.socket_path.exists():
                self._paths.socket_path.unlink()
        with contextlib.suppress(Exception):
            if self._paths.server_info_path.exists():
                self._paths.server_info_path.unlink()

    def ensure_server(self) -> RuntimeServerInfo:
        """
        确保 workspace 级 runtime server 已启动，并返回其发现信息。

        语义：
        - 若 server.json 存在且 pid/socket 都有效：直接复用
        - 否则：清理残余文件并启动新 server，然后等待其写出 server.json
        """

        info = self._read_server_info()
        if info is not None and info.secret and info.socket_path:
            if _pid_alive(info.pid) and Path(info.socket_path).exists():
                # 仅用 pid + socket file 存在并不足够：
                # - crash 后可能出现 zombie 或残留 socket 文件；
                # - 进程假存活会导致 client 误以为 server 可用，从而在后续 call 中表现为 hang/connection errors。
                # 因此这里做一次轻量 ping 探测，确认 server 可响应。
                try:
                    _ = self._call_with_info(info, method="ping", params={}, timeout_sec=0.5)
                    return info
                except Exception:
                    pass

        # stale：清理后重启
        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_server_files()

        secret = secrets.token_urlsafe(24)
        env = dict(os.environ)
        env["AGENT_SDK_RUNTIME_SECRET"] = secret
        env["AGENT_SDK_RUNTIME_WORKSPACE_ROOT"] = str(self._workspace_root)

        # 测试/嵌入式调用场景下，当前进程可能通过 `sys.path`（pytest pythonpath）加载 SDK，
        # 但环境变量里没有 `PYTHONPATH`。此时后台 server 进程若 cwd 改变会 import 失败。
        if not str(env.get("PYTHONPATH") or "").strip():
            try:
                import agent_sdk as _agent_sdk  # local import to avoid circular

                base = Path(_agent_sdk.__file__).resolve().parent.parent
                env["PYTHONPATH"] = str(base)
            except Exception:
                pass

        # 若调用方使用相对 PYTHONPATH（常见于本仓库开发态），则 server 进程的 cwd 不同会导致 import 失败。
        # 这里将其归一化为绝对路径，避免 `ModuleNotFoundError: agent_sdk.runtime`。
        py_path = str(env.get("PYTHONPATH") or "")
        if py_path.strip():
            parts = []
            base = Path.cwd().resolve()
            for raw in py_path.split(os.pathsep):
                if not raw:
                    continue
                p = Path(raw)
                if not p.is_absolute():
                    p = (base / p).resolve()
                parts.append(str(p))
            if parts:
                env["PYTHONPATH"] = os.pathsep.join(parts)

        # 后台启动 server（Unix socket 监听后会写 server.json）
        # 为了可观测性（避免 “start timeout 但无日志”），把 stdout/stderr 追加写到 runtime 目录。
        stdout_log = (self._paths.runtime_dir / "server.stdout.log").resolve()
        stderr_log = (self._paths.runtime_dir / "server.stderr.log").resolve()
        with open(stdout_log, "ab") as out_f, open(stderr_log, "ab") as err_f:
            subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "agent_sdk.runtime.server"],
                cwd=str(self._workspace_root),
                env=env,
                stdout=out_f,
                stderr=err_f,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )

        deadline = time.monotonic() + self._start_timeout_ms / 1000.0
        while time.monotonic() < deadline:
            info2 = self._read_server_info()
            if info2 is not None and info2.secret and info2.socket_path:
                if _pid_alive(info2.pid) and Path(info2.socket_path).exists():
                    return info2
            time.sleep(0.05)

        # 超时：把 stderr 尾部带上，便于定位（避免输出 secrets）
        tail = ""
        try:
            if stderr_log.exists():
                b = stderr_log.read_bytes()
                tail = b[-2000:].decode("utf-8", errors="replace")
        except Exception:
            tail = ""
        msg = "runtime server start timeout"
        if tail.strip():
            msg += f"; server.stderr.tail={tail.strip()!r}"
        raise RuntimeError(msg)

    def _call_with_info(
        self,
        info: RuntimeServerInfo,
        *,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        timeout_sec: float = 5.0,
    ) -> Dict[str, Any]:
        """
        使用已知 server info 发起一次 RPC（不会触发 ensure_server，避免递归）。

        参数：
        - info：RuntimeServerInfo（pid/secret/socket_path）
        - method：方法名
        - params：参数对象（可选）
        - timeout_sec：socket 超时秒数
        """

        sock_path = Path(info.socket_path)
        req = {"method": str(method), "params": params or {}, "secret": info.secret}
        data = json.dumps(req, ensure_ascii=False).encode("utf-8")

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(float(timeout_sec))
            s.connect(str(sock_path))
            s.sendall(data)
            s.shutdown(socket.SHUT_WR)

            chunks: list[bytes] = []
            while True:
                b = s.recv(65536)
                if not b:
                    break
                chunks.append(b)
        raw = b"".join(chunks).decode("utf-8", errors="replace")
        obj = json.loads(raw) if raw.strip() else {}
        if not isinstance(obj, dict):
            raise RuntimeError("invalid runtime response")
        if obj.get("ok") is not True:
            kind = str(obj.get("error_kind") or "").strip() or None
            msg = str(obj.get("error") or "runtime call failed")
            if kind:
                raise RuntimeError(f"{kind}: {msg}")
            raise RuntimeError(msg)
        data_obj = obj.get("data")
        return data_obj if isinstance(data_obj, dict) else {"data": data_obj}

    def call(self, *, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        发起一次 runtime JSON RPC 调用。

        参数：
        - method：方法名（例如 `exec.spawn` / `exec.write` / `collab.wait`）
        - params：参数对象（dict；可空）

        返回：
        - data 对象（dict）；当 server 返回 ok=false 时抛 RuntimeError
        """

        info = self.ensure_server()
        return self._call_with_info(info, method=method, params=params, timeout_sec=5.0)


import contextlib  # placed at end to keep imports minimal

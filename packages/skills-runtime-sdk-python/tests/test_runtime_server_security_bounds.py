from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

import pytest

from skills_runtime.runtime.client import RuntimeClient
from skills_runtime.runtime.paths import get_runtime_paths


def _wait_for_socket_close(sock: socket.socket, *, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    sock.settimeout(min(0.2, timeout_sec))
    while time.monotonic() < deadline:
        try:
            b = sock.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return True
        if not b:
            return True
    return False


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_runtime_server_rejects_oversized_rpc_request_with_validation_error_kind(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.6）：
    runtime server 必须对 RPC 请求体做大小上限控制，超限返回稳定 validation 错误，且 server 不崩溃。
    """

    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("no AF_UNIX support")

    client = RuntimeClient(workspace_root=tmp_path)
    info = client.ensure_server()

    pad = "x" * (1024 * 1024 + 64)
    req = {"method": "runtime.status", "params": {}, "secret": info.secret, "pad": pad}
    data = json.dumps(req, ensure_ascii=False).encode("utf-8")

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        s.connect(str(info.socket_path))
        s.sendall(data)
        s.shutdown(socket.SHUT_WR)

        chunks: list[bytes] = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)

    obj = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
    assert isinstance(obj, dict)
    assert obj.get("ok") is False
    assert obj.get("error_kind") == "validation"

    # no crash：后续正常请求仍可工作
    st = client.call(method="runtime.status")
    assert st.get("ok") is True
    client.call(method="shutdown")


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_runtime_server_writes_server_json_with_restrictive_permissions_best_effort(tmp_path: Path) -> None:
    """
    回归（harden-safety-redaction-and-runtime-bounds / 1.7）：
    server.json（含本地 secret）必须尽力以 restrictive 权限落盘（POSIX 0600）。
    """

    old_umask = os.umask(0o022)
    try:
        client = RuntimeClient(workspace_root=tmp_path)
        _ = client.ensure_server()
    finally:
        os.umask(old_umask)

    paths = get_runtime_paths(workspace_root=tmp_path)
    server_json = paths.server_info_path
    assert server_json.exists() is True

    mode = int(server_json.stat().st_mode) & 0o777
    assert (mode & 0o077) == 0, f"expected no group/other perms, got mode={oct(mode)}"
    client.call(method="shutdown")


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_runtime_exec_spawn_rejects_cwd_outside_workspace_with_permission_error(tmp_path: Path) -> None:
    """
    回归：runtime exec.spawn 必须继承 workspace_root 边界，不得允许在 workspace 外启动会话。
    """

    client = RuntimeClient(workspace_root=tmp_path)
    _ = client.ensure_server()

    outside = tmp_path.parent.resolve()
    with pytest.raises(RuntimeError, match="permission|workspace_root|禁止访问"):
        client.call(
            method="exec.spawn",
            params={
                "argv": ["python3", "-c", "print('x')"],
                "cwd": str(outside),
                "tty": False,
            },
        )

    client.call(method="shutdown")


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_runtime_server_half_open_client_does_not_block_later_status_request(tmp_path: Path) -> None:
    """
    回归（runtime-liveness-hardening / 1.1）：
    一个已连接但未结束上传的 client，不得阻塞后续 `runtime.status`。
    """

    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("no AF_UNIX support")

    client = RuntimeClient(workspace_root=tmp_path)
    info = client.ensure_server()

    blocker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    blocker.settimeout(1.0)
    req = {
        "method": "runtime.status",
        "params": {},
        "secret": info.secret,
    }
    blocker.connect(str(info.socket_path))
    blocker.sendall(json.dumps(req, ensure_ascii=False).encode("utf-8"))

    try:
        started = time.monotonic()
        st = client._call_with_info(info, method="runtime.status", params={}, timeout_sec=0.75)
        elapsed = time.monotonic() - started

        assert int(st.get("pid") or 0) > 0
        assert elapsed < 0.75
        assert _wait_for_socket_close(blocker, timeout_sec=2.0) is True
    finally:
        try:
            blocker.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        try:
            while blocker.recv(65536):
                pass
        except OSError:
            pass
        blocker.close()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                client._call_with_info(info, method="shutdown", params={}, timeout_sec=0.25)
                break
            except Exception:
                time.sleep(0.05)

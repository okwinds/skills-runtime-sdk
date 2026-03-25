from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skills_runtime.core.exec_sessions import PersistentExecSessionManager
from skills_runtime.runtime.client import RuntimeClient
from skills_runtime.runtime.paths import get_runtime_paths
from skills_runtime.runtime.server import RuntimeServer


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_runtime_status_reports_health_and_counts(tmp_path: Path) -> None:
    """
    BL-020（可观测性）：runtime server 必须提供 status，包含健康状态与关键计数。

    断言：
    - status 返回 server pid/created_at_ms；
    - active_exec_sessions / active_children 随 spawn/close 变化可观测。
    """

    client = RuntimeClient(workspace_root=tmp_path)
    _ = client.ensure_server()

    st0 = client.call(method="runtime.status")
    assert st0.get("ok") is True
    assert int(st0.get("pid") or 0) > 0
    assert int(st0.get("created_at_ms") or 0) > 0
    assert int(st0.get("active_exec_sessions") or 0) == 0
    assert int(st0.get("active_children") or 0) == 0

    mgr = PersistentExecSessionManager(workspace_root=tmp_path)
    s = mgr.spawn(argv=[sys.executable, "-u", "-c", "import time; time.sleep(5)"], cwd=tmp_path)

    st1 = client.call(method="runtime.status")
    assert int(st1.get("active_exec_sessions") or 0) >= 1

    mgr.close(s.session_id)
    st2 = client.call(method="runtime.status")
    assert int(st2.get("active_exec_sessions") or 0) == 0

    client.call(method="shutdown")


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_runtime_cleanup_closes_sessions_and_children(tmp_path: Path) -> None:
    """
    BL-020（显式 stop/cleanup）：runtime 必须支持显式 cleanup，并能在 status 中观测结果。
    """

    client = RuntimeClient(workspace_root=tmp_path)
    _ = client.ensure_server()

    # 让 child 进入 running（等待输入），便于验证 cleanup 会取消它。
    child = client.call(method="collab.spawn", params={"message": "wait_input:1", "agent_type": "default"})
    cid = str(child.get("id") or "")
    assert cid

    mgr = PersistentExecSessionManager(workspace_root=tmp_path)
    s = mgr.spawn(argv=[sys.executable, "-u", "-c", "import time; time.sleep(60)"], cwd=tmp_path)

    st1 = client.call(method="runtime.status")
    assert int(st1.get("active_exec_sessions") or 0) >= 1
    assert int(st1.get("active_children") or 0) >= 1

    out = client.call(method="runtime.cleanup", params={"exec": True, "children": True})
    assert out.get("ok") is True

    # cleanup 后计数应归零（允许极短延迟，避免线程 join 竞态）
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        st2 = client.call(method="runtime.status")
        if int(st2.get("active_exec_sessions") or 0) == 0 and int(st2.get("active_children") or 0) == 0:
            break
        time.sleep(0.05)
    st2 = client.call(method="runtime.status")
    assert int(st2.get("active_exec_sessions") or 0) == 0
    assert int(st2.get("active_children") or 0) == 0

    # 旧 id 操作应给出稳定 not-found（不得阻塞）
    with pytest.raises(KeyError):
        _ = mgr.write(session_id=s.session_id, yield_time_ms=0)
    with pytest.raises(RuntimeError):
        _ = client.call(method="collab.wait", params={"ids": [cid], "timeout_ms": 0})

    client.call(method="shutdown")


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_runtime_restart_runs_orphan_cleanup_and_old_session_not_found(tmp_path: Path) -> None:
    """
    BL-020（crash/restart + orphan cleanup）：
    - server crash 后 restart 必须执行 orphan cleanup，避免残留孤儿进程；
    - restart 后对旧 session_id 的操作必须返回稳定 not-found（不得阻塞）。
    """

    paths = get_runtime_paths(workspace_root=tmp_path)
    client = RuntimeClient(workspace_root=tmp_path)
    info = client.ensure_server()

    mgr = PersistentExecSessionManager(workspace_root=tmp_path)
    s = mgr.spawn(
        argv=[
            sys.executable,
            "-u",
            "-c",
            "import signal, time; signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)",
        ],
        cwd=tmp_path,
    )

    # registry 必须落盘（用于 restart 后 orphan cleanup）
    reg_obj = json.loads(paths.exec_registry_path.read_text(encoding="utf-8"))
    sessions = reg_obj.get("exec_sessions") or {}
    assert str(s.session_id) in sessions
    pid = int((sessions[str(s.session_id)] or {}).get("pid") or 0)
    assert pid > 0

    # 模拟 crash：强杀 server（不走 shutdown / close_all）
    os.kill(int(info.pid), signal.SIGKILL)

    # 进程仍存活（orphan）：由 restart 的 cleanup 负责清理
    assert _pid_alive(pid) is True

    # restart：新的 server 启动后，应清理 orphan
    info2 = client.ensure_server()
    assert int(info2.pid) != int(info.pid)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.05)
    assert _pid_alive(pid) is False

    with pytest.raises(KeyError):
        _ = mgr.write(session_id=s.session_id, yield_time_ms=0)

    client.call(method="shutdown")


@pytest.mark.skipif(os.name == "nt", reason="no Windows support in this SDK")
def test_collab_wait_keeps_send_input_interactive_and_then_returns(tmp_path: Path) -> None:
    """
    回归（runtime-liveness-hardening / 2.1, 2.3）：
    一个 client 正在 `collab.wait` 时，另一个 client 的 `collab.send_input`
    仍应可完成，且 wait 之后应返回 child 的完成结果。
    """

    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("no AF_UNIX support")

    client = RuntimeClient(workspace_root=tmp_path)
    info = client.ensure_server()

    child = client.call(method="collab.spawn", params={"message": "wait_input:1", "agent_type": "default"})
    cid = str(child.get("id") or "")
    assert cid

    wait_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    wait_sock.settimeout(3.0)
    wait_req = {
        "method": "collab.wait",
        "params": {"ids": [cid]},
        "secret": info.secret,
    }
    wait_sock.connect(str(info.socket_path))
    wait_sock.sendall(json.dumps(wait_req, ensure_ascii=False).encode("utf-8"))
    wait_sock.shutdown(socket.SHUT_WR)

    try:
        # 给 wait 请求一个极短窗口先进入 server，稳定复现当前串行处理的饥饿点。
        time.sleep(0.1)

        started = time.monotonic()
        send_out = client._call_with_info(
            info,
            method="collab.send_input",
            params={"id": cid, "message": "hello"},
            timeout_sec=0.75,
        )
        elapsed = time.monotonic() - started

        assert send_out.get("id") == cid
        assert elapsed < 0.75

        chunks: list[bytes] = []
        while True:
            b = wait_sock.recv(65536)
            if not b:
                break
            chunks.append(b)
        obj = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
        assert obj.get("ok") is True

        data = obj.get("data") or {}
        results = data.get("results") or []
        assert len(results) == 1
        assert results[0].get("id") == cid
        assert results[0].get("status") == "completed"
        assert results[0].get("final_output") == "got:hello"
    finally:
        wait_sock.close()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                client._call_with_info(info, method="shutdown", params={}, timeout_sec=0.25)
                break
            except Exception:
                time.sleep(0.05)
        else:
            with contextlib.suppress(Exception):
                os.kill(int(info.pid), signal.SIGKILL)


def test_orphan_cleanup_does_not_kill_when_marker_verification_fails_even_if_argv0_matches(tmp_path: Path, monkeypatch) -> None:
    server = RuntimeServer(workspace_root=tmp_path, secret="test-secret")
    server._write_exec_registry(
        {
            "schema": 1,
            "workspace_root": str(tmp_path),
            "exec_sessions": {
                "1": {
                    "pid": 12345,
                    "pgid": 12345,
                    "created_at_ms": 1,
                    "argv": [sys.executable, "-u", "-c", "print('x')"],
                    "cwd": str(tmp_path),
                    "marker": "missing-marker",
                }
            },
        }
    )

    kill_calls: list[int] = []

    monkeypatch.setattr(server, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(server, "_ps_env_contains_marker", lambda pid, marker: False)
    monkeypatch.setattr(server, "_kill_process_group", lambda pid: kill_calls.append(pid) or True)

    class _FakeCompletedProcess:
        def __init__(self) -> None:
            self.stdout = f"{sys.executable} -u -c print('x')"
            self.stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _FakeCompletedProcess())

    server._orphan_cleanup_on_startup()

    assert kill_calls == []
    reg = server._read_exec_registry()
    item = (reg.get("exec_sessions") or {}).get("1") or {}
    assert item.get("needs_manual_cleanup") is True
    assert server._last_orphan_cleanup.get("skipped") == 1


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

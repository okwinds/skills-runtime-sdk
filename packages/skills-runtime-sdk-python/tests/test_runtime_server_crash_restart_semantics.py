from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from agent_sdk.core.exec_sessions import PersistentExecSessionManager
from agent_sdk.runtime.client import RuntimeClient
from agent_sdk.runtime.paths import get_runtime_paths


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
